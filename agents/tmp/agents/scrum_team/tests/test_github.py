# agents/scrum_team/tests/test_github.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.github import (
    gh_pr_create,
    gh_pr_status,
    gh_pr_checks,
    gh_pr_comment,
    gh_pr_review,
    gh_release_create,
    git_push,
)
from ..state import ScrumState


class TestGitHubTools(unittest.TestCase):
    @patch("subprocess.run")
    def test_gh_pr_create(self, mock_run):
        """
        Acceptance Criteria:
        - A pull request is created with the specified title and body.
        """
        mock_run.return_value.stdout = "https://github.com/owner/repo/pull/1"
        state = ScrumState()
        pr_url = gh_pr_create("New Feature", "This is a new feature.", "feature-branch", "main", state)
        self.assertEqual(pr_url, "https://github.com/owner/repo/pull/1")

    @patch("subprocess.run")
    def test_gh_pr_status(self, mock_run):
        """
        Acceptance Criteria:
        - The status of a pull request is retrieved.
        """
        mock_run.return_value.stdout = "Open"
        state = ScrumState()
        status = gh_pr_status("https://github.com/owner/repo/pull/1", state)
        self.assertEqual(status, "Open")

    @patch("subprocess.run")
    def test_gh_pr_checks(self, mock_run):
        """
        Acceptance Criteria:
        - The status of checks on a pull request is retrieved.
        """
        mock_run.return_value.stdout = "success"
        state = ScrumState()
        checks = gh_pr_checks("https://github.com/owner/repo/pull/1", state)
        self.assertEqual(checks, "success")

    @patch("subprocess.run")
    def test_gh_pr_comment(self, mock_run):
        """
        Acceptance Criteria:
        - A comment is added to a pull request.
        """
        state = ScrumState()
        gh_pr_comment("This is a comment.", "https://github.com/owner/repo/pull/1", state)
        mock_run.assert_called_with(
            ["gh", "pr", "comment", "https://github.com/owner/repo/pull/1", "--body", "This is a comment."],
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_gh_pr_review(self, mock_run):
        """
        Acceptance Criteria:
        - A review is submitted for a pull request.
        """
        state = ScrumState()
        gh_pr_review("This is a review.", "https://github.com/owner/repo/pull/1", "approve", state)
        mock_run.assert_called_with(
            ["gh", "pr", "review", "https://github.com/owner/repo/pull/1", "--body", "This is a review.", "--approve"],
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_gh_release_create(self, mock_run):
        """
        Acceptance Criteria:
        - A new release is created.
        """
        state = ScrumState()
        gh_release_create("v1.0.0", "Initial release", state)
        mock_run.assert_called_with(
            ["gh", "release", "create", "v1.0.0", "--title", "v1.0.0", "--notes", "Initial release"],
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_git_push(self, mock_run):
        """
        Acceptance Criteria:
        - Changes are pushed to the remote repository.
        """
        state = ScrumState()
        git_push("feature-branch", state)
        mock_run.assert_called_with(
            ["git", "push", "origin", "feature-branch"],
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()