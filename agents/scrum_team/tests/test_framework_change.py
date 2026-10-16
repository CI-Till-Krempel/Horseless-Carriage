# agents/scrum_team/tests/test_framework_change.py
"""
Tests for propose_framework_change (tools/workflow.py) - the sanctioned path
for the Architect to propose a change to this project's "workbench"
(.hc/workbench-setup.sh in the product/state repo, _configured_repo_root),
as a human-reviewed PR. Mirrors test_steering_proposals.py's own structure
closely - propose_framework_change is deliberately modeled on
propose_steering_change.

Mocks the git/gh boundary at the exact names workflow.py imports (_run,
_checkout_develop_or_recover, git_push, gh_pr_create, record_blocking_
interaction) rather than their defining modules, matching how the module
under test actually calls them. Real file reads/writes of
.hc/workbench-setup.sh happen against the isolated tmp repo root the
autouse `_isolated_repo_root` fixture (conftest.py) redirects every test to
- except inside the dockerized test run (INTERNAL_STATE_REPO_PATH set),
where that fixture's redirect condition doesn't fire and repo state can
leak across test methods within one run (same quirk
test_steering_proposals.py already documents and works around). Each test
here therefore uses its own distinct script content, never a shared
constant - sidesteps the leakage regardless of root cause, same as that
file's own workaround.
"""
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.workflow import propose_framework_change
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
    return {"status": "ok", "pr_number": 999, "stdout": "https://github.com/example/example/pull/999"}


def _fake_record_blocking_interaction(kind, summary, detail="", tool_context=None):
    return {"status": "ok"}


def _make_tool_context(agent_name="Architect"):
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    tc.agent_name = agent_name
    return tc


def _overrides_or(overrides, key, default):
    return overrides.get(key, default)


def _patched(**overrides):
    targets = {
        "_run": _overrides_or(overrides, "_run", lambda *a, **k: dict(_OK_RUN_RESULT)),
        "_checkout_develop_or_recover": _overrides_or(overrides, "_checkout_develop_or_recover", _fake_checkout_develop_or_recover),
        "git_push": _overrides_or(overrides, "git_push", _fake_git_push),
        "gh_pr_create": _overrides_or(overrides, "gh_pr_create", _fake_gh_pr_create),
        "record_blocking_interaction": _overrides_or(overrides, "record_blocking_interaction", _fake_record_blocking_interaction),
    }
    return [
        patch(f"agents.scrum_team.tools.workflow.{name}", side_effect=fn)
        for name, fn in targets.items()
    ]


def _script(marker: str) -> str:
    """A workbench script unique to the caller - see this file's own module
    docstring for why no two tests may ever share identical content."""
    return f"#!/bin/sh\n# {marker}\napt-get update && apt-get install -y default-jdk\n"


_A_RATIONALE = "US-0006 needs Gradle to actually run ./gradlew test."


