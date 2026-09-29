# agents/scrum_team/tests/test_steering_proposals.py
"""
Tests for propose_steering_change (tools/workflow.py) - the sanctioned path
for an agent to propose an edit to THIS PROJECT's own AGENTS.md, in the
product/state repo (_configured_repo_root), as a human-reviewed PR.

Horseless-Carriage's own repo (prompts.py, docs/DEVELOPMENT-WORKFLOW.md,
spec-templates/DOD.md/DOR.md) is never a target of this tool at all - see a
real review comment on PR #306 (https://github.com/CI-Till-Krempel/
Horseless-Carriage/pull/306): those are fixed/non-negotiable and must never
be modifiable by a running instance, only the product/state repo can be
customized. This file replaces the earlier version that tested an
allowlist/self-modification guard against prompts.py - that whole mechanism
no longer exists, since prompts.py is never touched by this tool anymore.

Mocks the git/gh boundary at the exact names workflow.py imports
(_run, _checkout_develop_or_recover, git_push, gh_pr_create) rather than
their defining modules, matching how the module under test actually calls
them. Real file reads/writes of AGENTS.md happen against the isolated tmp
repo root the autouse `_isolated_repo_root` fixture (conftest.py) redirects
every test to - workflow.py is included in that fixture's patched-module
list specifically so this suite (and propose_steering_change in real usage)
can never leak a write into this actual checkout.
"""
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.workflow import propose_steering_change
from agents.scrum_team.state import ScrumState


_OK_RUN_RESULT = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}


def _fake_checkout_develop_or_recover(repo_root, develop, tool_context=None):
    return {
        "status": "ok",
        "fetch": dict(_OK_RUN_RESULT),
        "checkout": dict(_OK_RUN_RESULT),
        "auto_integrated": None,
        "preserved_local_commits": None,
    }


def _fake_git_push(branch, commit_message="chore: update", add_all=True, tool_context=None):
    return {"status": "ok", "branch": branch, "steps": {}}


def _fake_gh_pr_create(title, body="", base=None, head=None, draft=False, head_is_resolved=False, tool_context=None):
    return {"status": "ok", "pr_number": 999, "url": "https://github.com/example/example/pull/999"}


def _make_tool_context(agent_name="ScrumMaster"):
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    tc.agent_name = agent_name
    return tc


def _patched(**overrides):
    """
    Patches the 4 git/gh-boundary names propose_steering_change calls,
    defaulting to the succeed-cleanly fakes above; pass e.g.
    git_push=my_fake to override just one for a specific test.
    """
    targets = {
        "_run": _overrides_or(overrides, "_run", lambda *a, **k: dict(_OK_RUN_RESULT)),
        "_checkout_develop_or_recover": _overrides_or(overrides, "_checkout_develop_or_recover", _fake_checkout_develop_or_recover),
        "git_push": _overrides_or(overrides, "git_push", _fake_git_push),
        "gh_pr_create": _overrides_or(overrides, "gh_pr_create", _fake_gh_pr_create),
    }
    return [
        patch(f"agents.scrum_team.tools.workflow.{name}", side_effect=fn)
        for name, fn in targets.items()
    ]


def _overrides_or(overrides, key, default):
    return overrides.get(key, default)


class TestProposeSteeringChange(unittest.TestCase):
    def _run_with_patches(self, fn, **overrides):
        patchers = _patched(**overrides)
        for p in patchers:
            p.start()
        try:
            return fn()
        finally:
            for p in patchers:
                p.stop()

    def test_rejects_missing_or_too_short_rationale(self):
        result = self._run_with_patches(
            lambda: propose_steering_change("Some new AGENTS.md content.", "too short", tool_context=_make_tool_context())
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("rationale", result["message"])

    def test_requires_tool_context_state(self):
        result = self._run_with_patches(
            lambda: propose_steering_change("Some new AGENTS.md content.", "A real rationale explaining the need.", tool_context=None)
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("active session", result["message"])

    def test_creates_agents_md_when_none_exists_yet(self):
        tc = _make_tool_context()
        result = self._run_with_patches(
            lambda: propose_steering_change("# AGENTS.md\n\nFirst customization.\n", "A real rationale explaining the need.", tool_context=tc)
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposed"])

    def test_noop_when_new_content_matches_current_agents_md(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context()
        repo_root = base._configured_repo_root(tc)
        existing = "# AGENTS.md\n\nAlready here.\n"
        (repo_root / "AGENTS.md").write_text(existing, encoding="utf-8")

        result = self._run_with_patches(
            lambda: propose_steering_change(existing, "A real rationale explaining the need.", tool_context=tc)
        )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["proposed"])

    def test_successful_proposal_writes_file_pushes_and_opens_draft_pr(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context(agent_name="ScrumMaster")
        new_content = "# AGENTS.md\n\nRun `make lint` before every commit.\n"

        push_calls = []
        pr_calls = []

        def recording_git_push(branch, commit_message="chore: update", add_all=True, tool_context=None):
            push_calls.append({"branch": branch, "commit_message": commit_message, "add_all": add_all})
            return _fake_git_push(branch, commit_message, add_all, tool_context)

        def recording_gh_pr_create(title, body="", base=None, head=None, draft=False, head_is_resolved=False, tool_context=None):
            pr_calls.append({"title": title, "base": base, "head": head, "draft": draft, "head_is_resolved": head_is_resolved})
            return _fake_gh_pr_create(title, body, base, head, draft, head_is_resolved, tool_context)

        result = self._run_with_patches(
            lambda: propose_steering_change(new_content, "A real rationale explaining the need for this change.", tool_context=tc),
            git_push=recording_git_push,
            gh_pr_create=recording_gh_pr_create,
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposed"])
        self.assertTrue(result["branch"].startswith("steering/scrummaster-agents-md-"))

        repo_root = base._configured_repo_root(tc)
        self.assertEqual((repo_root / "AGENTS.md").read_text(encoding="utf-8"), new_content)

        self.assertEqual(len(push_calls), 1)
        self.assertTrue(push_calls[0]["add_all"])
        self.assertEqual(len(pr_calls), 1)
        self.assertTrue(pr_calls[0]["draft"])
        self.assertTrue(pr_calls[0]["head_is_resolved"])

    def test_checkout_develop_failure_returns_error_without_writing(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context()

        def failing_checkout(repo_root, develop, tool_context=None):
            return {
                "status": "error",
                "fetch": dict(_OK_RUN_RESULT),
                "checkout": {"status": "error", "stderr": "network unreachable", "returncode": 1},
                "auto_integrated": None,
                "preserved_local_commits": None,
            }

        result = self._run_with_patches(
            lambda: propose_steering_change("New content.", "A real rationale explaining the need.", tool_context=tc),
            _checkout_develop_or_recover=failing_checkout,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("Could not check out", result["message"])

        repo_root = base._configured_repo_root(tc)
        self.assertFalse((repo_root / "AGENTS.md").exists())

    def test_push_failure_returns_error(self):
        tc = _make_tool_context()

        def failing_push(branch, commit_message="chore: update", add_all=True, tool_context=None):
            return {"status": "error", "branch": branch, "steps": {}}

        result = self._run_with_patches(
            lambda: propose_steering_change("New content.", "A real rationale explaining the need.", tool_context=tc),
            git_push=failing_push,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("push", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
