# agents/scrum_team/tests/test_base.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.base import _hc_version, _default_push_branch, _develop_branch_name, _run, _redact_cmd
from agents.scrum_team.state import ScrumState


class TestHcVersion(unittest.TestCase):
    """
    Acceptance Criteria (release process, see RELEASE.md):
    - The running Horseless Carriage version is read from the committed
      VERSION file at the project root, not fabricated.
    """

    def test_hc_version_reads_real_version_file(self):
        version = _hc_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_hc_version_reports_unknown_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.scrum_team.tools.base._project_root", return_value=Path(tmp_dir)):
                self.assertEqual(_hc_version(), "unknown")


class TestDefaultPushBranch(unittest.TestCase):
    """
    Acceptance Criteria (team performance eval harness): seed_repository/
    gh_pr_create/create_release_pr must all target a configurable default
    branch, not a hardcoded "main", so an isolated eval/test run can point
    every push/PR at its own branch via GITHUB_REPO_BRANCH without
    contaminating the real default branch.
    """

    def test_defaults_to_main_with_nothing_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_default_push_branch(tool_context=None), "main")

    def test_uses_github_repo_branch_env_var(self):
        with patch.dict("os.environ", {"GITHUB_REPO_BRANCH": "eval/run-1"}, clear=True):
            self.assertEqual(_default_push_branch(tool_context=None), "eval/run-1")

    def test_prefers_state_configured_default_branch_over_env(self):
        tool_context = MagicMock()
        tool_context.state = {"repo": {"default_branch": "eval/run-2"}}
        with patch.dict("os.environ", {"GITHUB_REPO_BRANCH": "eval/run-1"}, clear=True):
            self.assertEqual(_default_push_branch(tool_context=tool_context), "eval/run-2")


class TestDevelopBranchName(unittest.TestCase):
    """
    Acceptance Criteria (GitFlow): _develop_branch_name mirrors
    _default_push_branch's resolution order exactly (state config ->
    GITHUB_DEVELOP_BRANCH env var -> "develop"), so an isolated eval/test
    run can point feature-branch PRs at its own develop branch without
    contaminating the real one.
    """

    def test_defaults_to_develop_with_nothing_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_develop_branch_name(tool_context=None), "develop")

    def test_uses_github_develop_branch_env_var(self):
        with patch.dict("os.environ", {"GITHUB_DEVELOP_BRANCH": "eval/run-1/develop"}, clear=True):
            self.assertEqual(_develop_branch_name(tool_context=None), "eval/run-1/develop")

    def test_prefers_state_configured_develop_branch_over_env(self):
        tool_context = MagicMock()
        tool_context.state = {"repo": {"develop_branch": "eval/run-2/develop"}}
        with patch.dict("os.environ", {"GITHUB_DEVELOP_BRANCH": "eval/run-1/develop"}, clear=True):
            self.assertEqual(_develop_branch_name(tool_context=tool_context), "eval/run-2/develop")


class TestRedactCmd(unittest.TestCase):
    """
    Acceptance Criteria (security review ahead of the public v0.1.0
    release, see SECURITY.md): the base64-encoded GitHub token _run()
    injects into `git -c http...extraheader=AUTHORIZATION: Basic <token>`
    must never come back out in a tool result, transcript, or log.
    """

    def test_redact_cmd_masks_authorization_header(self):
        cmd = [
            "git",
            "-c", "http.https://github.com/.extraheader=AUTHORIZATION: Basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0",
            "push",
        ]
        redacted = _redact_cmd(cmd)
        joined = " ".join(redacted)
        self.assertNotIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", joined)
        self.assertIn("AUTHORIZATION: Basic ***REDACTED***", joined)

    def test_redact_cmd_leaves_non_auth_args_untouched(self):
        cmd = ["git", "push", "-u", "origin", "main"]
        self.assertEqual(_redact_cmd(cmd), cmd)

    def test_run_never_leaks_token_via_returned_cmd(self):
        import base64
        token = "s3cr3t-token"
        auth_value = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["github_token"] = token

        with patch("subprocess.run") as mock_subprocess_run:
            mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _run(["git", "push", "-u", "origin", "main"], tool_context=tool_context)

        # The real subprocess call still gets the actual (unredacted) auth
        # header - the tool must actually authenticate.
        actual_cmd = mock_subprocess_run.call_args[0][0]
        self.assertIn(auth_value, str(actual_cmd))
        # ...but the metadata returned to the caller (and thus to any
        # transcript/log) must not contain the reversible base64 secret.
        self.assertNotIn(auth_value, str(result["cmd"]))


if __name__ == "__main__":
    unittest.main()
