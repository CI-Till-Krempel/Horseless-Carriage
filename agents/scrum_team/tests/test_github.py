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
    configure_github_repo,
    _diff_release_against_sprint_tracking,
    _stage_sprint_tracked_changes,
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
    def test_gh_pr_create_defaults_base_to_configured_default_branch(self, mock_run):
        """
        Acceptance Criteria (eval harness): omitting `base` must use the
        configured default branch (see _default_push_branch), not a
        hardcoded "main" - so an isolated eval run's PRs target its own
        branch via GITHUB_REPO_BRANCH.
        """
        mock_run.return_value = {"status": "ok", "stdout": "https://github.com/owner/repo/pull/2"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch.dict("os.environ", {"GITHUB_REPO_BRANCH": "eval/run-1"}, clear=True):
            gh_pr_create(title="Eval PR", head="feature-branch", tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "create", "--base", "eval/run-1", "--title", "Eval PR", "--head", "feature-branch"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_configure_github_repo_does_not_clobber_configured_branch_with_main(self, mock_run):
        """
        Acceptance Criteria (eval harness): if the caller omits
        default_branch, configure_github_repo must not silently reset the
        repo's configured default branch back to a hardcoded "main" -
        it should fall back to GITHUB_REPO_BRANCH like everything else.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch.dict("os.environ", {"GITHUB_REPO_BRANCH": "eval/run-1"}, clear=True):
            with patch("agents.scrum_team.tools.github._configured_repo_root") as mock_root:
                mock_root.return_value.__truediv__.return_value.exists.return_value = True
                result = configure_github_repo("git@github.com:owner/repo.git", tool_context=tool_context)

        self.assertEqual(result["repo"]["default_branch"], "eval/run-1")

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
          surfaces any mismatch as a clear warning, without failing the
          release outright (US-0011 selectively stages/flags instead of
          hard-blocking).
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

    @patch("agents.scrum_team.tools.github._run")
    def test_stage_sprint_tracked_changes_stages_tracked_files(self, mock_run):
        """
        Acceptance Criteria (US-0011):
        - Uncommitted changes that match sprint_files_touched are staged
          (included) rather than left behind.
        """
        mock_run.return_value = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}
        diff_check = {
            "tracked_files": ["specs/stories/US-0011-Foo.md"],
            "changed_files": ["specs/stories/US-0011-Foo.md"],
        }

        result = _stage_sprint_tracked_changes(diff_check, tool_context=MagicMock())

        self.assertEqual(result["staged_files"], ["specs/stories/US-0011-Foo.md"])
        self.assertEqual(result["flagged_for_review"], [])
        self.assertEqual(result["warnings"], [])
        mock_run.assert_called_once_with(
            ["git", "add", "--", "specs/stories/US-0011-Foo.md"],
            cwd=unittest.mock.ANY,
            tool_context=unittest.mock.ANY,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_stage_sprint_tracked_changes_flags_stray_changes(self, mock_run):
        """
        Acceptance Criteria (US-0011 edge case):
        - Uncommitted changes unrelated to this sprint's tracked files are
          flagged for human review, not silently staged/committed.
        """
        diff_check = {
            "tracked_files": [],
            "changed_files": ["notes/stray-local-edit.md"],
        }

        result = _stage_sprint_tracked_changes(diff_check, tool_context=MagicMock())

        self.assertEqual(result["staged_files"], [])
        self.assertEqual(result["flagged_for_review"], ["notes/stray-local-edit.md"])
        self.assertTrue(any("stray-local-edit.md" in w for w in result["warnings"]))
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run")
    def test_create_release_pr_stages_selectively_and_does_not_add_all(self, mock_run, mock_git_push, mock_gh_pr_create):
        """
        Acceptance Criteria (US-0011):
        - create_release_pr() stages sprint-tracked changes itself and
          calls git_push with add_all=False, so stray uncommitted changes
          aren't swept in by a blanket `git add -A`.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": " M specs/stories/US-0011-Foo.md\n?? notes/stray.md",
            "stderr": "",
        }
        mock_git_push.return_value = {"status": "ok"}
        mock_gh_pr_create.return_value = {"status": "ok", "stdout": "https://github.com/owner/repo/pull/1"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["sprint_files_touched"] = ["specs/stories/US-0011-Foo.md"]

        result = create_release_pr(title="Release", body="body", tool_context=tool_context)

        self.assertEqual(result["staged_files"], ["specs/stories/US-0011-Foo.md"])
        self.assertEqual(result["flagged_for_review"], ["notes/stray.md"])
        self.assertTrue(any("stray.md" in w for w in result["warnings"]))
        mock_git_push.assert_called_once_with(
            branch="release/increment",
            commit_message="chore: Release",
            add_all=False,
            tool_context=tool_context,
        )


if __name__ == "__main__":
    unittest.main()