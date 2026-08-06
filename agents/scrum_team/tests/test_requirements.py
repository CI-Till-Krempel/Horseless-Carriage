# agents/scrum_team/tests/test_requirements.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.requirements import (
    advance_story_stage, upsert_backlog_item, record_design_approval, plan_backlog_item, set_priority,
    upsert_story, upsert_epic, upsert_issue, deny_review, _update_story_markdown,
)


def _base_story(stages_completed):
    return {
        "id": "US-0001",
        "title": "Add real feature",
        "user_story": "As a user, I want X, so that Y.",
        "acceptance_criteria": ["Given X, when Y, then Z"],
        "stages_completed": list(stages_completed),
    }


def _tool_context(agent_name, stages_completed):
    """`stages_completed` gets "Draft" prepended automatically unless it's
    already there (GH issue #94: Draft is now a real, ordered STORY_STAGES
    entry before Ready) - existing callers here are testing the
    Implemented/Reviewed/Tested/Ready gates specifically, not Draft's own
    ordering/ownership (see TestDraftStage below for that), so they
    shouldn't all need updating just to keep passing Draft's prerequisite."""
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    stages = list(stages_completed)
    if "Draft" not in stages:
        stages = ["Draft"] + stages
    story = _base_story(stages)
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
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 5, "tests_failed": 0},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_tested_blocked_when_last_check_build_failed(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": False}
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("check_build", result["message"])

    def test_tested_blocked_when_no_tests_actually_ran(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria (GH issue #114): check_build() only verifies the
        build/dependency install, not that any tests actually ran - a story
        with a completely empty test suite must not be markable as Tested
        just because the build itself was clean and QA left a PR comment.
        """
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 0, "tests_failed": 0, "note": "no tests collected"},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("no tests", result["message"].lower())

    def test_tested_blocked_when_test_suite_could_not_run_at_all(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": False, "tests_run": 0, "tests_failed": 0, "note": "pytest could not be executed"},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_tested_blocked_when_tests_failed(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 10, "tests_failed": 2},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("2 of 10 tests failed", result["message"])

    def test_tested_succeeds_when_tests_actually_pass(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 12, "tests_failed": 0},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")


@patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestOneStoryAtATimeOrdering(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #106): stories are worked one at a time,
    top to bottom, in product_backlog priority order - the immediately
    preceding story must already be Accepted. _preceding_story must
    distinguish "not in product_backlog at all" from "genuinely first in
    product_backlog" - conflating the two (both previously returned None)
    let a sprint_backlog-only story (no matching product_backlog entry)
    advance through every stage with no ordering check at all.
    """

    def _two_story_context(self, agent_name, second_story_stages, second_in_product_backlog=True):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        first = _base_story(["Draft", "Ready"])
        first["id"] = "US-0001"
        second = _base_story(["Draft"] + list(second_story_stages))
        second["id"] = "US-0002"
        second["title"] = "Second story"

        product_backlog = [first]
        if second_in_product_backlog:
            product_backlog.append(second)
        tc.state["product_backlog"] = product_backlog
        tc.state["sprint_backlog"] = [dict(first), dict(second)]
        tc.agent_name = agent_name
        return tc

    def test_preceding_product_backlog_story_not_accepted_blocks_advancement(self, mock_save, mock_md, mock_roadmap):
        tc = self._two_story_context("ProductOwner", ["Ready"])
        result = advance_story_stage("US-0002", "Ready", tool_context=tc)
        self.assertEqual(result["status"], "ok")  # Ready re-affirm is a no-op success; real check is below
        # US-0001 (first, not yet Accepted) must block US-0002 from a stage that actually requires ordering.
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        tc.state["story_estimates"] = {"US-0002": {"estimate": 10, "actual": 5}}
        tc.agent_name = "DevTeam"
        result = advance_story_stage("US-0002", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("US-0001", result["message"])
        self.assertIn("must reach Accepted first", result["message"])

    def test_first_story_in_product_backlog_has_no_predecessor(self, mock_save, mock_md, mock_roadmap):
        tc = self._two_story_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        tc.state["story_estimates"] = {"US-0001": {"estimate": 10, "actual": 5}}
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_sprint_only_story_without_product_backlog_entry_is_refused(self, mock_save, mock_md, mock_roadmap):
        """
        The actual bug: US-0002 exists only in sprint_backlog (no matching
        product_backlog entry) - previously _preceding_story returned None
        for it (same as "genuinely first"), so it could advance freely
        regardless of US-0001 (still incomplete) sitting ahead of it in
        intent. It must now be refused instead, since ordering can't be
        verified for a story that was never actually planned into the
        product backlog.
        """
        tc = self._two_story_context("ProductOwner", [], second_in_product_backlog=False)
        result = advance_story_stage("US-0002", "Ready", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("US-0002", result["message"])
        self.assertIn("product_backlog", result["message"])


@patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestDraftStage(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #94): Draft is a real, ordered STORY_STAGES
    entry - the first stage, owned by Product Owner - not just the inert
    default label a freshly-created story's status happened to show before.
    """

    def test_ready_rejected_without_draft_first(self, mock_save, mock_md, mock_roadmap):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        story = _base_story([])  # no Draft yet
        tc.state["product_backlog"] = [story]
        tc.state["sprint_backlog"] = [dict(story)]
        tc.agent_name = "ProductOwner"

        result = advance_story_stage("US-0001", "Ready", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("Draft", result["message"])

    def test_draft_can_only_be_completed_by_product_owner(self, mock_save, mock_md, mock_roadmap):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        story = _base_story([])
        tc.state["product_backlog"] = [story]
        tc.state["sprint_backlog"] = [dict(story)]
        tc.agent_name = "DevTeam"

        result = advance_story_stage("US-0001", "Draft", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("ProductOwner", result["message"])

    def test_product_owner_completes_draft_then_ready(self, mock_save, mock_md, mock_roadmap):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        story = _base_story([])
        tc.state["product_backlog"] = [story]
        tc.state["sprint_backlog"] = [dict(story)]
        tc.agent_name = "ProductOwner"

        result = advance_story_stage("US-0001", "Draft", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stages_completed"], ["Draft"])

        result = advance_story_stage("US-0001", "Ready", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stages_completed"], ["Draft", "Ready"])


@patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestReadyDesignApprovalGate(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #94): "the designs are cleared by
    stakeholder review, then they are ready" - at the Stakeholder
    interaction level, Ready requires record_design_approval to have been
    called for that specific story. Not required at Product/CEO/EVAL.
    """

    def test_ready_blocked_at_stakeholder_level_without_design_approval(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Stakeholder"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            result = advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(result["status"], "error")
            self.assertIn("record_design_approval", result["message"])

    def test_ready_rejection_records_blocking_interaction(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Stakeholder"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(len(tc.state["blocking_interactions"]), 1)
            self.assertEqual(tc.state["blocking_interactions"][0]["kind"], "approval")

    def test_ready_succeeds_at_stakeholder_level_once_design_approved(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Stakeholder"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            record_design_approval("US-0001", "Looks good", tool_context=tc)
            result = advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(result["status"], "ok")

    def test_ready_requires_no_design_approval_at_product_level(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            result = advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(result["status"], "ok")

    def test_ready_requires_no_design_approval_at_ceo_level(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            result = advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(result["status"], "ok")

    def test_ready_requires_no_design_approval_at_eval_level(self, mock_save, mock_md, mock_roadmap):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            tc = _tool_context("ProductOwner", [])
            result = advance_story_stage("US-0001", "Ready", tool_context=tc)
            self.assertEqual(result["status"], "ok")


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestRecordDesignApproval(unittest.TestCase):
    """Acceptance Criteria (GH issue #94): record_design_approval sets a
    per-story flag (not a shared sprint-wide approval) on both backlog
    copies, so the Ready gate above can check it per story."""

    def test_sets_flag_on_both_backlog_copies(self, mock_save):
        tc = _tool_context("ProductOwner", [])
        result = record_design_approval("US-0001", "Reviewed with stakeholder", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(tc.state["product_backlog"][0]["design_approved"])
        self.assertTrue(tc.state["sprint_backlog"][0]["design_approved"])
        self.assertEqual(tc.state["product_backlog"][0]["design_approval_note"], "Reviewed with stakeholder")

    def test_unknown_story_errors(self, mock_save):
        tc = _tool_context("ProductOwner", [])
        result = record_design_approval("US-9999", "note", tool_context=tc)
        self.assertEqual(result["status"], "error")


@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestDenyReview(unittest.TestCase):
    """
    Acceptance Criteria: a review (Architect's code review, QA's, or Product
    Owner's acceptance check) can only be denied with a concrete, actionable
    reason - not silently (just never calling advance_story_stage, with the
    "why" left in conversation text only, if stated at all) and not with an
    empty/placeholder/generic non-reason ("not good", "denied", ...) that
    gives Dev Team nothing to act on.
    """

    _VALID_REASON = "The pagination logic off-by-one errors on the last page - fix the loop bound."

    def test_architect_denies_reviewed_with_concrete_reason(self, mock_save, mock_md):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        result = deny_review("US-0001", "Reviewed", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        for backlog in ("product_backlog", "sprint_backlog"):
            denial = tc.state[backlog][0]["review_denial"]
            self.assertEqual(denial["stage"], "Reviewed")
            self.assertEqual(denial["reason"], self._VALID_REASON)
            self.assertEqual(denial["by"], "Architect")

    def test_qa_denies_tested_with_concrete_reason(self, mock_save, mock_md):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        result = deny_review("US-0001", "Tested", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["stage"], "Tested")

    def test_product_owner_denies_accepted_with_concrete_reason(self, mock_save, mock_md):
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        result = deny_review("US-0001", "Accepted", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["stage"], "Accepted")

    def test_rejects_empty_reason(self, mock_save, mock_md):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        result = deny_review("US-0001", "Reviewed", "", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("review_denial", tc.state["product_backlog"][0])

    def test_rejects_too_short_reason(self, mock_save, mock_md):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        result = deny_review("US-0001", "Reviewed", "bad code", tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_rejects_generic_reason(self, mock_save, mock_md):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        for generic in ("not good", "denied", "needs work", "does not meet criteria"):
            with self.subTest(generic=generic):
                result = deny_review("US-0001", "Tested", generic, tool_context=tc)
                self.assertEqual(result["status"], "error")

    def test_rejects_placeholder_reason(self, mock_save, mock_md):
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        result = deny_review("US-0001", "Accepted", "<describe what's wrong here>", tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_rejects_wrong_role(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready", "Implemented"])
        result = deny_review("US-0001", "Reviewed", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("Architect", result["message"])

    def test_rejects_non_deniable_stage(self, mock_save, mock_md):
        tc = _tool_context("ProductOwner", [])
        result = deny_review("US-0001", "Draft", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_unknown_story_errors(self, mock_save, mock_md):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        result = deny_review("US-9999", "Reviewed", self._VALID_REASON, tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_advancing_past_denied_stage_clears_the_denial(self, mock_save, mock_md):
        """A resolved denial shouldn't linger as stale feedback once the
        story actually advances past the stage it was denied at."""
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        deny_review("US-0001", "Reviewed", self._VALID_REASON, tool_context=tc)
        self.assertIsNotNone(tc.state["product_backlog"][0]["review_denial"])

        # A real review call from Architect happened (re-review) - satisfies
        # advance_story_stage's own "Reviewed" gate, unrelated to deny_review.
        tc.state["pr_review_calls"] = {"Architect": 1}
        with patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0]["review_denial"])

    def test_denying_a_different_stage_does_not_clear_an_unrelated_denial(self, mock_save, mock_md):
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        deny_review("US-0001", "Reviewed", self._VALID_REASON, tool_context=tc)
        # Some other, unrelated progress happens (e.g. re-review not yet done) -
        # the denial must survive until the SAME stage actually advances.
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["stage"], "Reviewed")

    def test_denial_blocks_advance_even_if_the_sprintwide_counter_already_passes(self, mock_save, mock_md):
        """
        Acceptance Criteria (ISSUE-0044): the Reviewed/Tested gate's own
        evidence check is sprint-wide (pr_review_calls), not scoped to one
        story - so a story that was just denied could previously still
        advance right away, as long as *some* Architect review call (even
        the very one that led to the denial) already satisfied that
        sprint-wide count. A denial must require a review that's genuinely
        NEW since THIS story's own denial, not just since the last story
        that reached Reviewed at all.
        """
        tc = _tool_context("Architect", ["Ready", "Implemented"])
        # A real review call already happened (e.g. Architect's own
        # REQUEST_CHANGES comment) before the denial - the sprint-wide
        # counter is already past baseline at deny time.
        tc.state["pr_review_calls"] = {"Architect": 1}

        deny_review("US-0001", "Reviewed", self._VALID_REASON, tool_context=tc)
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["review_count_at_denial"], 1)

        # No NEW review call since the denial - the sprint-wide count is
        # unchanged - so this must still be refused.
        with patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn(self._VALID_REASON, result["message"])

        # A genuinely fresh review call after the denial resolves it.
        tc.state["pr_review_calls"] = {"Architect": 2}
        with patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0]["review_denial"])

    def test_tested_denial_blocks_advance_even_if_the_sprintwide_counter_already_passes(self, mock_save, mock_md):
        """Same ISSUE-0044 fix, QA/Tested side."""
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])
        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}

        deny_review("US-0001", "Tested", self._VALID_REASON, tool_context=tc)
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["review_count_at_denial"], 1)

        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 5, "tests_failed": 0},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn(self._VALID_REASON, result["message"])

        tc.state["pr_review_calls"] = {"QA": 2}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 5, "tests_failed": 0},
        ), patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0]["review_denial"])


class TestDenyReviewSurfacesInStoryMarkdown(unittest.TestCase):
    """_update_story_markdown itself (not mocked here) must fold a recorded
    review_denial into the story's rendered Notes section, so it's visible
    via `read_doc` - not just something said once in conversation."""

    def test_review_denial_appears_in_rendered_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.scrum_team.tools.requirements._configured_repo_root", return_value=Path(tmp)):
                item = {
                    "id": "US-0001",
                    "title": "Add login flow",
                    "type": "User Story",
                    "status": "Implemented",
                    "user_story": "As a user, I want to log in, so that I can access my account.",
                    "acceptance_criteria": ["Given valid creds, when I submit, then I'm logged in"],
                    "review_denial": {
                        "stage": "Reviewed",
                        "reason": "The session token is never invalidated on logout - fix that first.",
                        "by": "Architect",
                    },
                }
                result = _update_story_markdown(item, tool_context=MagicMock(state={}))
                self.assertEqual(result["status"], "ok")
                content = Path(result["path"]).read_text(encoding="utf-8")
                self.assertIn("REVIEW DENIED", content)
                self.assertIn("session token is never invalidated", content)


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

    def test_setting_status_to_draft_directly_is_blocked(self):
        """GH issue #94: Draft is now a real STORY_STAGES entry, not a
        free-form label - direct-setting it is exactly the pipeline bypass
        blocks_direct_status_set exists to close, same as any other stage."""
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_backlog_item({"id": "US-0001", "title": "Foo", "status": "Draft"}, tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("advance_story_stage", result["message"])


class TestUpsertStoryEpicIssueCoerceJsonStringArg(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run crashed the whole node with
    `TypeError: 'str' object does not support item assignment` - a model
    passed the dict-typed argument (story/epic/issue) as a JSON-encoded
    string instead of a real object. upsert_story/upsert_epic/upsert_issue
    must transparently accept that shape instead of crashing, and must
    return a normal tool-level error (not raise) for anything that still
    isn't a JSON object.
    """

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_upsert_story_accepts_json_string(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_story('{"id": "US-0001", "title": "Foo"}', tool_context=tc)
        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_upsert_epic_accepts_json_string(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_epic('{"id": "EP-0001", "title": "Foo"}', tool_context=tc)
        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_upsert_issue_accepts_json_string(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_issue('{"id": "ISSUE-0001", "title": "Foo"}', tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_upsert_story_malformed_json_string_returns_error_not_crash(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_story("not json at all", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("upsert_story", result["message"])

    def test_upsert_story_non_object_json_string_returns_error_not_crash(self):
        """A JSON-valid string that decodes to something other than an
        object (e.g. a bare string or list) is still a caller error, not
        something to silently coerce further."""
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_story('"just a string"', tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_upsert_story_wrong_type_returns_error_not_crash(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_story(123, tool_context=tc)
        self.assertEqual(result["status"], "error")

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_upsert_issue_accepts_python_repr_string(self, mock_save, mock_md):
        """
        Acceptance Criteria: a real eval run had QualityGuardian call
        upsert_issue(issue="{'title': 'Review PR ...', 'description':
        '...'}") - a Python repr (single-quoted), not valid JSON, so
        json.loads alone rejected it as "expected an object, got str" and
        the model never recovered. ast.literal_eval (via _coerce_dict_arg)
        must parse that shape too.
        """
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        result = upsert_issue(
            "{'title': 'Review PR feature/US-0099-add-login', 'description': 'Code review is pending'}",
            tool_context=tc,
        )
        self.assertEqual(result["status"], "ok")


class TestPlanBacklogItemPropagatesFailures(unittest.TestCase):
    """Acceptance Criteria (GH issue #120): plan_backlog_item must surface a
    sub-call failure (set_priority/update_roadmap) as its own top-level
    status/message instead of always reporting "ok"."""

    def _tool_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.state["product_backlog"] = [{"id": "US-0001", "title": "Foo", "priority": "Low"}]
        return tc

    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_unknown_item_priority_failure_propagates(self, mock_save):
        tc = self._tool_context()
        result = plan_backlog_item("does-not-exist", priority="High", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["message"].lower())

    @patch("agents.scrum_team.tools.requirements.update_roadmap")
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_roadmap_failure_propagates_even_when_priority_succeeds(self, mock_save, mock_update_roadmap):
        mock_update_roadmap.return_value = {"status": "error", "message": "ROADMAP.md not found and could not be seeded."}
        tc = self._tool_context()
        result = plan_backlog_item("US-0001", priority="High", version="v0.2", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("ROADMAP.md", result["message"])
        # The priority update itself succeeded and should still be reported.
        self.assertEqual(result["updates"][0]["result"]["status"], "ok")

    @patch("agents.scrum_team.tools.requirements.update_roadmap", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_all_sub_calls_succeeding_reports_ok(self, mock_save, mock_update_roadmap):
        tc = self._tool_context()
        result = plan_backlog_item("US-0001", priority="High", version="v0.2", tool_context=tc)
        self.assertEqual(result["status"], "ok")


class TestPriorityAffectsBacklogOrdering(unittest.TestCase):
    """Acceptance Criteria (GH issue #121): backlog order must actually
    reflect MoSCoW priority, since the one-story-at-a-time gate
    (_preceding_story) keys off backlog order directly."""

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_set_priority_moves_item_ahead_of_lower_priority_ones(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.state["product_backlog"] = [
            {"id": "US-0001", "title": "First", "priority": "Should"},
            {"id": "US-0002", "title": "Second", "priority": "Should"},
            {"id": "US-0003", "title": "Third", "priority": "Could"},
        ]
        result = set_priority("US-0003", "Must", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([x["id"] for x in tc.state["product_backlog"]], ["US-0003", "US-0001", "US-0002"])

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_same_priority_items_keep_relative_order(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.state["product_backlog"] = [
            {"id": "US-0001", "title": "First", "priority": "Should"},
            {"id": "US-0002", "title": "Second", "priority": "Should"},
            {"id": "US-0003", "title": "Third", "priority": "Should"},
        ]
        result = set_priority("US-0002", "Should", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([x["id"] for x in tc.state["product_backlog"]], ["US-0001", "US-0002", "US-0003"])

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_new_item_created_with_high_priority_jumps_the_queue(self, mock_save, mock_md):
        """upsert_backlog_item also re-sorts, since a new item's priority can
        be set at creation time rather than via a later set_priority call."""
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.state["product_backlog"] = [
            {"id": "US-0001", "title": "First", "priority": "Must"},
            {"id": "US-0002", "title": "Second", "priority": "Should"},
        ]
        result = upsert_backlog_item({"id": "US-0003", "title": "Third", "priority": "Must"}, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        ids = [x["id"] for x in tc.state["product_backlog"]]
        self.assertEqual(ids, ["US-0001", "US-0003", "US-0002"])

    @patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
    def test_unset_priority_ranks_as_must_not_pushed_to_the_back(self, mock_save, mock_md):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.state["product_backlog"] = [
            {"id": "US-0001", "title": "First", "priority": "Should"},
        ]
        result = upsert_backlog_item({"id": "US-0002", "title": "Second"}, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        ids = [x["id"] for x in tc.state["product_backlog"]]
        self.assertEqual(ids, ["US-0002", "US-0001"])


if __name__ == "__main__":
    unittest.main()
