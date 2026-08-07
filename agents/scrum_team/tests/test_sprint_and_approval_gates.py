# agents/scrum_team/tests/test_sprint_and_approval_gates.py
"""
Scripted, no-LLM tests for the gates added after a real eval run left a
sprint's token budget exhausted mid-story with NOTHING merged anywhere -
create_sprint_backlog_pr was only ever a prompt instruction away from
"Dev Team starts implementing", so a cut-short sprint could leave every
spec write (roadmap, PRD, epics, stories) stuck, uncommitted, on whatever
branch happened to be checked out. See docs/DEVELOPMENT-WORKFLOW.md and
this file's plan (agents/scrum_team/helpers.py's sprint_backlog_pr_missing/
ready_backlog_shortfall, agents/scrum_team/tools/github.py's
create_sprint_backlog_pr/create_story_spec_pr).

Covers, each as its own mechanical gate:
- Dev Team cannot start (or finish) a story until THIS sprint's Sprint
  Backlog PR has actually merged.
- create_sprint_backlog_pr refuses to run at all while the Ready backlog is
  short of holding enough queued-up work.
- create_sprint_backlog_pr opens the PR but withholds the merge until a
  required human approval is freshly recorded, then merges on re-call.
- record_design_approval, at the Stakeholder level, refuses without real
  evidence (a merged create_story_spec_pr) instead of a bare assertion.

Only genuine external boundaries are mocked (agents.scrum_team.tools.
github._run) - state mutations and file writes all run for real, into the
isolated tmp repo root the autouse `_isolated_repo_root` fixture
(conftest.py) already redirects every test to.
"""
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.requirements import (
    upsert_story, set_priority, advance_story_stage, record_design_approval,
)
from agents.scrum_team.tools.scrum import start_sprint, record_human_approval
from agents.scrum_team.tools.github import (
    start_feature_branch, create_sprint_backlog_pr, create_story_spec_pr,
)


def _as(tool_context, agent_name):
    tool_context.agent_name = agent_name
    return tool_context


class _FakeGhRuns:
    """Stands in for every real git/gh subprocess call github.py would
    otherwise make. `gh pr view <branch> ...` looks up `pr_states` (a dict
    of branch -> "OPEN"/"MERGED"/None) so tests can control exactly what
    create_sprint_backlog_pr's/create_story_spec_pr's "does a PR already
    exist" check and story_spec_pr_merged's evidence check each see -
    everything else (fetch/checkout/push/pr create/merge) just succeeds.
    """

    def __init__(self):
        self.pr_states = {}

    def merge(self, branch):
        self.pr_states[branch] = "MERGED"

    def __call__(self, cmd, cwd=None, tool_context=None, timeout=None, env_overrides=None):
        if cmd and cmd[:3] == ["gh", "pr", "view"]:
            branch = cmd[3] if len(cmd) > 3 else None
            state = self.pr_states.get(branch)
            if state is None:
                return {"status": "error", "returncode": 1, "stdout": "", "stderr": "no pull requests found"}
            return {"status": "ok", "returncode": 0, "stdout": json.dumps({"number": 1, "state": state}), "stderr": ""}
        if cmd and cmd[:3] == ["gh", "pr", "merge"]:
            branch = cmd[3] if len(cmd) > 3 else None
            if branch:
                self.pr_states[branch] = "MERGED"
            return {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}
        if cmd and cmd[:3] == ["gh", "pr", "create"]:
            # gh_pr_create doesn't need the branch name back out of this -
            # mark whatever --head names as freshly opened, so a later
            # `gh pr view` in the same test sees it exists (as OPEN).
            if "--head" in cmd:
                branch = cmd[cmd.index("--head") + 1]
                self.pr_states.setdefault(branch, "OPEN")
            return {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}
        return {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}


