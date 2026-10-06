# agents/scrum_team/tests/test_github.py
import os
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.github import (
    gh_pr_create,
    gh_pr_status,
    gh_pr_checks,
    gh_pr_comment,
    gh_pr_comments,
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
    integrate_open_changes,
    _checkout_develop_or_recover,
    _checkout_with_auto_integrate,
    _preserve_local_only_develop_commits,
    release_pr_still_open,
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
    def test_gh_pr_comments_reads_back_comments_and_reviews(self, mock_run):
        """
        Acceptance Criteria (user observation): agents had no way to read
        what another role already said on a PR, only whether the mechanical
        team-engagement gate (GH #357) considers them to have commented at
        all. gh_pr_comments is the read counterpart to gh_pr_comment/
        gh_pr_review.
        """
        import json
        mock_run.return_value = {
            "status": "ok",
            "stdout": json.dumps({
                "comments": [
                    {"author": {"login": "bot-account"}, "body": "**Architect:** LGTM.", "createdAt": "2026-10-01T00:00:00Z"},
                ],
                "reviews": [
                    {"author": {"login": "bot-account"}, "state": "APPROVED", "body": "**QA:** Approved.", "submittedAt": "2026-10-01T00:05:00Z"},
                ],
            }),
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = gh_pr_comments(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_called_with(
            ["gh", "pr", "view", "--json", "comments,reviews"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )
        self.assertEqual(len(result["comments"]), 1)
        self.assertEqual(result["comments"][0]["body"], "**Architect:** LGTM.")
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(result["reviews"][0]["body"], "**QA:** Approved.")
        self.assertEqual(result["reviews"][0]["state"], "APPROVED")

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comments_passes_through_an_explicit_pr_id(self, mock_run):
        import json
        mock_run.return_value = {"status": "ok", "stdout": json.dumps({"comments": [], "reviews": []})}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        gh_pr_comments(pr_id=42, tool_context=tool_context)

        mock_run.assert_called_with(
            ["gh", "pr", "view", "42", "--json", "comments,reviews"],
            cwd=unittest.mock.ANY,
            tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comments_empty_when_none_exist_yet(self, mock_run):
        import json
        mock_run.return_value = {"status": "ok", "stdout": json.dumps({"comments": [], "reviews": []})}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = gh_pr_comments(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["comments"], [])
        self.assertEqual(result["reviews"], [])

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comments_propagates_a_command_failure(self, mock_run):
        mock_run.return_value = {"status": "error", "stderr": "no pull requests found"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = gh_pr_comments(tool_context=tool_context)

        self.assertEqual(result["status"], "error")

    @patch("agents.scrum_team.tools.github._run")
    def test_gh_pr_comments_handles_unparseable_output(self, mock_run):
        mock_run.return_value = {"status": "ok", "stdout": "not json"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = gh_pr_comments(tool_context=tool_context)

        self.assertEqual(result["status"], "error")

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

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_allow_protected_resyncs_with_origin_before_committing(self, mock_run, mock_preserve):
        """
        Acceptance Criteria (GH issue #317): a plain `checkout -B <branch>`
        here (no origin sync at all) was the actual gap behind the
        roadmap-desync root cause - local commits piled up against a stale
        base while every push kept getting rejected non-fast-forward,
        growing worse every retry. When local hasn't diverged from origin
        (nothing to preserve), this must reset to origin/<branch> before
        committing, the same safe pattern _checkout_develop_or_recover uses.
        """
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "ok"},  # reset checkout -B develop origin/develop
            {"status": "ok"},  # add -A
            {"status": "ok", "returncode": 1},  # staged check (something staged)
            {"status": "ok", "returncode": 0},  # commit
            {"status": "ok", "returncode": 0},  # push
        ]
        mock_preserve.return_value = None
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main", "develop_branch": "develop"}

        result = _git_push_impl(branch="develop", allow_protected=True, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_any_call(["git", "fetch", "origin", "develop"], cwd=unittest.mock.ANY, tool_context=tool_context)
        mock_run.assert_any_call(["git", "checkout", "-B", "develop", "origin/develop"], cwd=unittest.mock.ANY, tool_context=tool_context)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_allow_protected_uses_plain_checkout_once_confirmed_in_sync(self, mock_run, mock_preserve):
        """Mirrors _checkout_develop_or_recover's own equivalent test - once
        local <develop>'s own unique commits have already been pushed (or
        merged-then-pushed) to match origin, the reset is redundant."""
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "ok"},  # plain checkout develop
            {"status": "ok"},  # add -A
            {"status": "ok", "returncode": 1},  # staged check
            {"status": "ok", "returncode": 0},  # commit
            {"status": "ok", "returncode": 0},  # push
        ]
        mock_preserve.return_value = {"status": "ok", "in_sync": True, "action": "pushed_local_ahead"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "main", "develop_branch": "develop"}

        result = _git_push_impl(branch="develop", allow_protected=True, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_any_call(["git", "checkout", "develop"], cwd=unittest.mock.ANY, tool_context=tool_context)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_non_protected_branch_with_no_remote_copy_skips_the_resync(self, mock_run, mock_preserve):
        """A brand-new feature branch (never pushed before) has nothing on
        origin to sync against yet - falls through to the plain checkout,
        same as before this branch ever needed a resync at all."""
        mock_run.side_effect = [
            {"status": "ok"},  # fetch origin <branch>
            {"status": "error", "returncode": 1},  # rev-parse --verify origin/<branch> - doesn't exist
            {"status": "ok"},  # checkout -B (no origin arg)
            {"status": "ok"},  # add -A
            {"status": "ok", "returncode": 1},  # staged check
            {"status": "ok", "returncode": 0},  # commit
            {"status": "ok", "returncode": 0},  # push
        ]
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _git_push_impl(branch="feature/US-0001-add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_preserve.assert_not_called()
        mock_run.assert_any_call(["git", "checkout", "-B", "feature/US-0001-add-login"], cwd=unittest.mock.ANY, tool_context=tool_context)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_non_protected_branch_resyncs_with_origin_when_a_remote_copy_exists(self, mock_run, mock_preserve):
        """
        Acceptance Criteria (GH eval run39): a feature branch has the exact
        same origin-divergence risk #317 fixed for protected branches - a
        real eval run saw one diverge by 26 commits because this path never
        synced against origin's own copy at all before committing on top of
        whatever the local working copy happened to have. When local hasn't
        diverged from origin (nothing to preserve), this must reset to
        origin/<branch> before committing, same as the protected-branch path.
        """
        mock_run.side_effect = [
            {"status": "ok"},  # fetch origin <branch>
            {"status": "ok", "returncode": 0},  # rev-parse --verify origin/<branch> - exists
            {"status": "ok"},  # reset checkout -B <branch> origin/<branch>
            {"status": "ok"},  # add -A
            {"status": "ok", "returncode": 1},  # staged check
            {"status": "ok", "returncode": 0},  # commit
            {"status": "ok", "returncode": 0},  # push
        ]
        mock_preserve.return_value = None
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _git_push_impl(branch="feature/US-0001-add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_any_call(
            ["git", "checkout", "-B", "feature/US-0001-add-login", "origin/feature/US-0001-add-login"],
            cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_non_protected_branch_uses_plain_checkout_once_confirmed_in_sync(self, mock_run, mock_preserve):
        """Mirrors the protected-branch path's own equivalent test - once
        local's own unique commits have already been pushed (or merged-
        then-pushed) to match origin, the reset is redundant."""
        mock_run.side_effect = [
            {"status": "ok"},  # fetch origin <branch>
            {"status": "ok", "returncode": 0},  # rev-parse --verify origin/<branch> - exists
            {"status": "ok"},  # plain checkout <branch>
            {"status": "ok"},  # add -A
            {"status": "ok", "returncode": 1},  # staged check
            {"status": "ok", "returncode": 0},  # commit
            {"status": "ok", "returncode": 0},  # push
        ]
        mock_preserve.return_value = {"status": "ok", "in_sync": True, "action": "pushed_local_ahead"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _git_push_impl(branch="feature/US-0001-add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_run.assert_any_call(
            ["git", "checkout", "feature/US-0001-add-login"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

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
        mock_run.assert_any_call(
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
            if cmd[:2] == ["git", "fetch"]:
                return {"status": "ok", "returncode": 0}
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return {"status": "error", "returncode": 1}  # no remote copy of this branch yet
            if cmd[:2] == ["git", "checkout"]:
                return {"status": "ok", "returncode": 0}
            if cmd[:2] == ["git", "add"]:
                return {"status": "ok", "returncode": 0}
            if cmd[:3] == ["git", "diff", "--cached"]:
                return {"status": "ok", "returncode": 1}  # something is staged (git add -A just ran)
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
    def test_git_push_retries_as_empty_commit_when_nothing_staged_despite_untracked_files_present(self, mock_run):
        """
        ISSUE-0050 follow-up / 0.1.0-run34: git prints a DIFFERENT message
        - "nothing added to commit but untracked files present" - instead of
        "nothing to commit, working tree clean" whenever there are any
        untracked files sitting around (e.g. .coverage/__pycache__/ left by
        a test run), even if nothing real is staged. The old code only ever
        string-matched the first message, so this second one hard-failed a
        real release every single sprint of a real eval run. A plumbing
        `git diff --cached --quiet` check (returncode 0 = nothing staged)
        catches this case too, without depending on git's message wording.
        """
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["git", "diff", "--cached"]:
                return {"status": "ok", "returncode": 0}  # nothing staged
            if cmd[:2] == ["git", "commit"] and "--allow-empty" not in cmd:
                return {
                    "status": "error", "returncode": 1,
                    "stdout": "nothing added to commit but untracked files present (use \"git add\" to track)",
                }
            return {"status": "ok", "returncode": 0}
        mock_run.side_effect = fake_run

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = git_push(branch="feature-branch", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("--allow-empty" in c for c in calls))
        # The plain (non-empty) `git commit` attempt is skipped entirely once
        # the plumbing check already knows nothing is staged - unlike the old
        # code, which always tried the real commit first and only fell back
        # to --allow-empty by parsing its failure text afterward.
        self.assertFalse(any(cmd[:2] == ["git", "commit"] and "--allow-empty" not in cmd for cmd in calls))

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


class TestCreateReleasePrLandsSprintReport(unittest.TestCase):
    """
    Acceptance Criteria: a real incident shipped a release PR with no
    sprint report or transcript in it at all, on a sprint that finished
    entirely within budget - create_sprint_report/_write_conversation_
    transcript only ever write specs/reports/*.md locally (see their own
    docstrings), onto whatever branch the working tree happened to be on,
    and nothing else committed them. create_release_pr must land both on
    develop, re-rendered from state, before opening the PR - but only
    when a sprint report actually exists to land (must not error out or
    change behavior otherwise).
    """

    def _tool_context(self, sprint_report=""):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["human_approvals"] = [{"type": "release", "note": "reviewed"}]
        tool_context.state["sprint_report"] = sprint_report
        return tool_context

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._git_push_impl")
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.budget._write_conversation_transcript")
    @patch("agents.scrum_team.tools.budget.render_fallback_sprint_report")
    @patch("agents.scrum_team.tools.github._checkout_develop_or_recover")
    def test_lands_report_and_transcript_before_opening_the_pr(
        self, mock_checkout, mock_render, mock_transcript, mock_integrate, mock_push, mock_run, mock_gh_pr_create,
    ):
        mock_checkout.return_value = {"status": "ok", "checkout": {"status": "ok"}, "fetch": {"status": "ok"}, "auto_integrated": None}
        mock_integrate.return_value = {"status": "ok", "integrated": True}
        mock_push.return_value = {"status": "ok"}
        tool_context = self._tool_context(sprint_report="real report content")

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_checkout.assert_called_once()
        mock_render.assert_called_once_with(tool_context=tool_context)
        mock_transcript.assert_called_once_with(tool_context=tool_context)
        # integrate_open_changes (scoped to specs/.hc/, see its own
        # docstring) - not add_all=True - sweeps in anything else this
        # sprint's grace-period work left dangling (a final update_roadmap/
        # upsert_issue/generate_workflow_diagram call), never a stray
        # unrelated file.
        mock_integrate.assert_called_once_with(tool_context=tool_context)
        mock_push.assert_called_once_with(
            branch="develop", commit_message=unittest.mock.ANY, add_all=False,
            allow_protected=True, tool_context=tool_context,
        )
        mock_gh_pr_create.assert_called_once()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch("agents.scrum_team.tools.github._checkout_develop_or_recover")
    def test_skips_landing_when_no_sprint_report_exists_yet(self, mock_checkout, mock_run, mock_gh_pr_create):
        """No create_sprint_report call happened at all this sprint (a
        separate, pre-existing gap this function isn't responsible for) -
        must behave exactly as before this fix, not error out."""
        tool_context = self._tool_context(sprint_report="")

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_checkout.assert_not_called()
        mock_gh_pr_create.assert_called_once()

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github._checkout_develop_or_recover")
    def test_a_checkout_failure_blocks_the_pr_instead_of_opening_it_without_the_report(
        self, mock_checkout, mock_gh_pr_create,
    ):
        mock_checkout.return_value = {
            "status": "error",
            "checkout": {"status": "error", "stderr": "local changes would be overwritten"},
            "fetch": {"status": "ok"}, "auto_integrated": {"status": "ok", "integrated": False},
        }
        tool_context = self._tool_context(sprint_report="real report content")

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("develop", result["message"])
        mock_gh_pr_create.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github._git_push_impl")
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.budget._write_conversation_transcript")
    @patch("agents.scrum_team.tools.budget.render_fallback_sprint_report")
    @patch("agents.scrum_team.tools.github._checkout_develop_or_recover")
    def test_a_push_failure_blocks_the_pr_instead_of_opening_it_without_the_report(
        self, mock_checkout, mock_render, mock_transcript, mock_integrate, mock_push, mock_gh_pr_create,
    ):
        mock_checkout.return_value = {"status": "ok", "checkout": {"status": "ok"}, "fetch": {"status": "ok"}, "auto_integrated": None}
        mock_integrate.return_value = {"status": "ok", "integrated": True}
        mock_push.return_value = {"status": "error", "message": "network error"}
        tool_context = self._tool_context(sprint_report="real report content")

        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        mock_gh_pr_create.assert_not_called()


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
        # sprint_number/sprint_backlog_pr_sprint must match - Dev Team
        # mechanically cannot start a feature branch until THIS sprint's
        # Sprint Backlog PR has actually merged (see sprint_backlog_pr_missing).
        tool_context.state = {
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
            "product_backlog": [{"id": "US-1", "title": "Add Login!"}],
        }

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
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
            "product_backlog": [{"id": "US-2", "title": "A Messy Slug!! Here??"}],
        }

        result = start_feature_branch("US-2", "A Messy Slug!! Here??", tool_context=tool_context)

        mock_git_push.assert_called_once_with(
            branch="feature/US-2-a-messy-slug-here", commit_message="chore: start US-2", tool_context=tool_context,
        )
        self.assertEqual(result["branch"], "feature/US-2-a-messy-slug-here")

    @patch("agents.scrum_team.tools.github._run")
    def test_start_feature_branch_reports_error_when_develop_checkout_fails(self, mock_run):
        mock_run.return_value = {"status": "error", "stderr": "no such ref"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
            "product_backlog": [{"id": "US-3", "title": "broken"}],
        }

        result = start_feature_branch("US-3", "broken", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("develop", result["message"])

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits", return_value=None)
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_start_feature_branch_self_heals_dangling_changes_on_its_own_checkout(
        self, mock_run, mock_integrate, mock_preserve, mock_git_push, mock_gh_pr_create
    ):
        """
        Acceptance Criteria (GH eval run41): a real eval run hit "local
        changes would be overwritten" specifically on start_feature_branch's
        OWN feature-branch checkout (a previous story's advance_story_stage
        roadmap update was still sitting uncommitted) - unlike the develop
        checkout right next to it, this one had no self-heal at all and
        DevTeam had to notice the failure and call integrate_open_changes
        itself before retrying by hand.
        """
        mock_run.side_effect = [
            {"status": "ok"},  # _checkout_develop_or_recover: fetch develop
            {"status": "ok"},  # _checkout_develop_or_recover: checkout develop
            {"status": "error", "stderr": "error: Your local changes to the following files would be overwritten by checkout"},
            {"status": "ok"},  # retried feature-branch checkout
        ]
        mock_integrate.return_value = {"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]}
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-4-resume-work"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
            "product_backlog": [{"id": "US-4", "title": "Resume Work"}],
        }

        result = start_feature_branch("US-4", "Resume Work", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        mock_integrate.assert_called_once()
        self.assertEqual(mock_run.call_count, 4)


class TestStartFeatureBranchOrderingGate(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #358): advance_story_stage already
    refuses to let a story reach Implemented-onward before the
    higher-priority story immediately ahead of it has reached Accepted -
    but nothing stopped the real work (start_feature_branch) from starting
    on a lower-priority story first. Mirrors that same ordering gate here,
    one step earlier, at the point work actually begins.
    """

    def _base_state(self, product_backlog):
        return {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "product_backlog": product_backlog,
            # GH issue #357's team-engagement gate also runs in
            # start_feature_branch - satisfy it by default here so this
            # class's tests stay focused on the ordering gate specifically.
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
        }

    @patch("agents.scrum_team.tools.github.gh_pr_create")
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_refuses_to_start_a_lower_priority_story_first(self, mock_run, mock_git_push, mock_gh_pr_create):
        tool_context = MagicMock()
        tool_context.state = self._base_state([
            {"id": "US-0001", "title": "First", "stages_completed": ["Draft", "Ready"]},
            {"id": "US-0002", "title": "Second", "stages_completed": ["Draft", "Ready"]},
        ])

        result = start_feature_branch("US-0002", "second-story", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("US-0001", result["message"])
        self.assertIn("must reach Accepted first", result["message"])
        # The set_priority escape hatch is only named when the blocker is an
        # Issue (a process finding) - US-0001 here is a real Story.
        self.assertNotIn("set_priority", result["message"])
        mock_git_push.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_allows_starting_once_the_preceding_story_is_accepted(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-0002-second-story"}
        tool_context = MagicMock()
        tool_context.state = self._base_state([
            {"id": "US-0001", "title": "First", "stages_completed": ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]},
            {"id": "US-0002", "title": "Second", "stages_completed": ["Draft", "Ready"]},
        ])

        result = start_feature_branch("US-0002", "second-story", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_skips_a_blocked_predecessor(self, mock_run, mock_git_push, mock_gh_pr_create):
        """A BLOCKED story shouldn't also freeze every lower-priority story
        behind it - the team is meant to move on while it waits."""
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-0002-second-story"}
        tool_context = MagicMock()
        tool_context.state = self._base_state([
            {
                "id": "US-0001", "title": "First", "stages_completed": ["Draft", "Ready"],
                "blocked": {"category": "technical", "question": "which approach?"},
            },
            {"id": "US-0002", "title": "Second", "stages_completed": ["Draft", "Ready"]},
        ])

        result = start_feature_branch("US-0002", "second-story", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_when_story_is_not_in_product_backlog(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = self._base_state([])

        result = start_feature_branch("US-9999", "untracked-story", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("product_backlog", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_a_preceding_issue_still_blocks_but_names_the_escape_hatch(self, mock_run):
        """
        Acceptance Criteria (GH issue #368, reconsidered): an auto-filed
        retro/impediment Issue sitting ahead of a real story DOES still
        gate start_feature_branch - see _preceding_story's own docstring
        for why exempting Issues removed the only pressure actually pushing
        one to Accepted, not just Ready. The refusal message now names the
        set_priority escape hatch explicitly.
        """
        tool_context = MagicMock()
        tool_context.state = self._base_state([
            {"id": "ISSUE-0001", "title": "Ensure robust test isolation", "type": "Issue", "priority": "Must", "stages_completed": ["Draft"]},
            {"id": "US-0002", "title": "Second", "stages_completed": ["Draft", "Ready"]},
        ])

        result = start_feature_branch("US-0002", "second-story", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("ISSUE-0001", result["message"])
        self.assertIn("resolve it for real", result["message"])
        self.assertIn("set_priority('ISSUE-0001'", result["message"])
        self.assertIn("warranted blocking priority", result["message"])
        # "Resolve it for real" must be the lead instruction, named before
        # the reprioritization escape hatch - not the other way around.
        self.assertLess(result["message"].index("resolve it for real"), result["message"].index("set_priority"))
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_first_story_in_backlog_has_no_predecessor(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-0001-first-story"}
        tool_context = MagicMock()
        tool_context.state = self._base_state([
            {"id": "US-0001", "title": "First", "stages_completed": ["Draft", "Ready"]},
        ])

        result = start_feature_branch("US-0001", "first-story", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")


class TestStartFeatureBranchBlockedStoryGate(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #359): a story marked BLOCKED
    (raise_story_blocker) must not have real work started/continued on it
    until the blocking reason is actually resolved (resolve_story_blocker) -
    advance_story_stage already refuses every further stage transition
    while `blocked` is set, but nothing previously stopped the real work
    (this call) from starting/continuing on it anyway.
    """

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_to_start_work_on_a_blocked_story(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "product_backlog": [{
                "id": "US-0001", "title": "Add login",
                "blocked": {"category": "technical", "question": "which auth library?", "raised_by": "DevTeam"},
            }],
        }

        result = start_feature_branch("US-0001", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("BLOCKED", result["message"])
        self.assertIn("which auth library?", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_allows_starting_once_unblocked(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-0001-add-login"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "product_backlog": [{"id": "US-0001", "title": "Add login", "blocked": None}],
            # GH issue #357's team-engagement gate also runs in
            # start_feature_branch, after this one - satisfy it here so
            # this test stays focused on the blocked-story gate specifically.
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
        }

        result = start_feature_branch("US-0001", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_a_blocked_different_story_does_not_affect_this_one(self, mock_run, mock_git_push, mock_gh_pr_create):
        mock_git_push.return_value = {"status": "ok", "branch": "feature/US-0002-add-logout"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "product_backlog": [
                {"id": "US-0001", "title": "Add login", "blocked": {"category": "technical", "question": "why?"}},
                {"id": "US-0002", "title": "Add logout"},
            ],
            "pr_review_calls": {"Architect": 1, "DevTeam": 1, "QA": 1},
        }

        result = start_feature_branch("US-0002", "add-logout", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")


class TestStartFeatureBranchTeamEngagementGate(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #357): start_feature_branch mechanically
    refuses to run until Architect, Dev Team, and QA have each left a real
    gh_pr_comment/gh_pr_review on this sprint's Sprint Backlog PR - the
    Sprint Backlog PR merging alone isn't proof the team actually weighed
    in on the plan Product Owner proposed.
    """

    def _base_state(self, **overrides):
        state = {
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            # GH issue #358's ordering gate and #359's blocked-story gate
            # also run in start_feature_branch, both before this one - a
            # single, non-blocked-story backlog satisfies both trivially,
            # keeping this class focused on the engagement gate specifically.
            "product_backlog": [{"id": "US-1", "title": "add-login"}],
            "pr_review_calls": {},
            "sprint_backlog_engagement_baseline": {},
        }
        state.update(overrides)
        return state

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_when_no_role_has_engaged_yet(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = self._base_state()

        result = start_feature_branch("US-1", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Architect", result["message"])
        self.assertIn("DevTeam", result["message"])
        self.assertIn("QA", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_naming_only_the_roles_still_missing(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = self._base_state(pr_review_calls={"Architect": 1, "DevTeam": 1})

        result = start_feature_branch("US-1", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("QA", result["message"])
        self.assertNotIn("Architect", result["message"])
        self.assertNotIn("DevTeam", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "ok", "branch": "feature/US-1-add-login"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_allows_starting_once_all_three_roles_have_engaged(self, mock_run, mock_git_push, mock_gh_pr_create):
        tool_context = MagicMock()
        tool_context.state = self._base_state(
            pr_review_calls={"Architect": 1, "DevTeam": 1, "QA": 1},
        )

        result = start_feature_branch("US-1", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "ok", "branch": "feature/US-1-add-login"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_an_earlier_sprints_engagement_does_not_satisfy_this_sprints_gate(self, mock_run, mock_git_push, mock_gh_pr_create):
        """
        pr_review_calls accumulates across the whole run, never resets per
        sprint - without comparing against a baseline snapshotted fresh each
        sprint, stale engagement from a past sprint would trivially satisfy
        this sprint's gate.
        """
        tool_context = MagicMock()
        tool_context.state = self._base_state(
            pr_review_calls={"Architect": 1, "DevTeam": 1, "QA": 1},
            sprint_backlog_engagement_baseline={"Architect": 1, "DevTeam": 1, "QA": 1},
        )

        result = start_feature_branch("US-1", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "error")

    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/9"})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "ok", "branch": "feature/US-1-add-login"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    def test_fresh_engagement_above_the_baseline_satisfies_the_gate(self, mock_run, mock_git_push, mock_gh_pr_create):
        tool_context = MagicMock()
        tool_context.state = self._base_state(
            pr_review_calls={"Architect": 2, "DevTeam": 2, "QA": 2},
            sprint_backlog_engagement_baseline={"Architect": 1, "DevTeam": 1, "QA": 1},
        )

        result = start_feature_branch("US-1", "add-login", tool_context=tool_context)

        self.assertEqual(result["status"], "ok")


class TestReleasePrStillOpen(unittest.TestCase):
    """
    Acceptance Criteria: a real incident planned an entire new sprint while
    the previous one's release PR (develop -> main) was still sitting open
    and unmerged - create_release_pr never merges anything itself, only
    opens the PR. release_pr_still_open is the mechanical check start_sprint
    (tools/scrum.py) uses to refuse that. Must never raise - a lookup
    failure (gh unreachable, no PR ever opened) means "nothing found",
    treated the same as genuinely no open PR.
    """

    @patch("agents.scrum_team.tools.github._run")
    def test_true_when_an_open_pr_exists(self, mock_run):
        mock_run.return_value = {"status": "ok", "stdout": '[{"number": 42}]'}
        self.assertTrue(release_pr_still_open(tool_context=MagicMock()))
        args = mock_run.call_args.args[0]
        self.assertEqual(args[:3], ["gh", "pr", "list"])
        self.assertIn("--state", args)
        self.assertEqual(args[args.index("--state") + 1], "open")

    @patch("agents.scrum_team.tools.github._run")
    def test_false_when_no_open_pr_exists(self, mock_run):
        mock_run.return_value = {"status": "ok", "stdout": "[]"}
        self.assertFalse(release_pr_still_open(tool_context=MagicMock()))

    @patch("agents.scrum_team.tools.github._run")
    def test_false_on_gh_lookup_failure(self, mock_run):
        mock_run.return_value = {"status": "error", "stderr": "gh: not authenticated"}
        self.assertFalse(release_pr_still_open(tool_context=MagicMock()))

    @patch("agents.scrum_team.tools.github._run")
    def test_false_on_malformed_json(self, mock_run):
        mock_run.return_value = {"status": "ok", "stdout": "not json"}
        self.assertFalse(release_pr_still_open(tool_context=MagicMock()))

    @patch("agents.scrum_team.tools.github._develop_branch_name", return_value="develop")
    @patch("agents.scrum_team.tools.github._default_push_branch", return_value="main")
    @patch("agents.scrum_team.tools.github._run")
    def test_checks_the_configured_develop_and_main_branches(self, mock_run, mock_default, mock_develop):
        mock_run.return_value = {"status": "ok", "stdout": "[]"}
        release_pr_still_open(tool_context=MagicMock())
        args = mock_run.call_args.args[0]
        self.assertEqual(args[args.index("--base") + 1], "main")
        self.assertEqual(args[args.index("--head") + 1], "develop")


class TestIntegrateOpenChanges(unittest.TestCase):
    """
    Acceptance Criteria: integrate_open_changes commits dangling writes
    scoped to specs/ and .hc/ only (never a caller's own unrelated
    uncommitted work), and no-ops cleanly when there's nothing to do.
    """

    @patch("agents.scrum_team.tools.github._configured_repo_root")
    @patch("agents.scrum_team.tools.github._run")
    def test_no_open_changes_is_a_clean_noop(self, mock_run, mock_root):
        mock_root.return_value = MagicMock(__truediv__=lambda self, other: MagicMock(exists=lambda: True))
        mock_run.return_value = {"status": "ok", "stdout": ""}
        result = integrate_open_changes(tool_context=MagicMock())
        self.assertEqual(result, {"status": "ok", "integrated": False,
                                   "message": "No open planning-doc changes under specs/ or .hc/ to integrate."})

    @patch("agents.scrum_team.tools.github._configured_repo_root")
    @patch("agents.scrum_team.tools.github._run")
    def test_commits_only_specs_and_hc(self, mock_run, mock_root):
        mock_root.return_value = MagicMock(__truediv__=lambda self, other: MagicMock(exists=lambda: True))
        mock_run.side_effect = [
            {"status": "ok", "stdout": " M specs/ROADMAP.md"},  # git status --porcelain
            {"status": "ok"},  # git add
            {"status": "ok", "stdout": "specs/ROADMAP.md"},  # git diff --cached --name-only
            {"status": "ok"},  # git commit
        ]
        result = integrate_open_changes(tool_context=MagicMock())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["integrated"])
        self.assertEqual(result["files"], ["specs/ROADMAP.md"])
        add_call = mock_run.call_args_list[1]
        self.assertEqual(add_call.args[0], ["git", "add", "--", "specs", ".hc"])

    @patch("agents.scrum_team.tools.github._configured_repo_root")
    def test_skips_a_pathspec_that_does_not_exist_yet(self, mock_root):
        # .hc/ doesn't exist before the first save_state_to_repo call ever
        # ran - `git add -- specs .hc` would hard-fail on that pathspec
        # ("did not match any files") if passed unconditionally.
        existing = {"specs"}
        mock_root.return_value = MagicMock(__truediv__=lambda self, other: MagicMock(exists=lambda: other in existing))
        with patch("agents.scrum_team.tools.github._run") as mock_run:
            mock_run.side_effect = [
                {"status": "ok", "stdout": " M specs/ROADMAP.md"},
                {"status": "ok"},
                {"status": "ok", "stdout": "specs/ROADMAP.md"},
                {"status": "ok"},
            ]
            integrate_open_changes(tool_context=MagicMock())
            status_call = mock_run.call_args_list[0]
            self.assertNotIn(".hc", status_call.args[0])


class TestCheckoutWithAutoIntegrate(unittest.TestCase):
    """
    Acceptance Criteria (GH eval run41): the shared self-heal-on-"would be
    overwritten" helper every checkout call site in this module uses -
    _checkout_develop_or_recover's own checkout, start_feature_branch's
    feature-branch checkout, create_story_spec_pr/create_sprint_backlog_pr's
    branch checkouts - factored out once so a new call site can't
    reintroduce the gap start_feature_branch's own checkout had (a real
    eval run hit it with no self-heal at all) by simply forgetting to copy
    the pattern.
    """

    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_self_heals_would_be_overwritten_and_retries_the_same_command(self, mock_run, mock_integrate):
        mock_run.side_effect = [
            {"status": "error", "stderr": "local changes would be overwritten by checkout"},
            {"status": "ok"},
        ]
        mock_integrate.return_value = {"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]}

        checkout, auto_integrated = _checkout_with_auto_integrate(["git", "checkout", "-B", "feature/x"], "/repo", tool_context=MagicMock())

        self.assertEqual(checkout["status"], "ok")
        self.assertEqual(auto_integrated["integrated"], True)
        mock_integrate.assert_called_once()
        self.assertEqual(mock_run.call_count, 2)
        for call in mock_run.call_args_list:
            self.assertEqual(call.args[0], ["git", "checkout", "-B", "feature/x"])

    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_does_not_retry_an_unrelated_failure(self, mock_run, mock_integrate):
        mock_run.return_value = {"status": "error", "stderr": "fatal: couldn't find remote ref develop"}

        checkout, auto_integrated = _checkout_with_auto_integrate(["git", "checkout", "-B", "develop"], "/repo", tool_context=MagicMock())

        self.assertEqual(checkout["status"], "error")
        self.assertIsNone(auto_integrated)
        mock_integrate.assert_not_called()
        self.assertEqual(mock_run.call_count, 1)

    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_gives_up_when_integration_finds_nothing_to_integrate(self, mock_run, mock_integrate):
        mock_run.return_value = {"status": "error", "stderr": "would be overwritten by checkout"}
        mock_integrate.return_value = {"status": "ok", "integrated": False, "message": "nothing to integrate"}

        checkout, auto_integrated = _checkout_with_auto_integrate(["git", "checkout", "-B", "develop"], "/repo", tool_context=MagicMock())

        self.assertEqual(checkout["status"], "error")
        self.assertFalse(auto_integrated["integrated"])
        self.assertEqual(mock_run.call_count, 1)

    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_returns_ok_immediately_when_checkout_succeeds_first_try(self, mock_run, mock_integrate):
        mock_run.return_value = {"status": "ok"}

        checkout, auto_integrated = _checkout_with_auto_integrate(["git", "checkout", "develop"], "/repo", tool_context=MagicMock())

        self.assertEqual(checkout["status"], "ok")
        self.assertIsNone(auto_integrated)
        mock_integrate.assert_not_called()
        self.assertEqual(mock_run.call_count, 1)


class TestCheckoutDevelopOrRecover(unittest.TestCase):
    """
    Acceptance Criteria: the shared checkout-develop helper used by
    create_story_spec_pr/create_sprint_backlog_pr/start_feature_branch/
    create_release_pr self-heals a "local changes would be overwritten"
    failure by integrating dangling specs/.hc writes and retrying once, but
    leaves any other checkout failure (bad branch, network, auth) untouched.

    _preserve_local_only_develop_commits (ISSUE-0050) is patched to a no-op
    None in these - they're about the self-heal/retry behavior, covered by
    its own TestPreserveLocalOnlyDevelopCommits below.
    """

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits", return_value=None)
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_self_heals_local_changes_would_be_overwritten(self, mock_run, mock_integrate, mock_preserve):
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "error", "stderr": "error: Your local changes to the following files would be overwritten by checkout"},
            {"status": "ok"},  # retried checkout
        ]
        mock_integrate.return_value = {"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]}

        result = _checkout_develop_or_recover("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "ok")
        mock_integrate.assert_called_once()
        self.assertEqual(result["auto_integrated"]["integrated"], True)
        self.assertEqual(mock_run.call_count, 3)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits", return_value=None)
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_does_not_retry_an_unrelated_checkout_failure(self, mock_run, mock_integrate, mock_preserve):
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "error", "stderr": "fatal: couldn't find remote ref develop"},
        ]

        result = _checkout_develop_or_recover("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["auto_integrated"])
        mock_integrate.assert_not_called()
        self.assertEqual(mock_run.call_count, 2)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits", return_value=None)
    @patch("agents.scrum_team.tools.github.integrate_open_changes")
    @patch("agents.scrum_team.tools.github._run")
    def test_gives_up_if_integration_finds_nothing_to_integrate(self, mock_run, mock_integrate, mock_preserve):
        # Not every "would be overwritten" is dangling specs/.hc writes (could
        # be an untracked file elsewhere in the shared checkout) - if
        # integrate_open_changes had nothing to do, retrying would just fail
        # identically, so don't bother.
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "error", "stderr": "would be overwritten by checkout"},
        ]
        mock_integrate.return_value = {"status": "ok", "integrated": False, "message": "nothing to integrate"}

        result = _checkout_develop_or_recover("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "error")
        self.assertEqual(mock_run.call_count, 2)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_uses_plain_checkout_once_local_is_confirmed_in_sync_with_origin(self, mock_run, mock_preserve):
        # ISSUE-0050: once _preserve_local_only_develop_commits has already
        # pushed (or merged-then-pushed) local <develop> to match origin, the
        # usual reset-to-origin checkout is redundant - use a plain checkout
        # instead of re-resetting (harmless either way once in sync, but a
        # plain checkout can't ever race a not-yet-visible push).
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "ok"},  # plain checkout
        ]
        mock_preserve.return_value = {"status": "ok", "in_sync": True, "action": "merged_and_pushed"}

        result = _checkout_develop_or_recover("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["preserved_local_commits"]["action"], "merged_and_pushed")
        mock_run.assert_any_call(["git", "checkout", "develop"], cwd="/repo", tool_context=unittest.mock.ANY)

    @patch("agents.scrum_team.tools.github._preserve_local_only_develop_commits")
    @patch("agents.scrum_team.tools.github._run")
    def test_falls_back_to_reset_checkout_when_preservation_could_not_sync(self, mock_run, mock_preserve):
        # A real, unresolvable merge conflict (rescued to a branch, not
        # in_sync) - the original reset-to-origin checkout still has to run,
        # same as before this fix existed, just with the loss now logged and
        # recoverable instead of silent.
        mock_run.side_effect = [
            {"status": "ok"},  # fetch
            {"status": "ok"},  # reset checkout
        ]
        mock_preserve.return_value = {"status": "error", "in_sync": False, "action": "rescued", "rescue_branch": "rescued-develop-abc123"}

        result = _checkout_develop_or_recover("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "ok")
        mock_run.assert_any_call(["git", "checkout", "-B", "develop", "origin/develop"], cwd="/repo", tool_context=unittest.mock.ANY)


class TestPreserveLocalOnlyDevelopCommits(unittest.TestCase):
    """
    ISSUE-0050 / 0.1.0-run33, 0.1.0-run34: _checkout_develop_or_recover's own
    reset-to-origin checkout silently discarded local-only commits (e.g.
    save_state_to_repo's checkpoint commits) - confirmed via a live git repro
    (fetch/checkout -B/pull --ff-only don't lose anything on their own; the
    unconditional `checkout -B <develop> origin/<develop>` does). This is
    the fix: push (or merge-then-push) local-ahead commits before that reset
    ever runs.
    """

    @patch("agents.scrum_team.tools.github._run")
    def test_no_op_when_local_branch_does_not_exist_yet(self, mock_run):
        mock_run.return_value = {"status": "error", "returncode": 1}

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=MagicMock())

        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 1)

    @patch("agents.scrum_team.tools.github._run")
    def test_no_op_when_local_is_not_ahead_of_origin(self, mock_run):
        mock_run.side_effect = [
            {"status": "ok", "returncode": 0},  # rev-parse --verify develop
            {"status": "ok", "stdout": ""},  # rev-list origin/develop..develop - nothing
        ]

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=MagicMock())

        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 2)

    @patch("agents.scrum_team.tools.github._run")
    def test_pushes_a_clean_fast_forward_ahead_of_origin(self, mock_run):
        mock_run.side_effect = [
            {"status": "ok", "returncode": 0},  # rev-parse --verify develop
            {"status": "ok", "stdout": "abc123\n"},  # rev-list - local is ahead
            {"status": "ok"},  # push succeeds - plain fast-forward
        ]

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result, {"status": "ok", "in_sync": True, "action": "pushed_local_ahead"})
        self.assertEqual(mock_run.call_count, 3)

    @patch("agents.scrum_team.tools.github._run")
    def test_merges_and_pushes_on_genuine_divergence_with_no_conflict(self, mock_run):
        mock_run.side_effect = [
            {"status": "ok", "returncode": 0},  # rev-parse --verify develop
            {"status": "ok", "stdout": "abc123\n"},  # rev-list - local is ahead
            {"status": "error"},  # plain push fails - diverged
            {"status": "ok", "stdout": "abc123\n"},  # rev-parse develop (pre-merge tip)
            {"status": "ok"},  # checkout develop
            {"status": "ok"},  # merge origin/develop - clean
            {"status": "ok"},  # push merged develop
        ]

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result, {"status": "ok", "in_sync": True, "action": "merged_and_pushed"})

    @patch("agents.scrum_team.tools.github._run")
    def test_rescues_local_tip_to_a_branch_on_a_real_merge_conflict(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {"decision_log": []}
        mock_run.side_effect = [
            {"status": "ok", "returncode": 0},  # rev-parse --verify develop
            {"status": "ok", "stdout": "abc123\n"},  # rev-list - local is ahead
            {"status": "error"},  # plain push fails - diverged
            {"status": "ok", "stdout": "abc123\n"},  # rev-parse develop (pre-merge tip)
            {"status": "ok"},  # checkout develop
            {"status": "error", "stderr": "CONFLICT"},  # merge fails - real conflict
            {"status": "ok"},  # merge --abort
            {"status": "ok"},  # branch rescued-...
            {"status": "ok"},  # push rescue branch
        ]

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["in_sync"])
        self.assertEqual(result["action"], "rescued")
        self.assertIn("abc123", result["rescue_branch"])
        self.assertEqual(len(tool_context.state["decision_log"]), 1)
        self.assertIn("rescued-develop-abc123", tool_context.state["decision_log"][0]["decision"])

    @patch("agents.scrum_team.tools.github._run")
    def test_does_not_lose_result_when_merge_succeeds_but_push_fails(self, mock_run):
        mock_run.side_effect = [
            {"status": "ok", "returncode": 0},  # rev-parse --verify develop
            {"status": "ok", "stdout": "abc123\n"},  # rev-list - local is ahead
            {"status": "error"},  # plain push fails - diverged
            {"status": "ok", "stdout": "abc123\n"},  # rev-parse develop (pre-merge tip)
            {"status": "ok"},  # checkout develop
            {"status": "ok"},  # merge origin/develop - clean
            {"status": "error"},  # push of the merge fails (network, auth, ...)
        ]

        result = _preserve_local_only_develop_commits("/repo", "develop", tool_context=MagicMock())

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["in_sync"])
        self.assertEqual(result["action"], "merged_but_push_failed")


# A single Ready (not yet Accepted), non-Epic story - enough to satisfy
# ready_backlog_shortfall's sufficiency gate once TARGET_STORIES_PER_SPRINT/
# READY_BACKLOG_SPRINTS_TARGET are overridden to 1 each below; these tests
# are about the git plumbing, not the sufficiency threshold itself (see
# test_sprint_and_approval_gates.py for that).
_ONE_READY_STORY = [{"id": "US-0001", "type": "User Story", "stages_completed": ["Draft", "Ready"]}]

_LOW_BACKLOG_TARGET_ENV = {"TARGET_STORIES_PER_SPRINT": "1", "READY_BACKLOG_SPRINTS_TARGET": "1"}


class TestCreateSprintBacklogPr(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #171): Product Owner's sprint-planning
    writes (roadmap/PRD/epics/stories) only ever hit disk, never git - the
    first subsequent push (normally DevTeam's start_feature_branch) swept
    them onto a feature branch instead of develop. create_sprint_backlog_pr
    commits+pushes them to their own branch, opens a "Sprint Backlog #<N>"
    PR against develop, and (once the Ready backlog holds enough queued-up
    work, and - at levels requiring one - a fresh sprint/budget approval is
    recorded) merges it.
    """

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_without_a_started_sprint(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 0}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("start_sprint", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, {**_LOW_BACKLOG_TARGET_ENV, "INTERACTION_LEVEL": "EVAL"})
    def test_happy_path_opens_and_merges_against_develop(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/3"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 3,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY,
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["merged"])
        self.assertEqual(result["sprint_number"], 3)
        self.assertEqual(result["branch"], "sprint-backlog/3")
        mock_git_push.assert_called_once_with(
            branch="sprint-backlog/3", commit_message="chore: sprint 3 backlog", add_all=False, tool_context=tool_context,
        )
        mock_gh_pr_create.assert_called_once_with(
            title="Sprint Backlog #3",
            body=unittest.mock.ANY,
            base="develop",
            head="sprint-backlog/3",
            head_is_resolved=True,
            tool_context=tool_context,
        )
        # Final call is the merge - explicitly targets this sprint's own
        # branch (not just "whatever's currently checked out") so a later,
        # idempotent re-call (e.g. after a required approval is recorded)
        # can merge the already-open PR without re-checking it out first.
        mock_run.assert_called_with(
            ["gh", "pr", "merge", "sprint-backlog/3", "--merge", "--admin"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )
        # No sprint_backlog seeded in this fixture - nothing to project a
        # capacity advisory from, so it's simply absent, not an error.
        self.assertNotIn("capacity_advisory", result)

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, {**_LOW_BACKLOG_TARGET_ENV, "INTERACTION_LEVEL": "EVAL"})
    def test_snapshots_engagement_baseline_on_the_first_merge_this_sprint(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        """GH issue #357: start_feature_branch's team-engagement gate needs a
        fresh-this-sprint baseline - take it the moment this sprint's Sprint
        Backlog PR first merges, from whatever pr_review_calls holds then."""
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/3"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 3,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY,
            "pr_review_calls": {"Architect": 2, "QA": 1},
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            tool_context.state["sprint_backlog_engagement_baseline"],
            {"Architect": 2, "QA": 1},
        )

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, {**_LOW_BACKLOG_TARGET_ENV, "INTERACTION_LEVEL": "EVAL"})
    def test_does_not_resnapshot_the_baseline_on_a_second_merge_the_same_sprint(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        """A second create_sprint_backlog_pr call the same sprint (e.g. PO
        adding more stories) shouldn't erase the baseline already taken -
        otherwise engagement a role already gave on the first merge would be
        silently forgotten."""
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/3"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 3,
            "sprint_backlog_pr_sprint": 3,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY,
            "pr_review_calls": {"Architect": 5, "DevTeam": 5, "QA": 5},
            "sprint_backlog_engagement_baseline": {"Architect": 1, "DevTeam": 1, "QA": 1},
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            tool_context.state["sprint_backlog_engagement_baseline"],
            {"Architect": 1, "DevTeam": 1, "QA": 1},
        )

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, {**_LOW_BACKLOG_TARGET_ENV, "INTERACTION_LEVEL": "EVAL"})
    def test_surfaces_capacity_advisory_when_backlog_is_undersized(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        """GH issue #294: a non-blocking nudge when the committed backlog
        looks clearly under-sized relative to the sprint's token budget."""
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/3"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 3,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY,
            "sprint_backlog": _ONE_READY_STORY,
            "story_estimates": {"US-0001": {"estimate": 50_000}},
            "budgets": {"total": 1_000_000},
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertIn("capacity_advisory", result)
        self.assertIn("under this sprint's 1,000,000 token budget", result["capacity_advisory"])

    @patch("agents.scrum_team.tools.github._run", return_value={"status": "error", "stderr": "no such ref"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_reports_error_when_develop_checkout_fails(self, mock_run):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1, "product_backlog": _ONE_READY_STORY}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("develop", result["message"])

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": False, "message": "No open planning-doc changes under specs/ or .hc/ to integrate."})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_refuses_when_there_is_no_new_planning_output_to_publish(self, mock_run, mock_git_push, mock_integrate):
        """
        GH issue #379: this used to call git_push with its default
        add_all=True ("git add -A"), which staged whichever pending writes
        happened to be sitting in the shared checkout at that moment -
        including, in a real run, another story's not-yet-committed spec
        file - and git_push's own --allow-empty fallback meant even
        genuinely NOTHING new still silently opened/merged a content-free
        PR. Scoped to specs/+.hc/ via integrate_open_changes now, this must
        refuse outright instead of publishing an empty "Sprint Backlog" PR.
        """
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 1,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY,
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("no new planning output", result["message"])
        mock_git_push.assert_not_called()

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "error", "branch": "sprint-backlog/1"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_reports_error_when_push_fails(self, mock_run, mock_git_push, mock_integrate):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1, "product_backlog": _ONE_READY_STORY}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("push", result["message"].lower())

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "error", "stderr": "already exists"})
    @patch("agents.scrum_team.tools.github.git_push", return_value={"status": "ok", "branch": "sprint-backlog/1"})
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_reports_error_when_pr_create_fails(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1, "product_backlog": _ONE_READY_STORY}

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Sprint Backlog PR", result["message"])
        # Never reaches the merge step if the PR was never opened.
        mock_run.assert_called_with(["git", "checkout", "-B", "sprint-backlog/1"], cwd=unittest.mock.ANY, tool_context=tool_context)

    @patch("agents.scrum_team.tools.github._run")
    def test_refuses_while_a_must_priority_issue_sits_unaddressed(self, mock_run):
        """GH issue (0.1.0-run42): _file_retro_items_as_issues (budget.py)
        files retro/impediment findings as Must-priority Issues specifically
        so they "can't be silently starved" - but nothing previously forced
        Sprint Planning to ever pick one back up. A real run showed exactly
        that: a dependency-pinning Issue filed in Sprint 1 sat at Draft,
        unaddressed, through Sprints 2-5."""
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 2,
            "product_backlog": [
                {"id": "ISSUE-0002", "type": "Issue", "priority": "Must", "stages_completed": ["Draft"]},
            ],
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("ISSUE-0002", result["message"])
        self.assertIn("advance_story_stage", result["message"])
        mock_run.assert_not_called()

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_proceeds_once_the_must_issue_has_reached_ready(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/2"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 2,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": [
                {"id": "ISSUE-0002", "type": "Issue", "priority": "Must", "stages_completed": ["Draft", "Ready"]},
            ],
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_does_not_block_on_a_blocked_must_issue(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        """A genuinely BLOCKED issue (raise_story_blocker) has its own
        separate escalation path - this guardrail must not additionally
        wedge Sprint Planning on something already flagged as blocked."""
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/2"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 2,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            # A separate Ready story satisfies the Ready-backlog-shortfall
            # gate right below, so this test isolates the blocked-Must-Issue
            # behavior specifically.
            "product_backlog": _ONE_READY_STORY + [
                {
                    "id": "ISSUE-0002", "type": "Issue", "priority": "Must", "stages_completed": ["Draft"],
                    "blocked": {"category": "product", "question": "which fix?"},
                },
            ],
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("agents.scrum_team.tools.github.integrate_open_changes", return_value={"status": "ok", "integrated": True, "files": ["specs/ROADMAP.md"]})
    @patch("agents.scrum_team.tools.github.gh_pr_create", return_value={"status": "ok", "stdout": "https://github.com/owner/repo/pull/124"})
    @patch("agents.scrum_team.tools.github.git_push")
    @patch("agents.scrum_team.tools.github._run", return_value={"status": "ok"})
    @patch.dict(os.environ, _LOW_BACKLOG_TARGET_ENV)
    def test_does_not_block_on_a_reprioritized_issue(self, mock_run, mock_git_push, mock_gh_pr_create, mock_integrate):
        """set_priority(..., away from 'Must') is the explicit escape hatch
        the error message offers - once used, this guardrail must stand
        down for that item."""
        mock_git_push.return_value = {"status": "ok", "branch": "sprint-backlog/2"}
        tool_context = MagicMock()
        tool_context.state = {
            "sprint_number": 2,
            "repo": {"default_branch": "main", "develop_branch": "develop"},
            "product_backlog": _ONE_READY_STORY + [
                {"id": "ISSUE-0002", "type": "Issue", "priority": "Should", "stages_completed": ["Draft"]},
            ],
        }

        result = create_sprint_backlog_pr(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")


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

        mock_run.assert_any_call(
            ["gh", "pr", "merge", "--merge"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_merge_story_pr_with_pr_id_and_admin(self, mock_run):
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        merge_story_pr(pr_id=7, admin=True, tool_context=tool_context)

        mock_run.assert_any_call(
            ["gh", "pr", "merge", "7", "--merge", "--admin"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_successful_merge_syncs_local_develop_to_origin(self, mock_run):
        """
        Acceptance Criteria (GH issue #317): `gh pr merge` lands on origin's
        develop directly, independent of this container's local git state -
        without a sync, local develop silently falls one commit behind until
        some later write's own divergence check discovers it. A successful
        merge must fetch+fast-forward local develop immediately.
        """
        mock_run.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        merge_story_pr(tool_context=tool_context)

        mock_run.assert_any_call(
            ["git", "fetch", "origin", "develop"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )
        mock_run.assert_any_call(
            ["git", "checkout", "develop"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )
        mock_run.assert_any_call(
            ["git", "merge", "--ff-only", "origin/develop"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )

    @patch("agents.scrum_team.tools.github._run")
    def test_failed_merge_does_not_attempt_a_sync(self, mock_run):
        """No PR actually merged - nothing new landed on origin's develop to
        sync to, and attempting one would just be a pointless extra call."""
        mock_run.return_value = {"status": "error", "stderr": "PR not found"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        merge_story_pr(tool_context=tool_context)

        mock_run.assert_called_once_with(
            ["gh", "pr", "merge", "--merge"], cwd=unittest.mock.ANY, tool_context=tool_context,
        )


if __name__ == "__main__":
    unittest.main()
