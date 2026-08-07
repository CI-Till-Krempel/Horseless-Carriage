# agents/scrum_team/tests/test_requirements.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.requirements import (
    advance_story_stage, upsert_backlog_item, record_design_approval, record_acceptance_check, plan_backlog_item,
    set_priority, upsert_story, upsert_epic, upsert_issue, deny_review, _update_story_markdown,
    raise_story_blocker, resolve_story_blocker, declare_backlog_scope_complete,
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
    # This sprint's Sprint Backlog PR is assumed already merged - these
    # tests are about the Implemented/Reviewed/Tested/Ready gates
    # themselves, not sprint_backlog_pr_missing (see
    # test_sprint_and_approval_gates.py for that one).
    tc.state["sprint_number"] = 1
    tc.state["sprint_backlog_pr_sprint"] = 1
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

    def test_implemented_rejects_blank_or_placeholder_earlier_work_justification(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria (ISSUE-0046): implemented_via_earlier_work is an
        honest escape hatch, not a universal bypass - a blank or generic
        placeholder justification must still be rejected, same as
        is_low_quality_retro_text already does for retro/impediment text.
        """
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["specs/stories/US-0001-Add-real-feature.md"]
        for bad_justification in (None, "", "  ", "done", "n/a"):
            with self.subTest(justification=bad_justification):
                result = advance_story_stage(
                    "US-0001", "Implemented", implemented_via_earlier_work=bad_justification, tool_context=tc,
                )
                self.assertEqual(result["status"], "error")
                self.assertIn("source file", result["message"])

    def test_implemented_accepts_a_real_earlier_work_justification(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria (ISSUE-0046): a real eval run's DevTeam wrote the
        entire app (app.py/templates/index.html/test_app.py) once during
        US-0001, then had genuinely nothing new to write for US-0002 through
        US-0005 - with no honest way to say so, it fabricated a throwaway
        one-line "verification" stub file 4 times purely to satisfy this
        check (cluttering the repo root, docked in the report's Code
        Quality score). A substantive justification must now be accepted
        instead, and logged to decision_log for audit rather than silently
        bypassing the check.
        """
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["specs/stories/US-0001-Add-real-feature.md"]
        tc.state["story_estimates"] = {"US-0001": {"estimate": 10, "actual": 5}}
        justification = "Already implemented as part of US-0000's app.py edit in commit abc123."

        result = advance_story_stage(
            "US-0001", "Implemented", implemented_via_earlier_work=justification, tool_context=tc,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(tc.state["decision_log"]), 1)
        self.assertEqual(tc.state["decision_log"][0]["rationale"], justification)

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

    def test_accepted_requires_a_recorded_acceptance_check(self, mock_save, mock_md, mock_roadmap):
        """Acceptance Criteria (ISSUE-0043): Accepted previously had no
        evidence gate at all - any role could call advance_story_stage
        on assertion alone."""
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        result = advance_story_stage("US-0001", "Accepted", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("record_acceptance_check", result["message"])

    def test_accepted_succeeds_once_acceptance_check_is_recorded(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        record_acceptance_check("US-0001", "Verified all AC met.", tool_context=tc)
        result = advance_story_stage("US-0001", "Accepted", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_blocked_story_refuses_any_stage_advance(self, mock_save, mock_md, mock_roadmap):
        """A BLOCKED story (raise_story_blocker) refuses every further
        advance_story_stage call for it, from any stage, until
        resolve_story_blocker clears it."""
        tc = _tool_context("DevTeam", ["Ready"])
        tc.state["product_backlog"][0]["blocked"] = {
            "question": "Which payment gateway should this integrate with?",
            "category": "product",
            "raised_by": "DevTeam",
            "escalated_to_user": False,
        }
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        tc.state["story_estimates"] = {"US-0001": {"estimate": 10, "actual": 5}}
        result = advance_story_stage("US-0001", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("BLOCKED", result["message"])
        self.assertIn("resolve_story_blocker", result["message"])


@patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestStageRejectionLoopBreaker(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run showed advance_story_stage(story,
    stage) rejected for the exact same content/evidence reason 9 times in a
    row, with neither agent.py's _detect_transfer_loop nor
    _detect_repeated_call_loop ever tripping - both only catch a single
    action repeated back-to-back with nothing else interleaved, but this
    loop was a repeating multi-step sequence (check_build -> gh_pr_comment ->
    advance_story_stage(rejected) -> transfer_to_agent -> ...), so no single
    call ever repeated "in a row". _reject_stage_transition (requirements.py)
    tracks the (story, stage) pair itself, surviving whatever real, distinct
    tool calls happen between attempts.
    """

    def test_survives_intervening_calls_then_blocks_at_threshold(self, mock_save, mock_md, mock_roadmap):
        from agents.scrum_team.tools.quality import check_build

        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])

        for i in range(2):
            # No QA gh_pr_review/gh_pr_comment call recorded - the same
            # content-gate rejection every time, but with a genuinely
            # different, real tool call (check_build) in between, exactly
            # the shape that defeated the two existing loop breakers.
            with patch("agents.scrum_team.tools.quality._run", return_value={"status": "ok", "stdout": "", "stderr": ""}), \
                 patch("agents.scrum_team.tools.quality._configured_repo_root", return_value=Path(tempfile.gettempdir())):
                check_build(tool_context=tc)
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
            self.assertEqual(result["status"], "error")
            self.assertIn("gh_pr_review/gh_pr_comment", result["message"])
            self.assertIsNone(tc.state["product_backlog"][0].get("blocked"))

        # 3rd rejection: same (story, stage) pair, still no QA review call -
        # this is the one that trips the breaker.
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("STAGE REJECTION LOOP DETECTED", result["message"])
        self.assertIsNotNone(tc.state["product_backlog"][0].get("blocked"))

        # BLOCKED now refuses any further call for this story, same as any
        # other BLOCKED story (raise_story_blocker's own mechanism).
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("BLOCKED", result["message"])

    def test_a_genuinely_different_rejection_reason_does_not_carry_the_streak(self, mock_save, mock_md, mock_roadmap):
        """Two different content-gate rejections for the SAME stage are
        different keys internally only by stage, not by reason - but this
        confirms a real fix (satisfying gate 1, hitting gate 2) doesn't
        itself get treated as 3 identical failures; the streak only grows on
        an actual repeat of advance_story_stage failing again."""
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])

        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertIn("gh_pr_review/gh_pr_comment", result["message"])

        # Now satisfy the QA-review-call gate - the very next rejection (if
        # any) is a fresh cause, not a continuation of the same failure.
        tc.state["pr_review_calls"] = {"QA": 1}
        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertIn("check_build", result["message"])
        self.assertIsNone(tc.state["product_backlog"][0].get("blocked"))

    def test_success_clears_the_streak(self, mock_save, mock_md, mock_roadmap):
        tc = _tool_context("QA", ["Ready", "Implemented", "Reviewed"])

        result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tc.state["_stage_rejection_streaks"]["US-0001:Tested"], 1)

        tc.state["pr_review_calls"] = {"QA": 1}
        tc.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        with patch(
            "agents.scrum_team.tools.quality._execute_test_suite_coverage",
            return_value={"available": True, "tests_run": 5, "tests_failed": 0},
        ):
            result = advance_story_stage("US-0001", "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("US-0001:Tested", tc.state.get("_stage_rejection_streaks", {}))


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
        tc.state["sprint_number"] = 1
        tc.state["sprint_backlog_pr_sprint"] = 1
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

    def test_blocked_preceding_story_does_not_block_advancement(self, mock_save, mock_md, mock_roadmap):
        """
        Acceptance Criteria: a BLOCKED story (raise_story_blocker) shouldn't
        also freeze every lower-priority story behind it - the team is meant
        to move on to the next one while it waits (RELEASE.md "Blocked
        stories"). _preceding_story must skip a BLOCKED predecessor and look
        further back, not treat it as an ordinary incomplete story.
        """
        tc = self._two_story_context("ProductOwner", ["Ready"])
        tc.state["product_backlog"][0]["blocked"] = {
            "question": "Which auth provider should this integrate with?",
            "category": "product",
            "raised_by": "DevTeam",
            "escalated_to_user": False,
        }
        tc.state["human_approvals"] = [{"type": "sprint", "note": "ok"}]
        tc.state["sprint_files_touched"] = ["app/main.py"]
        tc.state["story_estimates"] = {"US-0002": {"estimate": 10, "actual": 5}}
        tc.agent_name = "DevTeam"
        result = advance_story_stage("US-0002", "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "ok")

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
        # record_design_approval now requires evidence this story's own
        # create_story_spec_pr branch actually merged (see
        # test_sprint_and_approval_gates.py::TestStorySpecPrEvidenceGate for
        # that gate's own dedicated coverage) - not the concern of this
        # test, which is about the Ready gate reading design_approved once
        # it's set.
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Stakeholder"}, clear=True), \
             patch("agents.scrum_team.tools.github.story_spec_pr_merged", return_value=True):
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


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestRecordAcceptanceCheck(unittest.TestCase):
    """Acceptance Criteria (ISSUE-0043): record_acceptance_check sets a
    per-story COUNTER (not a one-time boolean like design_approved) on both
    backlog copies, so a denial can later require a genuinely fresh check
    (ISSUE-0044) rather than reuse of the one that got denied."""

    def test_increments_counter_on_both_backlog_copies(self, mock_save):
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        result = record_acceptance_check("US-0001", "Verified all AC met.", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tc.state["product_backlog"][0]["acceptance_check_count"], 1)
        self.assertEqual(tc.state["sprint_backlog"][0]["acceptance_check_count"], 1)
        self.assertEqual(tc.state["product_backlog"][0]["acceptance_check_note"], "Verified all AC met.")

    def test_counter_increments_across_repeated_calls(self, mock_save):
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        record_acceptance_check("US-0001", "first pass", tool_context=tc)
        result = record_acceptance_check("US-0001", "second pass", tool_context=tc)
        self.assertEqual(result["acceptance_check_count"], 2)
        self.assertEqual(tc.state["product_backlog"][0]["acceptance_check_count"], 2)

    def test_unknown_story_errors(self, mock_save):
        tc = _tool_context("ProductOwner", [])
        result = record_acceptance_check("US-9999", "note", tool_context=tc)
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

    def test_accepted_denial_blocks_advance_even_if_already_checked_once(self, mock_save, mock_md):
        """Same ISSUE-0044 fix, Accepted/record_acceptance_check side
        (ISSUE-0043): a denial must require a genuinely NEW
        record_acceptance_check call, not just reuse of the check that led
        to the denial."""
        tc = _tool_context("ProductOwner", ["Ready", "Implemented", "Reviewed", "Tested"])
        # A check already happened before the denial - the counter is
        # already > 0 at deny time.
        record_acceptance_check("US-0001", "First pass, missed something", tool_context=tc)
        self.assertEqual(tc.state["product_backlog"][0]["acceptance_check_count"], 1)

        deny_review("US-0001", "Accepted", self._VALID_REASON, tool_context=tc)
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["acceptance_count_at_denial"], 1)

        # No NEW acceptance check since the denial - must still be refused.
        with patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Accepted", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn(self._VALID_REASON, result["message"])

        # A genuinely fresh acceptance check after the denial resolves it.
        record_acceptance_check("US-0001", "Re-checked after fix", tool_context=tc)
        with patch("agents.scrum_team.tools.requirements._sync_roadmap_for_story", return_value={"status": "ok"}):
            result = advance_story_stage("US-0001", "Accepted", tool_context=tc)
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

    def test_blocked_appears_in_rendered_notes(self):
        """Same as above, for raise_story_blocker's `blocked` field - a
        BLOCKED story's open question must be visible via read_doc too."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.scrum_team.tools.requirements._configured_repo_root", return_value=Path(tmp)):
                item = {
                    "id": "US-0001",
                    "title": "Add login flow",
                    "type": "User Story",
                    "status": "Implemented",
                    "user_story": "As a user, I want to log in, so that I can access my account.",
                    "acceptance_criteria": ["Given valid creds, when I submit, then I'm logged in"],
                    "blocked": {
                        "question": "Which identity provider should this integrate with?",
                        "category": "product",
                        "raised_by": "DevTeam",
                    },
                }
                result = _update_story_markdown(item, tool_context=MagicMock(state={}))
                self.assertEqual(result["status"], "ok")
                content = Path(result["path"]).read_text(encoding="utf-8")
                self.assertIn("BLOCKED", content)
                self.assertIn("Which identity provider should this integrate with", content)


@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestRaiseStoryBlocker(unittest.TestCase):
    """
    Acceptance Criteria: a story can become BLOCKED from any stage (not just
    a fixed pipeline point) when the team genuinely can't proceed - a real
    open question, not a rejected review (see deny_review). Any role may
    raise one; category ("technical"/"product") decides who's asked.
    """

    _VALID_QUESTION = "Which payment gateway should this integrate with - Stripe or the in-house one?"

    def test_any_role_can_raise_a_blocker(self, mock_save, mock_md):
        for role in ("ProductOwner", "ScrumMaster", "DevTeam", "QA", "Architect"):
            with self.subTest(role=role):
                tc = _tool_context(role, ["Ready"])
                result = raise_story_blocker("US-0001", self._VALID_QUESTION, "product", tool_context=tc)
                self.assertEqual(result["status"], "ok")

    def test_sets_blocked_on_both_backlog_copies(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        result = raise_story_blocker("US-0001", self._VALID_QUESTION, "technical", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        for backlog in ("product_backlog", "sprint_backlog"):
            blocked = tc.state[backlog][0]["blocked"]
            self.assertEqual(blocked["question"], self._VALID_QUESTION)
            self.assertEqual(blocked["category"], "technical")
            self.assertEqual(blocked["raised_by"], "DevTeam")
            self.assertFalse(blocked["escalated_to_user"])

    def test_records_a_blocking_interaction(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        raise_story_blocker("US-0001", self._VALID_QUESTION, "technical", tool_context=tc)
        interactions = tc.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "blocked_story")
        self.assertIn("US-0001", interactions[0]["summary"])
        blocked = tc.state["product_backlog"][0]["blocked"]
        self.assertEqual(blocked["blocking_interaction_id"], interactions[0]["id"])

    def test_escalates_to_user_for_product_category_at_product_level(self, mock_save, mock_md):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            tc = _tool_context("DevTeam", ["Ready"])
            raise_story_blocker("US-0001", self._VALID_QUESTION, "product", tool_context=tc)
            self.assertTrue(tc.state["product_backlog"][0]["blocked"]["escalated_to_user"])

    def test_does_not_escalate_technical_category_at_product_level(self, mock_save, mock_md):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            tc = _tool_context("DevTeam", ["Ready"])
            raise_story_blocker("US-0001", self._VALID_QUESTION, "technical", tool_context=tc)
            self.assertFalse(tc.state["product_backlog"][0]["blocked"]["escalated_to_user"])

    def test_rejects_invalid_category(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        result = raise_story_blocker("US-0001", self._VALID_QUESTION, "business", tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_rejects_generic_question(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        for generic in ("not sure", "stuck", "tbd"):
            with self.subTest(generic=generic):
                result = raise_story_blocker("US-0001", generic, "technical", tool_context=tc)
                self.assertEqual(result["status"], "error")

    def test_unknown_story_errors(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        result = raise_story_blocker("US-9999", self._VALID_QUESTION, "technical", tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_already_blocked_story_refuses_a_second_raise(self, mock_save, mock_md):
        tc = _tool_context("DevTeam", ["Ready"])
        raise_story_blocker("US-0001", self._VALID_QUESTION, "technical", tool_context=tc)
        result = raise_story_blocker("US-0001", "A completely different question here", "product", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("already BLOCKED", result["message"])


@patch("agents.scrum_team.tools.requirements._update_story_markdown", return_value={"status": "ok"})
@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestResolveStoryBlocker(unittest.TestCase):
    """Acceptance Criteria: only the category's owning role can clear a
    BLOCKED story, and a "product" blocker escalated to the human User at
    the Product interaction level requires that human's own resolution
    first - Product Owner cannot route around it with its own judgment."""

    _VALID_QUESTION = "Which payment gateway should this integrate with - Stripe or the in-house one?"
    _VALID_RESOLUTION = "Decided on Stripe - it's already used by the billing service."

    def _blocked_context(self, category, agent_name="DevTeam"):
        """A story already BLOCKED, raised by DevTeam (an arbitrary raiser -
        raise_story_blocker allows any role), with tc.agent_name then set to
        whichever role the test wants to attempt resolve_story_blocker as.
        Raised at the EVAL interaction level specifically - "Product" is the
        one level where a "product"-category blocker escalates to the human
        User instead (see test_escalated_product_blocker_requires_human_
        resolution_first below), which these tests aren't about."""
        tc = _tool_context("DevTeam", ["Ready"])
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            raise_story_blocker("US-0001", self._VALID_QUESTION, category, tool_context=tc)
        tc.agent_name = agent_name
        return tc

    def test_product_owner_resolves_a_product_blocker(self, mock_save, mock_md):
        tc = self._blocked_context("product", agent_name="ProductOwner")
        result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0]["blocked"])

    def test_architect_resolves_a_technical_blocker(self, mock_save, mock_md):
        tc = self._blocked_context("technical", agent_name="Architect")
        result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
        self.assertEqual(result["status"], "ok")

    def test_wrong_role_cannot_resolve(self, mock_save, mock_md):
        tc = self._blocked_context("technical", agent_name="ProductOwner")
        result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("Architect", result["message"])

    def test_not_blocked_story_errors(self, mock_save, mock_md):
        tc = _tool_context("ProductOwner", ["Ready"])
        result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
        self.assertEqual(result["status"], "error")

    def test_rejects_generic_resolution(self, mock_save, mock_md):
        tc = self._blocked_context("product", agent_name="ProductOwner")
        result = resolve_story_blocker("US-0001", "fixed", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIsNotNone(tc.state["product_backlog"][0]["blocked"])

    def test_resolves_the_linked_blocking_interaction(self, mock_save, mock_md):
        tc = self._blocked_context("product", agent_name="ProductOwner")
        interaction_id = tc.state["product_backlog"][0]["blocked"]["blocking_interaction_id"]
        resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
        interaction = next(i for i in tc.state["blocking_interactions"] if i["id"] == interaction_id)
        self.assertTrue(interaction["resolved"])

    def test_escalated_product_blocker_requires_human_resolution_first(self, mock_save, mock_md):
        """
        Acceptance Criteria: at the Product interaction level, a "product"
        blocker was escalated straight to the human User when raised - this
        must mechanically refuse Product Owner's own resolution until that
        human has actually resolved the linked blocking_interaction.
        """
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            tc = _tool_context("DevTeam", ["Ready"])
            raise_story_blocker("US-0001", self._VALID_QUESTION, "product", tool_context=tc)
            tc.agent_name = "ProductOwner"

            result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
            self.assertEqual(result["status"], "error")
            self.assertIn("human User", result["message"])
            self.assertIsNotNone(tc.state["product_backlog"][0]["blocked"])

            from agents.scrum_team.tools.notifications import resolve_blocking_interaction
            interaction_id = tc.state["product_backlog"][0]["blocked"]["blocking_interaction_id"]
            resolve_blocking_interaction(interaction_id, tool_context=tc)

            result = resolve_story_blocker("US-0001", self._VALID_RESOLUTION, tool_context=tc)
            self.assertEqual(result["status"], "ok")
            self.assertIsNone(tc.state["product_backlog"][0]["blocked"])


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


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestDeclareBacklogScopeComplete(unittest.TestCase):
    """
    Acceptance Criteria (ISSUE-0046): an honest, justified escape hatch from
    ready_backlog_shortfall's target - a real eval run's fixed,
    deliberately-closed-scope product genuinely ran out of real stories
    against the target and, with no honest way to say so, fabricated two
    throwaway "Additional Buffer Story" entries purely to pad the count.
    """

    def _tc(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        tc.agent_name = "ProductOwner"
        return tc

    def test_rejects_blank_or_placeholder_justification(self, mock_save):
        tc = self._tc()
        for bad in (None, "", "  ", "done", "n/a"):
            with self.subTest(justification=bad):
                result = declare_backlog_scope_complete(bad, tool_context=tc)
                self.assertEqual(result["status"], "error")
                self.assertFalse(tc.state["backlog_scope_complete"])

    def test_accepts_a_real_justification_and_logs_it(self, mock_save):
        tc = self._tc()
        justification = "All 6 product-vision stories are Accepted or Ready; the vision explicitly excludes further scope."

        result = declare_backlog_scope_complete(justification, tool_context=tc)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(tc.state["backlog_scope_complete"])
        self.assertEqual(len(tc.state["decision_log"]), 1)
        self.assertEqual(tc.state["decision_log"][0]["rationale"], justification)


if __name__ == "__main__":
    unittest.main()
