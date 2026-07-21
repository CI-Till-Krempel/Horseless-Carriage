# agents/scrum_team/tests/test_github.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.github import (
    gh_pr_create,
    gh_pr_status,
    gh_pr_checks,
    gh_pr_comment,
    gh_pr_review,
    gh_release_create,
    git_push,
    repo_status,
    create_release_pr,
    _diff_release_against_sprint_tracking,
)
from agents.scrum_team.state import ScrumState


class TestGitHubTools(unittest.TestCase):
    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_create(self, mock_run):
        """
        Acceptance Criteria:
        - A pull request is created with the specified title and body.
        """
        mock_run.return_value = {"status": "ok", "stdout": "https://github.com/owner/repo/pull/1"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        pr_url = gh_pr_create(title="New Feature", body="This is a new feature.", head="feature-branch", base="main", tool_context=tool_context)
        self.assertEqual(pr_url["stdout"], "https://github.com/owner/repo/pull/1")

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_status(self, mock_run):
        """
        Acceptance Criteria:
        - The status of a pull request is retrieved.
        """
        mock_run.return_value = {"status": "ok", "stdout": "Open"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        status = gh_pr_status(tool_context=tool_context)
        self.assertEqual(status["stdout"], "Open")

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_checks(self, mock_run):
        """
        Acceptance Criteria:
        - The status of checks on a pull request is retrieved.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        checks = gh_pr_checks(tool_context=tool_context)
        self.assertEqual(checks["status"], "ok")

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comment(self, mock_run):
        """
        Acceptance Criteria:
        - A comment is added to a pull request.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.agent_name = "TestAgent"
        gh_pr_comment(body="This is a comment.", tool_context=tool_context)
        mock_run.assert_called_with(
            ["gh", "pr", "comment", "--body", "**TestAgent:** This is a comment."],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_review(self, mock_run):
        """
        Acceptance Criteria:
        - A review is submitted for a pull request.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.agent_name = "TestAgent"
        gh_pr_review(body="This is a review.", event="APPROVE", tool_context=tool_context)
        mock_run.assert_called_with(
            ["gh", "pr", "review", "--body", "**TestAgent:** This is a review.", "--approve"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_release_create(self, mock_run):
        """
        Acceptance Criteria:
        - A new release is created.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        gh_release_create(tag="v1.0.0", title="Initial release", tool_context=tool_context)
        mock_run.assert_called_with(
            ["gh", "release", "create", "v1.0.0", "--title", "Initial release"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push(self, mock_run):
        """
        Acceptance Criteria:
        - Changes are pushed to the remote repository.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        git_push(branch="feature-branch", tool_context=tool_context)
        mock_run.assert_called_with(
            ["git", "push", "-u", "origin", "feature-branch"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    def test_repo_status(self):
        """
        Acceptance Criteria:
        - repo_status returns a dictionary with diagnostic information.
        - The NameError: os is not defined is resolved.
        - env_config is included in the output.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        with patch.dict("os.environ", {"GITHUB_REPO_URL": "test_url"}):
            res = repo_status(tool_context=tool_context)
            self.assertEqual(res["status"], "ok")
            self.assertIn("diagnostics", res)
            self.assertIn("env_repo_url_present", res["diagnostics"])
            self.assertEqual(res["env_config"]["url"], "test_url")

    @patch("agents.scrum_team.tools.github._run")
    def test_diff_release_against_sprint_tracking_matches_exactly(self, mock_run):
        """
        Acceptance Criteria (US-0010 edge case):
        - Tracked files and the real git diff match exactly - no warnings.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": " M specs/stories/US-0010-Foo.md\n?? specs/ROADMAP.md",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_files_touched"] = ["specs/stories/US-0010-Foo.md", "specs/ROADMAP.md"]

        result = _diff_release_against_sprint_tracking(tool_context=tool_context)

        self.assertTrue(result["matched"])
        self.assertEqual(result["warnings"], [])

    @patch("agents.scrum_team.tools.github._run")
    def test_diff_release_against_sprint_tracking_flags_missing_file(self, mock_run):
        """
        Acceptance Criteria (US-0010):
        - A tracked file absent from the real diff triggers a clear warning.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": "",  # nothing actually changed in git
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_files_touched"] = ["specs/stories/US-0010-Foo.md"]

        result = _diff_release_against_sprint_tracking(tool_context=tool_context)

        self.assertFalse(result["matched"])
        self.assertTrue(any("missing from the release diff" in w for w in result["warnings"]))
        self.assertIn("specs/stories/US-0010-Foo.md", result["warnings"][0])

    @patch("agents.scrum_team.tools.github._run")
    def test_diff_release_against_sprint_tracking_flags_extra_file(self, mock_run):
        """
        Acceptance Criteria (US-0010):
        - A real diff entry not tracked as sprint-touched triggers a clear
          warning (the "vice versa" case).
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": "?? specs/stories/UNTRACKED.md",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_files_touched"] = []

        result = _diff_release_against_sprint_tracking(tool_context=tool_context)

        self.assertFalse(result["matched"])
        self.assertTrue(any("not tracked as sprint-touched" in w for w in result["warnings"]))
        self.assertIn("specs/stories/UNTRACKED.md", result["warnings"][0])

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run")
    def test_create_release_pr_surfaces_mismatch_warning_without_blocking(self, mock_run, mock_git_push, mock_gh_pr_create):
        """
        Acceptance Criteria (US-0010):
        - create_release_pr() runs the sprint-tracking diff check and
          surfaces any mismatch as a clear warning, without blocking the
          release (blocking is US-0011's concern).
        """
        mock_run.return_value = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}
        mock_git_push.return_value = {"status": "ok"}
        mock_gh_pr_create.return_value = {"status": "ok", "stdout": "https://github.com/owner/repo/pull/1"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_files_touched"] = ["specs/stories/US-0010-Foo.md"]

        result = create_release_pr(title="Release", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_git_push.assert_called_once()
        mock_gh_pr_create.assert_called_once()
        self.assertTrue(len(result["warnings"]) > 0)
        self.assertFalse(result["sprint_tracking_check"]["matched"])


if __name__ == "__main__":
    unittest.main()