class TestProposeFrameworkChange(unittest.TestCase):
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
            lambda: propose_framework_change(_script("rejects_missing_rationale"), [], "too short", tool_context=_make_tool_context())
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("rationale", result["message"])

    def test_requires_tool_context_state(self):
        result = self._run_with_patches(
            lambda: propose_framework_change(_script("requires_tool_context_state"), [], _A_RATIONALE, tool_context=None)
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("active session", result["message"])

    def test_creates_workbench_script_when_none_exists_yet(self):
        tc = _make_tool_context()
        result = self._run_with_patches(
            lambda: propose_framework_change(_script("creates_workbench_script_when_none_exists_yet"), [], _A_RATIONALE, tool_context=tc)
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposed"])

    def test_noop_when_script_matches_current_workbench_file(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context()
        script = _script("noop_when_script_matches_current_workbench_file")
        repo_root = base._configured_repo_root(tc)
        (repo_root / ".hc").mkdir(parents=True, exist_ok=True)
        (repo_root / ".hc" / "workbench-setup.sh").write_text(script, encoding="utf-8")

        result = self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc)
        )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["proposed"])
        self.assertEqual(tc.state["framework_change_proposal_count"], 0)

    def test_successful_proposal_writes_file_pushes_and_opens_draft_pr(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context()
        script = _script("successful_proposal_writes_file_pushes_and_opens_draft_pr")
        push_calls = []
        pr_calls = []

        def recording_git_push(branch, commit_message="chore: update", add_all=True, tool_context=None):
            push_calls.append({"branch": branch, "commit_message": commit_message, "add_all": add_all})
            return _fake_git_push(branch, commit_message, add_all, tool_context)

        def recording_gh_pr_create(title, body="", base=None, head=None, draft=False, head_is_resolved=False, tool_context=None):
            pr_calls.append({"title": title, "body": body, "base": base, "head": head, "draft": draft, "head_is_resolved": head_is_resolved})
            return _fake_gh_pr_create(title, body, base, head, draft, head_is_resolved, tool_context)

        result = self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc),
            git_push=recording_git_push,
            gh_pr_create=recording_gh_pr_create,
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposed"])
        self.assertTrue(result["branch"].startswith("framework/workbench-"))

        repo_root = base._configured_repo_root(tc)
        self.assertEqual((repo_root / ".hc" / "workbench-setup.sh").read_text(encoding="utf-8"), script)

        self.assertEqual(len(push_calls), 1)
        self.assertTrue(push_calls[0]["add_all"])
        self.assertEqual(len(pr_calls), 1)
        self.assertEqual(pr_calls[0]["title"], "Framework change proposal: workbench")
        self.assertTrue(pr_calls[0]["draft"])
        self.assertTrue(pr_calls[0]["head_is_resolved"])
        self.assertIn(_A_RATIONALE, pr_calls[0]["body"])
        self.assertNotIn("HUMAN OPERATOR ACTION REQUIRED", pr_calls[0]["body"])

    def test_successful_proposal_bumps_the_proposal_count_and_is_recorded(self):
        tc = _make_tool_context()
        script = _script("successful_proposal_bumps_the_proposal_count_and_is_recorded")
        self.assertEqual(tc.state.get("framework_change_proposal_count", 0), 0)

        self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc)
        )

        self.assertEqual(tc.state["framework_change_proposal_count"], 1)
        self.assertEqual(len(tc.state["framework_change_proposals"]), 1)
        proposal = tc.state["framework_change_proposals"][0]
        self.assertEqual(proposal["proposed_by"], "Architect")
        self.assertEqual(proposal["workbench_setup_script"], script)
        self.assertEqual(proposal["additional_allowlist_hosts"], [])
        self.assertIn("pr_url", proposal)

    def test_additional_allowlist_hosts_surface_in_pr_body_and_raise_a_blocking_interaction(self):
        tc = _make_tool_context()
        script = _script("additional_allowlist_hosts_surface_in_pr_body_and_raise_a_blocking_interaction")
        pr_calls = []
        blocking_calls = []

        def recording_gh_pr_create(title, body="", base=None, head=None, draft=False, head_is_resolved=False, tool_context=None):
            pr_calls.append(body)
            return _fake_gh_pr_create(title, body, base, head, draft, head_is_resolved, tool_context)

        def recording_blocking_interaction(kind, summary, detail="", tool_context=None):
            blocking_calls.append({"kind": kind, "summary": summary, "detail": detail})
            return _fake_record_blocking_interaction(kind, summary, detail, tool_context)

        result = self._run_with_patches(
            lambda: propose_framework_change(
                script, ["repo.maven.apache.org"], _A_RATIONALE, tool_context=tc
            ),
            gh_pr_create=recording_gh_pr_create,
            record_blocking_interaction=recording_blocking_interaction,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(pr_calls), 1)
        self.assertIn("HUMAN OPERATOR ACTION REQUIRED", pr_calls[0])
        self.assertIn("repo.maven.apache.org", pr_calls[0])
        self.assertIn("config/net-proxy/allowlist.filter", pr_calls[0])

        self.assertEqual(len(blocking_calls), 1)
        self.assertEqual(blocking_calls[0]["kind"], "framework_change")
        self.assertIn("repo.maven.apache.org", blocking_calls[0]["summary"])

    def test_no_blocking_interaction_when_no_new_hosts_requested(self):
        tc = _make_tool_context()
        script = _script("no_blocking_interaction_when_no_new_hosts_requested")
        blocking_calls = []

        def recording_blocking_interaction(kind, summary, detail="", tool_context=None):
            blocking_calls.append(kind)
            return _fake_record_blocking_interaction(kind, summary, detail, tool_context)

        self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc),
            record_blocking_interaction=recording_blocking_interaction,
        )

        self.assertEqual(blocking_calls, [])

    def test_checkout_develop_failure_returns_error_without_writing(self):
        from agents.scrum_team.tools import base

        tc = _make_tool_context()
        script = _script("checkout_develop_failure_returns_error_without_writing")

        def failing_checkout(repo_root, develop, tool_context=None):
            return {
                "status": "error",
                "fetch": dict(_OK_RUN_RESULT),
                "checkout": {"status": "error", "stderr": "network unreachable", "returncode": 1},
                "auto_integrated": None,
                "preserved_local_commits": None,
            }

        result = self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc),
            _checkout_develop_or_recover=failing_checkout,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("Could not check out", result["message"])

        repo_root = base._configured_repo_root(tc)
        workbench_path = repo_root / ".hc" / "workbench-setup.sh"
        if workbench_path.exists():
            self.assertNotEqual(workbench_path.read_text(encoding="utf-8"), script)

    def test_push_failure_returns_error(self):
        tc = _make_tool_context()
        script = _script("push_failure_returns_error")

        def failing_push(branch, commit_message="chore: update", add_all=True, tool_context=None):
            return {"status": "error", "branch": branch, "steps": {}}

        result = self._run_with_patches(
            lambda: propose_framework_change(script, [], _A_RATIONALE, tool_context=tc),
            git_push=failing_push,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("push", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