class TestSprintBacklogPrGate(unittest.TestCase):
    """Dev Team mechanically cannot start (or finish) a story until this
    sprint's Sprint Backlog PR has actually merged (the direct fix for the
    "nothing was merged" failure)."""

    def _new_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        return tc

    def _ready_story(self, tc, title):
        _as(tc, "ProductOwner")
        result = upsert_story(
            {
                "title": title,
                "user_story": f"As a user, I want {title.lower()}, so that I benefit.",
                "acceptance_criteria": ["Given a precondition, when I act, then the outcome happens"],
            },
            tool_context=tc,
        )
        self.assertEqual(result["status"], "ok")
        story_id = result["item"]["id"]
        self.assertEqual(set_priority(story_id, "Must", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Draft", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Ready", tool_context=tc)["status"], "ok")
        return story_id

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {
        "INTERACTION_LEVEL": "EVAL",
        "TARGET_STORIES_PER_SPRINT": "1",
        "READY_BACKLOG_SPRINTS_TARGET": "1",
    })
    def test_start_feature_branch_refuses_without_sprint_started(self, mock_run):
        mock_run.side_effect = _FakeGhRuns()
        tc = self._new_context()
        self._ready_story(tc, "Add login flow")

        _as(tc, "DevTeam")
        result = start_feature_branch("US-0001", "add-login", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("start_sprint", result["message"])

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {
        "INTERACTION_LEVEL": "EVAL",
        "TARGET_STORIES_PER_SPRINT": "1",
        "READY_BACKLOG_SPRINTS_TARGET": "1",
    })
    def test_start_feature_branch_refuses_until_backlog_pr_merges_then_succeeds(self, mock_run):
        fake = _FakeGhRuns()
        mock_run.side_effect = fake
        tc = self._new_context()
        story_id = self._ready_story(tc, "Add login flow")

        _as(tc, "ScrumMaster")
        self.assertEqual(start_sprint("Ship the login flow end to end", tool_context=tc)["status"], "ok")

        _as(tc, "DevTeam")
        blocked = start_feature_branch(story_id, "add-login", tool_context=tc)
        self.assertEqual(blocked["status"], "error")
        self.assertIn("Sprint Backlog PR", blocked["message"])

        _as(tc, "ProductOwner")
        publish = create_sprint_backlog_pr(tool_context=tc)
        self.assertEqual(publish["status"], "ok")
        self.assertTrue(publish["merged"])
        self.assertEqual(tc.state["sprint_backlog_pr_sprint"], 1)

        _as(tc, "DevTeam")
        result = start_feature_branch(story_id, "add-login", tool_context=tc)
        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {
        "INTERACTION_LEVEL": "EVAL",
        "TARGET_STORIES_PER_SPRINT": "1",
        "READY_BACKLOG_SPRINTS_TARGET": "1",
    })
    def test_advance_to_implemented_refuses_without_backlog_pr_even_for_a_spike(self, mock_run):
        """Belt-and-suspenders: a spike story skips start_feature_branch
        entirely (no code to branch for), so the same gate must also apply
        directly inside advance_story_stage's Implemented check."""
        mock_run.side_effect = _FakeGhRuns()
        tc = self._new_context()
        story_id = self._ready_story(tc, "Spike: evaluate auth providers")
        tc.state["product_backlog"][0]["spike"] = True

        _as(tc, "ScrumMaster")
        self.assertEqual(start_sprint("Spike on auth providers", tool_context=tc)["status"], "ok")

        _as(tc, "DevTeam")
        result = advance_story_stage(story_id, "Implemented", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("Sprint Backlog PR", result["message"])


class TestReadyBacklogSufficiencyGate(unittest.TestCase):
    """create_sprint_backlog_pr refuses to run while the Ready backlog is
    short of holding TARGET_STORIES_PER_SPRINT x READY_BACKLOG_SPRINTS_TARGET
    stories - the mechanical push-back into the requirements engineering
    loop instead of publishing (and thereby unlocking Dev Team on) a thin
    sprint."""

    def _new_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        return tc

    def _ready_story(self, tc, title):
        _as(tc, "ProductOwner")
        result = upsert_story(
            {
                "title": title,
                "user_story": f"As a user, I want {title.lower()}, so that I benefit.",
                "acceptance_criteria": ["Given a precondition, when I act, then the outcome happens"],
            },
            tool_context=tc,
        )
        story_id = result["item"]["id"]
        self.assertEqual(set_priority(story_id, "Must", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Draft", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Ready", tool_context=tc)["status"], "ok")
        return story_id

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {
        "INTERACTION_LEVEL": "EVAL",
        "TARGET_STORIES_PER_SPRINT": "3",
        "READY_BACKLOG_SPRINTS_TARGET": "2",
    })
    def test_refuses_when_short_then_succeeds_once_enough_ready(self, mock_run):
        mock_run.side_effect = _FakeGhRuns()
        tc = self._new_context()
        _as(tc, "ScrumMaster")
        self.assertEqual(start_sprint("Ship the MVP core flow", tool_context=tc)["status"], "ok")
        for i in range(2):
            self._ready_story(tc, f"Story {i}")

        _as(tc, "ProductOwner")
        short = create_sprint_backlog_pr(tool_context=tc)
        self.assertEqual(short["status"], "error")
        self.assertIn("4", short["message"])  # 3*2 target - 2 ready = 4 short

        for i in range(2, 6):
            self._ready_story(tc, f"Story {i}")

        result = create_sprint_backlog_pr(tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["merged"])


