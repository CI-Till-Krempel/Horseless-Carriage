# agents/scrum_team/tests/test_steering_proposals.py
"""
Tests for propose_steering_change (tools/workflow.py) - the sanctioned path
for an agent to propose an edit to one of this project's own workflow
steering documents (prompts.py, docs/DEVELOPMENT-WORKFLOW.md,
spec-templates/DOD.md/DOR.md) as a human-reviewed PR against Horseless-
Carriage's own repo, distinct from write_file (which only ever targets the
configured target/state repo).

Only the git/gh subprocess boundary (agents.scrum_team.tools.workflow._run)
is mocked, same convention as test_story_pipeline_state_machine.py's
_fake_run for github.py - everything else (path allowlist checks, the
rationale check, the own-prompt content guard, real file reads against this
actual checkout) runs for real. _project_root() always resolves to the real
Horseless-Carriage checkout even under pytest (see conftest.py's own note:
only _configured_repo_root is isolated per test) - propose_steering_change
never writes there directly regardless (all mutation happens inside a
throwaway git-worktree tempdir), so this is safe to exercise as-is.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.workflow import (
    propose_steering_change,
    _extract_prompt_constant,
    _diff_touches_own_prompt,
)
from agents.scrum_team.tools.base import _project_root
from agents.scrum_team.state import ScrumState


_OK_RUN_RESULT = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}


def _fake_run(cmd, cwd=None, tool_context=None, timeout=None, env_overrides=None):
    if cmd[:3] == ["git", "remote", "show"]:
        return {"status": "ok", "returncode": 0, "stdout": "  HEAD branch: main\n", "stderr": ""}
    return dict(_OK_RUN_RESULT)


def _make_tool_context(agent_name="ScrumMaster"):
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    tc.agent_name = agent_name
    return tc


PROMPTS_PATH = "agents/scrum_team/prompts.py"


class TestProposeSteeringChangeGuards(unittest.TestCase):
    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_rejects_path_outside_allowlist(self, _mock_run):
        result = propose_steering_change(
            "agents/scrum_team/agent.py", "print('hi')\n", "A real rationale explaining the need.",
            tool_context=_make_tool_context(),
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("can only target", result["message"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_rejects_missing_or_too_short_rationale(self, _mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        result = propose_steering_change(PROMPTS_PATH, current, "too short", tool_context=_make_tool_context())
        self.assertEqual(result["status"], "error")
        self.assertIn("rationale", result["message"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_rejects_during_eval_run(self, _mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        with patch.dict(os.environ, {"EVAL_RUN_ID": "test-run-1"}):
            result = propose_steering_change(
                PROMPTS_PATH, current + "\n# harmless trailing comment\n",
                "A real rationale explaining the need for this change.",
                tool_context=_make_tool_context(),
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("eval run", result["message"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_rejects_path_traversal_attempt(self, _mock_run):
        result = propose_steering_change(
            "agents/scrum_team/../../../etc/passwd", "irrelevant",
            "A real rationale explaining the need for this change.",
            tool_context=_make_tool_context(),
        )
        self.assertEqual(result["status"], "error")

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_noop_when_new_content_matches_current(self, _mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        result = propose_steering_change(PROMPTS_PATH, current, "A real rationale explaining the need for this change.",
                                          tool_context=_make_tool_context())
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["proposed"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_refuses_role_editing_its_own_prompt(self, _mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        sm_body = _extract_prompt_constant(current, "SM_PROMPT")
        self.assertIsNotNone(sm_body)
        mutated = current.replace(sm_body, sm_body + "\nExtra rogue instruction.\n")

        result = propose_steering_change(
            PROMPTS_PATH, mutated, "A real rationale explaining the need for this change.",
            tool_context=_make_tool_context(agent_name="ScrumMaster"),
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("own system prompt", result["message"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_allows_role_editing_a_different_roles_prompt(self, _mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        po_body = _extract_prompt_constant(current, "PO_PROMPT")
        self.assertIsNotNone(po_body)
        mutated = current.replace(po_body, po_body + "\nExtra clarified instruction.\n")

        result = propose_steering_change(
            PROMPTS_PATH, mutated, "A real rationale explaining the need for this change.",
            tool_context=_make_tool_context(agent_name="ScrumMaster"),
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposed"])

    @patch("agents.scrum_team.tools.workflow._run", side_effect=_fake_run)
    def test_successful_proposal_pushes_and_opens_draft_pr(self, mock_run):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        po_body = _extract_prompt_constant(current, "PO_PROMPT")
        mutated = current.replace(po_body, po_body + "\nExtra clarified instruction.\n")

        result = propose_steering_change(
            PROMPTS_PATH, mutated, "A real rationale explaining the need for this change.",
            tool_context=_make_tool_context(agent_name="ScrumMaster"),
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["branch"].startswith("steering/scrummaster-prompts-"))

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(any(c[:2] == ["git", "worktree"] and c[2] == "add" for c in commands))
        self.assertTrue(any(c[:2] == ["git", "push"] for c in commands))
        self.assertTrue(any(c[:3] == ["gh", "pr", "create"] and "--draft" in c for c in commands))
        self.assertTrue(any(c[:3] == ["git", "worktree", "remove"] for c in commands))

    def test_requires_tool_context_state(self):
        current = (_project_root() / PROMPTS_PATH).read_text(encoding="utf-8")
        result = propose_steering_change(
            PROMPTS_PATH, current + "\n# trivial change\n",
            "A real rationale explaining the need for this change.",
            tool_context=None,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("active session", result["message"])


class TestDiffTouchesOwnPrompt(unittest.TestCase):
    def test_none_agent_name_never_matches(self):
        self.assertFalse(_diff_touches_own_prompt("SM_PROMPT = \"\"\"a\"\"\"", "SM_PROMPT = \"\"\"b\"\"\"", None))

    def test_unknown_role_never_matches(self):
        self.assertFalse(_diff_touches_own_prompt("SM_PROMPT = \"\"\"a\"\"\"", "SM_PROMPT = \"\"\"b\"\"\"", "SomeNewRole"))

    def test_detects_change_to_own_constant(self):
        current = 'SM_PROMPT = """a"""\nPO_PROMPT = """x"""\n'
        new = 'SM_PROMPT = """b"""\nPO_PROMPT = """x"""\n'
        self.assertTrue(_diff_touches_own_prompt(current, new, "ScrumMaster"))

    def test_ignores_change_to_other_role_constant(self):
        current = 'SM_PROMPT = """a"""\nPO_PROMPT = """x"""\n'
        new = 'SM_PROMPT = """a"""\nPO_PROMPT = """y"""\n'
        self.assertFalse(_diff_touches_own_prompt(current, new, "ScrumMaster"))


if __name__ == "__main__":
    unittest.main()
