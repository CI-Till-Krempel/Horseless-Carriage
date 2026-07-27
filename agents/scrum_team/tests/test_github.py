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
    start_feature_branch,
    mark_pr_ready_for_review,
    merge_story_pr,
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
    def test_gh_pr_create_head_is_resolved_skips_eval_prefixing(self, mock_run):
        """
        Acceptance Criteria (GitFlow): a caller passing an already-resolved
        branch name (e.g. develop/main, which bake any eval run id directly
        into the value rather than via _with_eval_branch_prefix) must not
        have it re-tagged with the ad-hoc "eval-<run-id>/" prefix - that
        would double-prefix a value like "eval/run-1/develop" into
        "eval-run-1/eval/run-1/develop".
        """
        mock_run.return_value = {"status": "ok", "stdout": "https://github.com/owner/repo/pull/3"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch.dict("os.environ", {"EVAL_RUN_ID": "run-1"}, clear=True):
            gh_pr_create(
                title="Sprint PR", base="eval/run-1/main", head="eval/run-1/develop",
                head_is_resolved=True, tool_context=tool_context,
            )

        mock_run.assert_called_once_with(
            ["gh", "pr", "create", "--base", "eval/run-1/main", "--title", "[eval-run-1] Sprint PR", "--head", "eval/run-1/develop"],
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

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_refuses_to_push_directly_to_protected_branch(self, mock_run):
        """
        Acceptance Criteria (ISSUE-0006): git_push refuses a push straight
        to the configured default branch instead of running it.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main"}

        result = git_push(branch="main", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_refuses_to_push_directly_to_develop_branch(self, mock_run):
        """
        Acceptance Criteria (GitFlow): the protected-branch guard covers
        both main AND develop - only feature branches get pushed to
        directly, everything else reaches develop/main via a PR merge.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main", "develop_branch": "develop"}

        result = git_push(branch="develop", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_allow_protected_escape_hatch(self, mock_run):
        """
        Acceptance Criteria (ISSUE-0006): seed_repository's initial bootstrap
        commit is the one legitimate exception - allow_protected=True lets
        it through.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main"}

        result = git_push(branch="main", allow_protected=True, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_review_records_pr_review_call(self, mock_run):
        """
        Acceptance Criteria (ISSUE-0005): a successful gh_pr_review call is
        counted per calling role, so advance_story_stage's Reviewed/Tested
        gates can tell a claimed stage apart from an actual review.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.agent_name = "Architect"

        gh_pr_review(body="Looks good", tool_context=tool_context)

        self.assertEqual(tool_context.state["pr_review_calls"]["Architect"], 1)

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comment_records_pr_review_call(self, mock_run):
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.agent_name = "QA"

        gh_pr_comment(body="Found an issue", tool_context=tool_context)

        self.assertEqual(tool_context.state["pr_review_calls"]["QA"], 1)

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

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    def test_create_release_pr_rejects_without_fresh_release_approval(self, mock_gh_pr_create):
        """
        Acceptance Criteria (ISSUE-0001): create_release_pr refuses without
        a fresh record_human_approval("release", ...) since the last one.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = create_release_pr(title="Release", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_gh_pr_create.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_create_release_pr_requires_no_approval_at_ceo_and_eval_levels(
        self, mock_run, mock_gh_pr_create
    ):
        """
        Acceptance Criteria (interaction levels, see docs/INTERACTION-LEVELS.md): CEO and EVAL
        levels don't gate create_release_pr on any human approval at all.
        """
        for level in ("CEO", "EVAL"):
            with patch.dict("os.environ", {"INTERACTION_LEVEL": level}, clear=True):
                tool_context = MagicMock()
                tool_context.state = ScrumState().model_dump()
                result = create_release_pr(title="Release", body="body", tool_context=tool_context)
                self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_create_release_pr_opens_develop_to_main_sprint_pr(self, mock_run, mock_gh_pr_create):
        """
        Acceptance Criteria (GitFlow): create_release_pr is the sprint PR -
        it opens develop -> main (or their eval-run-resolved equivalents),
        with head marked as already-resolved so it isn't double-prefixed.
        There's no local diff to push/stage anymore - content already
        landed on develop via merged feature-branch PRs.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["human_approvals"] = [{"type": "release", "note": "reviewed"}]
        tool_context.state["repo"] = {"default_branch": "main", "develop_branch": "develop"}

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_gh_pr_create.assert_called_once_with(
            title="Sprint 1", body="body", base="main", head="develop",
            head_is_resolved=True, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "error", "message": "boom"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_create_release_pr_surfaces_pr_create_failure(self, mock_run, mock_gh_pr_create):
        """
        Acceptance Criteria: create_release_pr must not always report "ok" -
        a failed gh_pr_create (e.g. base doesn't exist on the remote yet)
        has to be visible to the caller, not silently swallowed (see
        0.1.0-run4 in RELEASE.md).
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["human_approvals"] = [{"type": "release", "note": "reviewed"}]

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertFalse(tool_context.state["sprint_report_pending_release"])


class TestStartFeatureBranch(unittest.TestCase):
    """
    Acceptance Criteria (GitFlow): start_feature_branch checks out+pulls
    develop, branches feature/<story_id>-<slug> off it, pushes, and opens a
    draft PR back into develop - recording the branch name in state.
    """

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_start_feature_branch_happy_path(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-1-add-login"}
        tool_context = MagicMock()
        tool_context.state = {"repo": {"default_branch": "main", "develop_branch": "develop"}}

        result = start_feature_branch("US-1", "Add Login!", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["branch"], "feature/US-1-add-login")
        mock_git_push.assert_called_once_with(
            branch="feature/US-1-add-login", commit_message="chore: start US-1", tool_context=tool_context,
        )
        mock_gh_pr_create.assert_called_once_with(
            title="US-1: Add Login!",
            body=unittest.mock.ANY,
            base="develop",
            head="feature/US-1-add-login",
            head_is_resolved=True,
            draft=True,
            tool_context=tool_context,
        )
        self.assertEqual(tool_context.state["active_feature_branches"]["US-1"], "feature/US-1-add-login")

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run")
    def test_start_feature_branch_slugifies_free_text(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_run.return_value = {"status": "ok"}
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-2-a-messy-slug-here"}
        mock_gh_pr_create.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = {}

        result = start_feature_branch("US-2", "A Messy Slug!! Here??", tool_context=tool_context)

        mock_git_push.assert_called_once_with(
            branch="feature/US-2-a-messy-slug-here", commit_message="chore: start US-2", tool_context=tool_context,
        )
        self.assertEqual(result["branch"], "feature/US-2-a-messy-slug-here")

    @patch("agents.scrum_team.tools.github._run")
    def test_start_feature_branch_reports_error_when_develop_checkout_fails(self, mock_run):
        mock_run.return_value = {"status": "error", "stderr": "no such ref"}
        tool_context = MagicMock()
        tool_context.state = {}

        result = start_feature_branch("US-3", "broken", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("develop", result["message"])


class TestMarkPrReadyForReview(unittest.TestCase):
    @patch("agents.scrum_team.tools.github._run")
    def test_mark_pr_ready_for_review_with_explicit_pr_id(self, mock_run):
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        mark_pr_ready_for_review(pr_id=42, tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "ready", "42"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_mark_pr_ready_for_review_defaults_to_current_branch(self, mock_run):
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        mark_pr_ready_for_review(tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "ready"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )


class TestMergeStoryPr(unittest.TestCase):
    @patch("agents.scrum_team.tools.github._run")
    def test_merge_story_pr_defaults_without_admin(self, mock_run):
        """
        Acceptance Criteria (GitFlow): a story-level merge respects real
        branch-protection/required-checks by default - --admin is opt-in
        only, unlike the eval harness's own forced-admin sprint-PR merges.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        merge_story_pr(tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "merge", "--merge"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_merge_story_pr_with_pr_id_and_admin(self, mock_run):
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        merge_story_pr(pr_id=7, admin=True, tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "merge", "7", "--merge", "--admin"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )


if __name__ == "__main__":
    unittest.main()
