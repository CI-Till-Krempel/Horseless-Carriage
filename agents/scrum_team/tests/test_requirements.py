# agents/scrum_team/tests/test_requirements.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.requirements import advance_story_stage, upsert_backlog_item


def _base_story(stages_completed):
    return {
        "id": "US-0001",
        "title": "Add real feature",
        "user_story": "As a user, I want X, so that Y.",
        "acceptance_criteria": ["Given X, when Y, then Z"],
        "stages_completed": list(stages_completed),
    }


def _tool_context(agent_name, stages_completed):
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    story = _base_story(stages_completed)
    tc.state["product_backlog"] = [story]
    tc.state["sprint_backlog"] = [dict(story)]
    tc.agent_name = agent_name
    return tc


@patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestAdvanceStoryStageGates(unittest.TestCase):
    """
    Acceptance Criteria (ISSUE-0001, ISSUE-0002, ISSUE-0003, ISSUE-0005,
    ISSUE-0010): advance_story_stage's Implemented/Reviewed/Tested
    transitions must mechanically verify the precondition they claim,
    instead of accepting the call on ordering/ownership grounds alone.
    """

    def test_implemented_requires_fresh_sprint_approval(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("human approval", result["message"])

    def test_implemented_rejection_records_blocking_interaction(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria (GH issue #53): a story rejected for lack of a
        fresh human approval is exactly the "absolutely necessary human
        feedback" case that must be recorded (and notified on), not just
        left as a tool error return the calling agent might not relay.
        """
        tc = _tool_context("DevTeam", ["Ready"])
        advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(len(tc.state["blocking_interactions"]), 1)
        interaction = tc.state["blocking_interactions"][0]
        self.assertEqual(interaction["kind"], "approval")
        self.assertIn("US-0001", interaction["summary"])
        self.assertFalse(interaction["resolved"])

    def test_implemented_blocked_while_previous_release_pending(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_report_pending_release"] = True
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("create_release_pr", result["message"])

    def test_implemented_requires_real_source_file_written(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        # Only a story markdown file touched - not real source code.
        tc.state["sprint_files_touched"] = ["specs/stories/US-0001-Add-real-feature.md"]
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("source file", result["message"])

    def test_implemented_requires_actual_tokens_logged(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("log_story_tokens", result["message"])

    def test_implemented_succeeds_once_every_precondition_is_met(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        tc.state["story_estimates"] = {"US-0001": {"estimate": 100, "actual": 90}}
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tc.state["dev_touch_baseline"], 1)

    def test_implemented_spike_story_bypasses_file_write_gate(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["product_backlog"][0]["spike"] = True
        tc.state["sprint_backlog"][0]["spike"] = True
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["story_estimates"] = {"US-0001": {"estimate": 10, "actual": 5}}
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_implemented_requires_budget_approval_at_ceo_level(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria (interaction levels, see docs/INTERACTION-LEVELS.md): at the CEO
        level, a "sprint" approval is not enough - a fresh "budget" approval is required instead.
        """
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            tc = _tool_context("DevTeam", ["Ready"])
            tc.state["sprint_files_touched"] = ["app/main.py"]
            tc.state["story_estimates"] = {"US-0001": {"estimate": 100, "actual": 90}}

            tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
            result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
            self.assertEqual(result["status"], "error")
            self.assertIn("budget", result["message"])

            tc.state["human_approvals"] = [{"type": "budget", "note": "ok"}]
            result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
            self.assertEqual(result["status"], "ok")

    def test_implemented_requires_no_approval_at_eval_level(self, mock_save, mock_md, mock_roadmap):
        """Acceptance Criteria: EVAL level requires no human approval at all."""
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            tc = _tool_context("DevTeam", ["Ready"])
            tc.state["sprint_files_touched"] = ["app/main.py"]
            tc.state["story_estimates"] = {"US-0001": {"estimate": 100, "actual": 90}}
            result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
            self.assertEqual(result["status"], "ok")

    def test_reviewed_requires_architect_review_call(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        result = advance_story_stage("US-0001", "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("gh_pr_review", result["message"])

        tc.state["pr_review_calls"] = {"Architect": 1}
        result = advance_story_stage("US-0001", "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tc.state["architect_review_baseline"], 1)

    def test_tested_requires_qa_review_call_and_passing_build(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("gh_pr_review", result["message"])

        tc.state["pr_review_calls"] = {"QA": 1}
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("check_build", result["message"])

        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_tested_blocked_when_last_check_build_failed(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": False}
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("check_build", result["message"])


class TestUpsertBacklogItemGuards(unittest.TestCase):
    """Acceptance Criteria (ISSUE-0007, ISSUE-0008)."""

    @patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_setting_version_directly_triggers_roadmap_sync(self, mock_save, mock_md, mock_sync):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_backlog_item({"id": "US-0001", "title": "Foo", "version": "v0.2"}, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        mock_sync.assert_called_once()

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_near_duplicate_title_produces_warning(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        upsert_backlog_item({"id": "US-0001", "title": "Add login flow"}, tool_context=tc)
        result = upsert_backlog_item({"id": "US-0002", "title": "add  Login-flow!"}, tool_context=tc)
        self.assertIsNotNone(result["duplicate_warning"])
        self.assertIn("US-0001", result["duplicate_warning"])

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_distinct_titles_produce_no_warning(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        upsert_backlog_item({"id": "US-0001", "title": "Add login flow"}, tool_context=tc)
        result = upsert_backlog_item({"id": "US-0002", "title": "Export data as CSV"}, tool_context=tc)
        self.assertIsNone(result["duplicate_warning"])


if __name__ == "__main__":
    unittest.main()
