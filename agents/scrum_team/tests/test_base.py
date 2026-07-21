# agents/scrum_team/tests/test_base.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.base import _hc_version, _default_push_branch


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


if __name__ == "__main__":
    unittest.main()