class TestSprintBacklogPrApprovalGate(unittest.TestCase):
    """'Approve sprint planning': at interaction levels requiring a fresh
    sprint/budget approval before Implemented, create_sprint_backlog_pr now
    opens the PR but withholds the merge until that approval is recorded,
    then merges on re-call - instead of self-approving via an instant
    --admin merge in the same call that opened it."""

    def _new_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        return tc

    def _ready_story(self, tc, title):
        _as(tc, "ProductOwner")
        result = upsert_story(
            {
                "title": title,
                "user_story": f"As a user, I want {title.lower()}, so that I benefit.",
                "acceptance_criteria": ["Given a precondition, when I act, then the outcome happens"],
            },
            tool_context=tc,
        )
        story_id = result["item"]["id"]
        self.assertEqual(set_priority(story_id, "Must", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Draft", tool_context=tc)["status"], "ok")
        self.assertEqual(advance_story_stage(story_id, "Ready", tool_context=tc)["status"], "ok")
        return story_id

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {
        "INTERACTION_LEVEL": "Product",
        "TARGET_STORIES_PER_SPRINT": "1",
        "READY_BACKLOG_SPRINTS_TARGET": "1",
    })
    def test_opens_without_merging_then_merges_once_approval_recorded(self, mock_run):
        fake = _FakeGhRuns()
        mock_run.side_effect = fake
        tc = self._new_context()
        _as(tc, "ScrumMaster")
        self.assertEqual(start_sprint("Ship the login flow", tool_context=tc)["status"], "ok")
        self._ready_story(tc, "Add login flow")

        _as(tc, "ProductOwner")
        opened = create_sprint_backlog_pr(tool_context=tc)
        self.assertEqual(opened["status"], "ok")
        self.assertFalse(opened["merged"])
        self.assertIn("record_human_approval", opened["message"])
        self.assertNotEqual(tc.state.get("sprint_backlog_pr_sprint"), 1)

        _as(tc, "ScrumMaster")
        self.assertEqual(record_human_approval("sprint", "Reviewed and approved.", tool_context=tc)["status"], "ok")

        _as(tc, "ProductOwner")
        merged = create_sprint_backlog_pr(tool_context=tc)
        self.assertEqual(merged["status"], "ok")
        self.assertTrue(merged["merged"])
        self.assertEqual(tc.state["sprint_backlog_pr_sprint"], 1)


class TestStorySpecPrEvidenceGate(unittest.TestCase):
    """At the Stakeholder interaction level, record_design_approval now
    requires real evidence - this story's own create_story_spec_pr branch
    must have actually merged - instead of a bare assertion."""

    def _new_context(self):
        tc = MagicMock()
        tc.state = ScrumState().model_dump()
        return tc

    def _draft_story(self, tc, title):
        _as(tc, "ProductOwner")
        result = upsert_story(
            {
                "title": title,
                "user_story": f"As a user, I want {title.lower()}, so that I benefit.",
                "acceptance_criteria": ["Given a precondition, when I act, then the outcome happens"],
            },
            tool_context=tc,
        )
        self.assertEqual(result["status"], "ok")
        return result["item"]["id"]

    @patch("agents.scrum_team.tools.github._run")
    @patch.dict(os.environ, {"INTERACTION_LEVEL": "Stakeholder"})
    def test_refuses_without_merged_spec_pr_then_succeeds_once_merged(self, mock_run):
        fake = _FakeGhRuns()
        mock_run.side_effect = fake
        tc = self._new_context()
        story_id = self._draft_story(tc, "Add login flow")

        _as(tc, "ProductOwner")
        denied = record_design_approval(story_id, "Looks good to me.", tool_context=tc)
        self.assertEqual(denied["status"], "error")
        self.assertIn("create_story_spec_pr", denied["message"])

        spec_pr = create_story_spec_pr(story_id, tool_context=tc)
        self.assertEqual(spec_pr["status"], "ok")
        # Stakeholder level: opened, but NOT auto-merged - a human still
        # needs to review/merge it themselves.
        self.assertFalse(spec_pr["merged"])

        still_denied = record_design_approval(story_id, "Looks good to me.", tool_context=tc)
        self.assertEqual(still_denied["status"], "error")

        # The human merges it themselves (simulated: the fake PR store now
        # reports MERGED for this story's branch).
        fake.merge(f"story-spec/{story_id}")

        approved = record_design_approval(story_id, "Looks good to me.", tool_context=tc)
        self.assertEqual(approved["status"], "ok")
        self.assertTrue(tc.state["product_backlog"][0]["design_approved"])


if __name__ == "__main__":
    unittest.main()
