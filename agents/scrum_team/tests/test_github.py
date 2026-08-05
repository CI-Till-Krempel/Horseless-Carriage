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
    _git_push_impl,
    repo_status,
    create_release_pr,
    configure_github_repo,
    start_feature_branch,
    create_sprint_backlog_pr,
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
        mock_run.return_value = {"status": "ok", "returncode": 0}
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
    def test_git_push_impl_allow_protected_escape_hatch(self, mock_run):
        """
        Acceptance Criteria (ISSUE-0006): seed_repository's initial bootstrap
        commit is the one legitimate exception - allow_protected=True lets
        it through. Only reachable via _git_push_impl (internal Python code,
        never an agent tool call) - see the next two tests.
        """
        mock_run.return_value = {"status": "ok", "returncode": 0}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main"}

        result = _git_push_impl(branch="main", allow_protected=True, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_called()

    def test_git_push_tool_has_no_allow_protected_parameter(self):
        """
        Acceptance Criteria: a real ADK eval run showed a live model, under
        enough user pressure ("skip the PR, we need this live right now"),
        choosing to call git_push with allow_protected=True itself - if that
        parameter exists on the tool exposed to agents at all, a
        sufficiently persuasive prompt can talk a model into using it. It
        must not be settable through the public git_push tool at all,
        regardless of prompt wording - only through _git_push_impl, which is
        never registered as a tool for any role.
        """
        import inspect
        assert "allow_protected" not in inspect.signature(git_push).parameters

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_tool_always_refuses_protected_branch_even_under_pressure(self, mock_run):
        """Same scenario as the eval failure, exercised directly: git_push
        (the tool) has no way to bypass the protected-branch guard at all."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main"}

        result = git_push(branch="main", commit_message="skip PR to push live right now", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_refuses_refspec_disguised_as_a_branch_name(self, mock_run):
        """
        Acceptance Criteria (GH issue #104): branch="HEAD:main" never equals
        the protected-branch string "main", so the exact-string protected
        check alone doesn't catch it - but `git push origin HEAD:main` would
        still push current HEAD straight onto main. Reject anything shaped
        like a refspec (or containing any other non-branch-name character)
        before the protected-branch check even runs, and before any git
        command is invoked at all.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main"}

        result = git_push(branch="HEAD:main", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_refuses_branch_names_with_shell_metacharacters(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        for bad_branch in ["feature; rm -rf /", "feature branch", "feature~1", "feature?", "feature*"]:
            result = git_push(branch=bad_branch, tool_context=tool_context)
            self.assertEqual(result["status"], "error", f"expected refusal for {bad_branch!r}")

        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_still_allows_normal_branch_names(self, mock_run):
        mock_run.return_value = {"status": "ok", "returncode": 0}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        for ok_branch in ["feature/foo-bar_1", "US-0042-do-the-thing", "release/1.2.3"]:
            result = git_push(branch=ok_branch, tool_context=tool_context)
            self.assertEqual(result["status"], "ok", f"expected success for {ok_branch!r}")

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_stops_if_checkout_fails(self, mock_run):
        """
        Acceptance Criteria (GH issue #104): a failed `git checkout -B`
        must be fatal, not silently discarded - continuing to commit/push
        afterward would operate on whatever branch was already checked out,
        not the caller's intended target.
        """
        mock_run.return_value = {"status": "error", "stderr": "fatal: some checkout failure"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = git_push(branch="feature-branch", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_run.assert_called_once_with(
            ["git", "checkout", "-B", "feature-branch"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_reports_error_when_commit_fails_for_a_real_reason(self, mock_run):
        """
        Acceptance Criteria (GH issue #115): a `git commit` failure for any
        reason other than "nothing to commit" must be fatal - previously
        the final status was derived only from the push result, so if the
        remote happened to already be up to date, `git push` exits 0
        ("Everything up-to-date") and git_push reported "ok" even though
        the intended commit never actually happened anywhere.
        """
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "checkout"]:
                return {"status": "ok", "returncode": 0}
            if cmd[:2] == ["git", "add"]:
                return {"status": "ok", "returncode": 0}
            if cmd[:2] == ["git", "commit"]:
                return {"status": "error", "returncode": 1, "stderr": "fatal: unable to write new_index file"}
            if cmd[:2] == ["git", "push"]:
                return {"status": "ok", "returncode": 0}
            raise AssertionError(f"unexpected command: {cmd}")
        mock_run.side_effect = fake_run

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = git_push(branch="feature-branch", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("commit failed", result["message"])
        # The push step must never have been reached.
        push_calls = [c for c in mock_run.call_args_list if c.args[0][:2] == ["git", "push"]]
        self.assertEqual(push_calls, [])

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_retries_as_empty_commit_when_nothing_to_commit(self, mock_run):
        """The one legitimate case a failed `git commit` should NOT be
        fatal: nothing changed, so an empty commit is made instead to still
        get the branch pushed - unchanged behavior from before this issue."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["git", "commit"] and "--allow-empty" not in cmd:
                return {"status": "error", "returncode": 1, "stderr": "nothing to commit, working tree clean"}
            return {"status": "ok", "returncode": 0}
        mock_run.side_effect = fake_run

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = git_push(branch="feature-branch", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("--allow-empty" in c for c in calls))
        self.assertTrue(any(c[:2] == ["git", "push"] for c in calls))

    @patch("agents.scrum_team.tools.github._run")
    def test_git_push_reports_error_when_empty_commit_retry_also_fails(self, mock_run):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "commit"]:
                return {"status": "error", "returncode": 1, "stderr": "nothing to commit, working tree clean"}
            return {"status": "ok", "returncode": 0}
        mock_run.side_effect = fake_run

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = git_push(branch="feature-branch", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        push_calls = [c for c in mock_run.call_args_list if c.args[0][:2] == ["git", "push"]]
        self.assertEqual(push_calls, [])

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

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    def test_create_release_pr_rejection_records_blocking_interaction(self, mock_gh_pr_create):
        """
        Acceptance Criteria (GH issue #53): a release PR rejected for lack
        of a fresh human approval is exactly the "absolutely necessary
        human feedback" case that must be recorded (and notified on), not
        just left as a tool error return the calling agent might not
        relay to the human.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        create_release_pr(title="Release", body="body", tool_context=tool_context)

        self.assertEqual(len(tool_context.state["blocking_interactions"]), 1)
        interaction = tool_context.state["blocking_interactions"][0]
        self.assertEqual(interaction["kind"], "approval")
        self.assertIn("Release", interaction["summary"])
        self.assertFalse(interaction["resolved"])

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


class TestCreateSprintBacklogPr(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #171): Product Owner's sprint-planning
    writes (roadmap/PRD/epics/stories) only ever hit disk, never git - the
    first subsequent push (normally DevTeam's start_feature_branch) swept
    them onto a feature branch instead of develop. create_sprint_backlog_pr
    commits+pushes them to their own branch, opens a "Sprint Backlog #<N>"
    PR against develop, and merges it immediately.
    """

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_without_a_started_sprint(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 0}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("start_sprint", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_happy_path_opens_and_merges_against_develop(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/3"}
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 3, "repo": {"default_branch": "main", "develop_branch": "develop"}}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sprint_number"], 3)
        self.assertEqual(result["branch"], "sprint-backlog/3")
        mock_git_push.assert_called_once_with(
            branch="sprint-backlog/3", commit_message="chore: sprint 3 backlog", tool_context=tool_context,
        )
        mock_gh_pr_create.assert_called_once_with(
            title="Sprint Backlog #3",
            body=unittest.mock.ANY,
            base="develop",
            head="sprint-backlog/3",
            head_is_resolved=True,
            tool_context=tool_context,
        )
        # Final call is the merge - no explicit PR number needed, it
        # resolves from the currently checked-out branch (same convention
        # as merge_story_pr(pr_id=None)).
        mock_run.assert_called_with(
            ["gh", "pr", "merge", "--merge", "--admin"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run", return_value={"status": "error", "stderr": "no such ref"})
    def test_reports_error_when_develop_checkout_fails(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("develop", result["message"])

    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "error", "branch": "sprint-backlog/1"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_reports_error_when_push_fails(self, mock_run, mock_git_push):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("push", result["message"].lower())

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "error", "stderr": "already exists"})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "ok", "branch": "sprint-backlog/1"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_reports_error_when_pr_create_fails(self, mock_run, mock_git_push, mock_gh_pr_create):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Sprint Backlog PR", result["message"])
        # Never reaches the merge step if the PR was never opened.
        mock_run.assert_called_with(["git", "checkout", "-B", "sprint-backlog/1"], cwd=unittest.mock.ANY, tool_context=tool_context)


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
