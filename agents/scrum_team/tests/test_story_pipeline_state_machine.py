# agents/scrum_team/tests/test_story_pipeline_state_machine.py
"""
Scripted, no-LLM integration tests: read docs/DEVELOPMENT-WORKFLOW.md's diagram 2
as a state machine and drive one story through it by calling the REAL tool
functions in the exact order a correctly-behaving agent conversation would -
no model, no ADK runner, just a scripted sequence of the same Python calls the
model would otherwise make. Verifies the tool harness itself can actually
complete every valid path through the documented pipeline, including the
three review-fix loops (Architect's code review, QA's, Product Owner's
acceptance check).

Only genuine external boundaries are mocked: git/gh subprocess calls
(agents.scrum_team.tools.github._run) and the real pytest run
_execute_test_suite_coverage shells out to (agents.scrum_team.tools.
quality._run). State mutations, gate logic, and file writes
(write_file/upsert_story/upsert_prd/update_roadmap/_update_story_markdown)
all run for real, into the isolated tmp repo root the autouse
`_isolated_repo_root` fixture (conftest.py) already redirects every test to -
no real git repo or network access is ever touched.

INTERACTION_LEVEL is pinned to EVAL for the whole file: these tests are about
the mechanical dev/review/test/accept pipeline, not the separate human-
approval gates (already covered by TestReadyDesignApprovalGate and friends in
test_requirements.py) - EVAL requires none of those, keeping the scripted
sequence here focused on the state machine itself. TARGET_STORIES_PER_SPRINT/
READY_BACKLOG_SPRINTS_TARGET are likewise pinned to 1 each - this file's
single-story scripts aren't testing the Ready-backlog-sufficiency gate
(see test_sprint_and_approval_gates.py for that); a real sprint's start_sprint
+ create_sprint_backlog_pr call is still made (via _draft_to_ready ->
_publish_sprint_backlog) since Dev Team mechanically cannot start any story
otherwise.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.requirements import (
    upsert_story, set_priority, advance_story_stage, deny_review, record_acceptance_check,
    raise_story_blocker, resolve_story_blocker,
)
from agents.scrum_team.tools.docs import upsert_prd, write_file
from agents.scrum_team.tools.requirements import update_roadmap
from agents.scrum_team.tools.budget import log_story_tokens
from agents.scrum_team.tools.scrum import start_sprint
from agents.scrum_team.tools.github import (
    start_feature_branch, git_push, mark_pr_ready_for_review,
    gh_pr_review, merge_story_pr, create_sprint_backlog_pr,
)
from agents.scrum_team.tools.quality import check_build


_OK_RUN_RESULT = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}


def _fake_run(cmd, cwd=None, tool_context=None, timeout=None):
    """Stands in for every real git/gh subprocess call (github.py) and the
    real pytest run check_build/_execute_test_suite_coverage would otherwise
    shell out to (quality.py) - this file never touches a real repo, network,
    or test runner. `cmd` is inspected only to give the pytest invocation a
    stdout shape _execute_test_suite_coverage's own regexes can parse
    (_COVERAGE_TOTAL_RE/_PASSED_RE); every other command just succeeds."""
    if cmd and "pytest" in cmd:
        return {
            "status": "ok",
            "returncode": 0,
            "stdout": "TOTAL 100 10 90%\n5 passed in 0.42s",
            "stderr": "",
        }
    return dict(_OK_RUN_RESULT)


def _as(tool_context, agent_name):
    """Switches whose turn it is - tool_context.agent_name is what every
    ownership check (advance_story_stage, deny_review) and every
    pr_review_calls attribution (gh_pr_review/gh_pr_comment) reads."""
    tool_context.agent_name = agent_name
    return tool_context


@patch("agents.scrum_team.tools.quality._run", side_effect=_fake_run)
@patch("agents.scrum_team.tools.github._run", side_effect=_fake_run)
@patch.dict(os.environ, {
    "INTERACTION_LEVEL": "EVAL",
    "TARGET_STORIES_PER_SPRINT": "1",
    "READY_BACKLOG_SPRINTS_TARGET": "1",
})
class TestStoryPipelineStateMachine(unittest.TestCase):
    """
    Acceptance Criteria: the tool harness described by
    docs/DEVELOPMENT-WORKFLOW.md's diagram 2 can actually be driven start to
    finish - the golden path (every review approved first try) and all three
    documented review-fix loops (Architect/QA/Product Owner denying, then
    approving after a fix) - purely via scripted tool calls, no LLM involved.
    """

    def _new_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        return tc

    def _plan_story(self, tc):
        """Requirements engineering & product workflow sub-flow (diagram 2's
        ReqEng subgraph): vision/PRD, roadmap, and a real, complete story -
        everything that happens before/while a story sits in Draft."""
        _as(tc, "ProductOwner")
        self.assertEqual(
            upsert_prd("# Vision\nShip a working login flow.\n\n## Goals\n- Ship login", "MVP", tool_context=tc)["status"],
            "ok",
        )
        self.assertEqual(update_roadmap("v0.1", goals=["Ship login"], tool_context=tc)["status"], "ok")
        story_result = upsert_story(
            {
                "title": "Add login flow",
                "user_story": "As a user, I want to log in, so that I can access my account.",
                "acceptance_criteria": ["Given valid credentials, when I submit, then I'm logged in"],
            },
            tool_context=tc,
        )
        self.assertEqual(story_result["status"], "ok")
        story_id = story_result["item"]["id"]
        self.assertEqual(set_priority(story_id, "Must", tool_context=tc)["status"], "ok")
        return story_id

    def _publish_sprint_backlog(self, tc):
        """Dev Team mechanically cannot start (or finish) any story until
        THIS sprint's Sprint Backlog PR has actually merged (see
        sprint_backlog_pr_missing, agents/scrum_team/helpers.py) - every
        test that reaches Implemented must go through this first, same as a
        real conversation would via PO_PROMPT's SPRINT PLANNING section."""
        if not tc.state.get("sprint_goal"):
            _as(tc, "ScrumMaster")
            self.assertEqual(start_sprint("Ship this story end to end", tool_context=tc)["status"], "ok")
        _as(tc, "ProductOwner")
        self.assertEqual(create_sprint_backlog_pr(tool_context=tc)["status"], "ok")

    def _draft_to_ready(self, tc, story_id):
        self.assertEqual(advance_story_stage(story_id, "Draft", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Ready", tool_context=tc)["status"], "ok")
        self._publish_sprint_backlog(tc)

    def _implement(self, tc, story_id, slug="add-login", source_path="src/app.py", content="def login(): ..."):
        """GitFlow + Implemented - DevTeam's whole stage."""
        _as(tc, "DevTeam")
        self.assertEqual(start_feature_branch(story_id, slug, tool_context=tc)["status"], "ok")
        branch = tc.state["active_feature_branches"][story_id]
        self.assertEqual(write_file(source_path, content, overwrite=True, tool_context=tc)["status"], "ok")
        self.assertEqual(log_story_tokens(story_id, 1234, tool_context=tc)["status"], "ok")
        self.assertEqual(git_push(branch=branch, commit_message="feat: add login", tool_context=tc)["status"], "ok")
        self.assertEqual(mark_pr_ready_for_review(tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Implemented", tool_context=tc)["status"], "ok")
        return branch

    def _dev_pushes_a_fix(self, tc, branch, content="def login(): ...  # fixed"):
        _as(tc, "DevTeam")
        self.assertEqual(write_file("src/app.py", content, overwrite=True, tool_context=tc)["status"], "ok")
        self.assertEqual(git_push(branch=branch, commit_message="fix: address review feedback", tool_context=tc)["status"], "ok")

    # ------------------------------------------------------------------
    # Golden path: every review/check passes on the first try.
    # ------------------------------------------------------------------

    def test_golden_path_reaches_accepted(self, mock_gh_run, mock_quality_run):
        tc = self._new_context()
        story_id = self._plan_story(tc)
        self._draft_to_ready(tc, story_id)
        self._implement(tc, story_id)

        _as(tc, "Architect")
        self.assertEqual(gh_pr_review("Looks solid, approving.", event="APPROVE", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Reviewed", tool_context=tc)["status"], "ok")

        _as(tc, "QA")
        self.assertEqual(check_build(tool_context=tc)["status"], "ok")
        self.assertEqual(gh_pr_review("Test coverage looks good.", event="APPROVE", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Tested", tool_context=tc)["status"], "ok")
        self.assertEqual(merge_story_pr(tool_context=tc)["status"], "ok")

        _as(tc, "ProductOwner")
        self.assertEqual(record_acceptance_check(story_id, "Verified all AC met.", tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_id, "Accepted", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["stages_completed"],
            ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"],
        )
        self.assertIsNone(tc.state["product_backlog"][0].get("review_denial"))

    # ------------------------------------------------------------------
    # Loop 1: Architect's code review finds a problem, denies, DevTeam
    # fixes, Architect approves.
    # ------------------------------------------------------------------

    def test_architect_code_review_fix_loop(self, mock_gh_run, mock_quality_run):
        tc = self._new_context()
        story_id = self._plan_story(tc)
        self._draft_to_ready(tc, story_id)
        branch = self._implement(tc, story_id)

        _as(tc, "Architect")
        reason = "The password is stored in plain text - hash it with bcrypt before saving."
        self.assertEqual(gh_pr_review(reason, event="REQUEST_CHANGES", tool_context=tc)["status"], "ok")
        deny_result = deny_review(story_id, "Reviewed", reason, tool_context=tc)
        self.assertEqual(deny_result["status"], "ok")
        self.assertEqual(tc.state["product_backlog"][0]["review_denial"]["reason"], reason)
        # Still at Implemented - the denial didn't (and shouldn't) advance
        # the stage on its own.
        self.assertNotIn("Reviewed", tc.state["product_backlog"][0]["stages_completed"])

        self._dev_pushes_a_fix(tc, branch, content="def login(): ...  # now hashes with bcrypt")

        _as(tc, "Architect")
        self.assertEqual(gh_pr_review("Confirmed - bcrypt hashing looks correct now.", event="APPROVE", tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_id, "Reviewed", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        # A resolved denial doesn't linger as stale feedback once the story
        # actually advances past the stage it was denied at.
        self.assertIsNone(tc.state["product_backlog"][0].get("review_denial"))

    # ------------------------------------------------------------------
    # Loop 2: QA finds an issue (build/test failure or manual finding),
    # denies, DevTeam fixes, QA approves.
    # ------------------------------------------------------------------

    def test_qa_review_fix_loop(self, mock_gh_run, mock_quality_run):
        tc = self._new_context()
        story_id = self._plan_story(tc)
        self._draft_to_ready(tc, story_id)
        branch = self._implement(tc, story_id)

        _as(tc, "Architect")
        self.assertEqual(gh_pr_review("Looks solid, approving.", event="APPROVE", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Reviewed", tool_context=tc)["status"], "ok")

        _as(tc, "QA")
        self.assertEqual(check_build(tool_context=tc)["status"], "ok")
        reason = "Login accepts an empty password and logs the user in anyway - add validation and a test for it."
        self.assertEqual(gh_pr_review(reason, event="REQUEST_CHANGES", tool_context=tc)["status"], "ok")
        deny_result = deny_review(story_id, "Tested", reason, tool_context=tc)
        self.assertEqual(deny_result["status"], "ok")
        self.assertNotIn("Tested", tc.state["product_backlog"][0]["stages_completed"])

        self._dev_pushes_a_fix(tc, branch, content="def login(): ...  # now rejects empty passwords")

        _as(tc, "QA")
        self.assertEqual(check_build(tool_context=tc)["status"], "ok")
        self.assertEqual(gh_pr_review("Empty-password case is covered now.", event="APPROVE", tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_id, "Tested", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0].get("review_denial"))
        self.assertEqual(merge_story_pr(tool_context=tc)["status"], "ok")

    # ------------------------------------------------------------------
    # Loop 3: Product Owner's acceptance check finds the criteria aren't
    # actually met, denies, DevTeam fixes, PO accepts.
    # ------------------------------------------------------------------

    def test_acceptance_fix_loop(self, mock_gh_run, mock_quality_run):
        tc = self._new_context()
        story_id = self._plan_story(tc)
        self._draft_to_ready(tc, story_id)
        branch = self._implement(tc, story_id)

        _as(tc, "Architect")
        self.assertEqual(gh_pr_review("Looks solid, approving.", event="APPROVE", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Reviewed", tool_context=tc)["status"], "ok")

        _as(tc, "QA")
        self.assertEqual(check_build(tool_context=tc)["status"], "ok")
        self.assertEqual(gh_pr_review("Test coverage looks good.", event="APPROVE", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Tested", tool_context=tc)["status"], "ok")
        self.assertEqual(merge_story_pr(tool_context=tc)["status"], "ok")

        _as(tc, "ProductOwner")
        self.assertEqual(record_acceptance_check(story_id, "First pass", tool_context=tc)["status"], "ok")
        reason = "Acceptance criteria says login must work with valid credentials, but the demo build still 500s on submit."
        deny_result = deny_review(story_id, "Accepted", reason, tool_context=tc)
        self.assertEqual(deny_result["status"], "ok")
        self.assertNotIn("Accepted", tc.state["product_backlog"][0]["stages_completed"])

        self._dev_pushes_a_fix(tc, branch, content="def login(): ...  # fixed the 500 on submit")

        _as(tc, "ProductOwner")
        # A fresh acceptance check is required after the denial - re-using
        # the one that led to it wouldn't satisfy the gate (ISSUE-0044).
        self.assertEqual(record_acceptance_check(story_id, "Re-checked after fix", tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_id, "Accepted", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0].get("review_denial"))

    # ------------------------------------------------------------------
    # BLOCKED: DevTeam hits a genuine open question it can't answer alone
    # (not a code-review rejection - see deny_review above), raises a
    # blocker instead of guessing or looping; one-story-at-a-time ordering
    # skips the BLOCKED story so a lower-priority one can still proceed;
    # Architect (the "technical" category's resolver) answers it and the
    # story can continue.
    # ------------------------------------------------------------------

    def test_blocked_story_is_skipped_and_next_story_proceeds(self, mock_gh_run, mock_quality_run):
        tc = self._new_context()
        story_a = self._plan_story(tc)
        self._draft_to_ready(tc, story_a)

        story_b_result = upsert_story(
            {
                "title": "Add logout flow",
                "user_story": "As a user, I want to log out, so that my session ends.",
                "acceptance_criteria": ["Given I'm logged in, when I click logout, then my session ends"],
            },
            tool_context=tc,
        )
        self.assertEqual(story_b_result["status"], "ok")
        story_b = story_b_result["item"]["id"]
        self.assertEqual(set_priority(story_b, "Must", tool_context=tc)["status"], "ok")

        _as(tc, "DevTeam")
        question = "Should sessions be revoked server-side on logout, or is client-side token deletion enough?"
        block_result = raise_story_blocker(story_a, question, "technical", tool_context=tc)
        self.assertEqual(block_result["status"], "ok")

        # story_a is stuck at Ready, unresolved - it cannot advance further...
        result = advance_story_stage(story_a, "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("BLOCKED", result["message"])

        # ...but story_b (lower priority, behind story_a in product_backlog)
        # is NOT frozen by it - the team moves on instead of staying stuck.
        _as(tc, "ProductOwner")
        self.assertEqual(advance_story_stage(story_b, "Draft", tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_b, "Ready", tool_context=tc)
        self.assertEqual(result["status"], "ok")

        _as(tc, "Architect")
        resolution = "Server-side revocation - client-side deletion alone leaves the session valid if the token leaks."
        resolve_result = resolve_story_blocker(story_a, resolution, tool_context=tc)
        self.assertEqual(resolve_result["status"], "ok")
        self.assertIsNone(tc.state["product_backlog"][0].get("blocked"))

        # Now that story_a is unblocked, it can proceed again.
        _as(tc, "DevTeam")
        self.assertEqual(write_file("src/app.py", "def login(): ...", overwrite=True, tool_context=tc)["status"], "ok")
        self.assertEqual(log_story_tokens(story_a, 500, tool_context=tc)["status"], "ok")
        result = advance_story_stage(story_a, "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "ok")
