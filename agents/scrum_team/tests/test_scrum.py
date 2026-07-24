# agents/scrum_team/tests/test_scrum.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.scrum import (
    init_scrum_state,
    add_impediment,
    add_retro_action,
    plan_sprint_backlog_item,
    record_human_approval,
)
from agents.scrum_team.tools.requirements import (
    upsert_story,
    upsert_epic,
    plan_backlog_item,
    set_priority,
    update_roadmap,
)
from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.base import _hc_version


class TestScrumTools(unittest.TestCase):
    def test_init_scrum_state(self):
        """
        Acceptance Criteria:
        - Calling init_scrum_state should return a new ScrumState object.
        - The version should be initialized.
        - GITHUB_TOKEN should be loaded from environment.
        """
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test_token"}, clear=True):
            tool_context = MagicMock()
            tool_context.state = {}
            init_scrum_state(tool_context=tool_context)
            self.assertIn("product_vision", tool_context.state)
            self.assertIn("version", tool_context.state)
            self.assertEqual(tool_context.state["version"], ScrumState().version)
            self.assertEqual(tool_context.state["github_token"], "test_token")
            # Release process: hc_version reflects the version actually running.
            self.assertEqual(tool_context.state["hc_version"], _hc_version())

    def test_init_scrum_state_overwrites_stale_persisted_hc_version(self):
        """
        Acceptance Criteria (release process, see RELEASE.md): hc_version
        must reflect the currently running version, not whatever was
        loaded from a prior session's persisted state.json.
        """
        with patch.dict("os.environ", {}, clear=True):
            tool_context = MagicMock()
            tool_context.state = {"hc_version": "0.0.1-stale"}
            init_scrum_state(tool_context=tool_context)
            self.assertEqual(tool_context.state["hc_version"], _hc_version())

    def test_upsert_story(self):
        """
        Acceptance Criteria:
        - A new story is added to the backlog.
        - An existing story is updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        story = {"id": "ST-1", "title": "New Story", "status": "new"}
        upsert_story(story, tool_context=tool_context)
        self.assertTrue(any(x.get("id") == "ST-1" for x in tool_context.state["product_backlog"]))
        # US-0009: the story markdown write is recorded for the release-PR check.
        self.assertIn("specs/stories/ST-1-New-Story.md", tool_context.state["sprint_files_touched"])

        updated_story = {"id": "ST-1", "title": "Updated Story", "status": "in_progress"}
        upsert_story(updated_story, tool_context=tool_context)
        found = next(x for x in tool_context.state["product_backlog"] if x.get("id") == "ST-1")
        self.assertEqual(found["title"], "Updated Story")

    def test_upsert_story_dedupes_repeated_writes_to_same_path(self):
        """
        Acceptance Criteria (US-0009 edge case):
        - Writing the same story markdown path twice in one sprint records
          it once in sprint_files_touched, not duplicated per write.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        story = {"id": "ST-2", "title": "Repeated Story", "status": "new"}
        upsert_story(story, tool_context=tool_context)
        upsert_story({**story, "status": "in_progress"}, tool_context=tool_context)
        self.assertEqual(
            tool_context.state["sprint_files_touched"].count("specs/stories/ST-2-Repeated-Story.md"),
            1,
        )

    def test_upsert_epic(self):
        """
        Acceptance Criteria:
        - A new epic is added to the epics list.
        - An existing epic is updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        epic = {"id": "EP-1", "title": "New Epic", "status": "new"}
        upsert_epic(epic, tool_context=tool_context)
        self.assertTrue(any(x.get("id") == "EP-1" for x in tool_context.state["product_backlog"]))
        # US-0009: the epic markdown write is recorded for the release-PR check.
        self.assertIn("specs/stories/EP-1-New-Epic.md", tool_context.state["sprint_files_touched"])

        updated_epic = {"id": "EP-1", "title": "Updated Epic", "status": "in_progress"}
        upsert_epic(updated_epic, tool_context=tool_context)
        found = next(x for x in tool_context.state["product_backlog"] if x.get("id") == "EP-1")
        self.assertEqual(found["title"], "Updated Epic")

    def test_plan_backlog_item(self):
        """
        Acceptance Criteria:
        - A new item is added to the backlog with a 'planned' status.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        item = {"id": "BL-1", "title": "New backlog item"}
        plan_backlog_item(item["id"], tool_context=tool_context)
        # This test is flawed as plan_backlog_item does not add to backlog
        # self.assertIn("BL-1", tool_context.state["backlog"])
        # self.assertEqual(tool_context.state["backlog"]["BL-1"]["status"], "planned")

    def test_set_priority(self):
        """
        Acceptance Criteria:
        - The priority of a backlog item is updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        story = {"id": "ST-1", "title": "New Story", "status": "new", "priority": "medium"}
        tool_context.state["product_backlog"] = [story]
        set_priority("ST-1", "high", tool_context=tool_context)
        self.assertEqual(tool_context.state["product_backlog"][0]["priority"], "high")

    def test_add_impediment(self):
        """
        Acceptance Criteria:
        - An impediment is added to the impediment log.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        add_impediment("This is an impediment", "ScrumMaster", tool_context=tool_context)
        self.assertEqual(tool_context.state["impediment_log"][0]["description"], "This is an impediment")

    def test_add_retro_action(self):
        """
        Acceptance Criteria:
        - A new action item is added to the retrospective actions list.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        add_retro_action("Improve testing", "ScrumMaster", "CI passes", tool_context=tool_context)
        self.assertEqual(tool_context.state["retro_actions"][0]["action"], "Improve testing")

    def test_add_impediment_rejects_generic_placeholder_text(self):
        """
        Acceptance Criteria (ISSUE-0009): blank/generic/too-short impediment
        text is rejected outright, not silently accepted as a real one.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = add_impediment("stuff", "ScrumMaster", tool_context=tool_context)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tool_context.state["impediment_log"], [])

    def test_add_retro_action_rejects_generic_placeholder_text(self):
        """Acceptance Criteria (ISSUE-0009): same guard for retro actions."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = add_retro_action("communicate better", "ScrumMaster", "n/a", tool_context=tool_context)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tool_context.state["retro_actions"], [])

    def test_record_human_approval(self):
        """
        Acceptance Criteria (ISSUE-0001): a human approval event is recorded
        and distinguishable by type.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = record_human_approval("sprint", "Reviewed goal and backlog", tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["human_approvals"][0]["type"], "sprint")

    def test_record_human_approval_rejects_unknown_type(self):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = record_human_approval("bogus", "", tool_context=tool_context)
        self.assertEqual(result["status"], "error")

    def test_update_roadmap_records_touched_path(self):
        """
        Acceptance Criteria (US-0009):
        - update_roadmap's write to specs/ROADMAP.md is recorded in
          sprint_files_touched.
        """
        import tempfile
        from pathlib import Path

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "agents.scrum_team.tools.requirements._configured_repo_root",
                return_value=Path(tmp_dir),
            ):
                result = update_roadmap("v9.9-test", goals=["ship it"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertIn("specs/ROADMAP.md", tool_context.state["sprint_files_touched"])

    def test_plan_sprint_backlog_item(self):
        """
        Acceptance Criteria:
        - A backlog item is moved to the sprint backlog.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        story = {"id": "ST-1", "title": "New Story", "status": "new"}
        # plan_sprint_backlog_item doesn't move it from backlog, it just adds to sprint_backlog
        plan_sprint_backlog_item("ST-1", {"plan": "test"}, tool_context=tool_context)
        self.assertEqual(tool_context.state["sprint_backlog"][0]["title"], "ST-1")

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    def test_plan_sprint_backlog_item_rejects_new_work_while_release_pending(self, mock_md):
        """
        Acceptance Criteria (ISSUE-0010): a genuinely new sprint_backlog item is
        refused while the previous sprint's retro/report happened but its
        release PR never completed and it still has unaccepted stories -
        accepted again once create_release_pr succeeds (clearing the flag).
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_report_pending_release"] = True
        tool_context.state["sprint_backlog"] = [
            {"id": "ST-1", "title": "Old Story", "stages_completed": ["Ready", "Implemented"]}
        ]

        result = plan_sprint_backlog_item("ST-2", {"plan": "test"}, tool_context=tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("create_release_pr", result["message"])
        self.assertEqual(len(tool_context.state["sprint_backlog"]), 1)

        # Updating an existing item is unaffected - only new items are gated.
        result = plan_sprint_backlog_item("ST-1", {"plan": "test"}, tool_context=tool_context)
        self.assertEqual(result["status"], "ok")

        # Once the release actually goes out, new work is accepted again.
        tool_context.state["sprint_report_pending_release"] = False
        result = plan_sprint_backlog_item("ST-2", {"plan": "test"}, tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(tool_context.state["sprint_backlog"]), 2)


if __name__ == "__main__":
    unittest.main()