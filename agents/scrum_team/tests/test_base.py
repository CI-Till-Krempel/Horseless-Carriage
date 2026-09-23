# agents/scrum_team/tests/test_base.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import agents.scrum_team.tools.base as base_module
from agents.scrum_team.tools.base import (
    _configured_repo_root,
    _default_push_branch,
    _develop_branch_name,
    _ensure_git_safe_directory,
    _hc_version,
    _redact_cmd,
    _redact_secrets,
    _run,
)
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


class TestRedactSecrets(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #128): conversation text about to be
    persisted (transcript/messages, and from there sprint reports/
    state.json) must have common secret shapes masked, since a user can
    paste a real credential into a prompt or a tool result can echo one
    back into the model's response text.
    """

    def test_masks_github_classic_token(self):
        text = "here is my token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345"
        redacted = _redact_secrets(text)
        self.assertNotIn("ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345", redacted)
        self.assertIn("***REDACTED-GH-TOKEN***", redacted)

    def test_masks_github_fine_grained_pat(self):
        text = "use github_pat_11ABCDEFG0123456789abcdefghijklmnopqrstuvwxyz for auth"
        redacted = _redact_secrets(text)
        self.assertNotIn("github_pat_11ABCDEFG0123456789abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("***REDACTED-GH-TOKEN***", redacted)

    def test_masks_openai_or_litellm_style_key(self):
        text = "OPENAI_API_KEY=sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        redacted = _redact_secrets(text)
        self.assertNotIn("sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", redacted)
        self.assertIn("***REDACTED-KEY***", redacted)

    def test_masks_bearer_token_but_keeps_the_word_bearer(self):
        text = "Authorization: Bearer abcDEF123.token-value_here"
        redacted = _redact_secrets(text)
        self.assertNotIn("abcDEF123.token-value_here", redacted)
        self.assertIn("Bearer ***REDACTED***", redacted)

    def test_masks_basic_auth_header(self):
        text = "AUTHORIZATION: Basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0"
        redacted = _redact_secrets(text)
        self.assertNotIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", redacted)
        self.assertIn("AUTHORIZATION: Basic ***REDACTED***", redacted)

    def test_leaves_ordinary_text_untouched(self):
        text = "Let's plan the sprint backlog and pick up US-0042 next."
        self.assertEqual(_redact_secrets(text), text)

    def test_empty_string_is_a_no_op(self):
        self.assertEqual(_redact_secrets(""), "")


class TestRunTimeoutAndStdin(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #113): no subprocess this tool layer
    invokes (git, gh) had a timeout - a network stall on `git push`, or `gh`
    falling back to an interactive prompt for a credential it can't resolve
    non-interactively, hung the entire agent session indefinitely, with no
    way to tell a slow LLM call apart from a genuinely stuck subprocess.
    """

    def test_default_timeout_is_passed_to_subprocess_run(self):
        with patch("subprocess.run") as mock_subprocess_run:
            mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _run(["git", "status"])

        self.assertEqual(mock_subprocess_run.call_args.kwargs["timeout"], 120)

    def test_custom_timeout_is_honored(self):
        with patch("subprocess.run") as mock_subprocess_run:
            mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _run(["git", "status"], timeout=5)

        self.assertEqual(mock_subprocess_run.call_args.kwargs["timeout"], 5)

    def test_stdin_is_explicitly_closed(self):
        """Without this, `gh` falling back to an interactive prompt would
        hang waiting for input that can never arrive in this context."""
        import subprocess as subprocess_module
        with patch("subprocess.run") as mock_subprocess_run:
            mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _run(["gh", "auth", "status"])

        self.assertEqual(mock_subprocess_run.call_args.kwargs["stdin"], subprocess_module.DEVNULL)

    def test_env_overrides_are_applied_to_the_subprocess_environment(self):
        """Acceptance Criteria (ISSUE-0047): env_overrides must reach the
        actual subprocess call, layered on top of (not replacing) the
        inherited environment/GH_TOKEN/git-identity injection above."""
        with patch("subprocess.run") as mock_subprocess_run:
            mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _run(["pytest"], env_overrides={"PYTHONPATH": "/some/repo"})

        self.assertEqual(mock_subprocess_run.call_args.kwargs["env"]["PYTHONPATH"], "/some/repo")

    def test_timeout_expired_returns_a_clean_error_not_a_raised_exception(self):
        import subprocess as subprocess_module
        with patch("subprocess.run", side_effect=subprocess_module.TimeoutExpired(cmd=["git", "push"], timeout=120)):
            result = _run(["git", "push"])

        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("timed_out"))
        self.assertIn("timed out", result["message"])

    def test_git_command_gets_non_interactive_ssh_options(self):
        """A repo_url can be git@github.com:... (see .env.example's
        GITHUB_REPO_URL) before any token has ever been seeded into session
        state - e.g. the very first configure_github_repo call of a fresh
        session, or a session with no GITHUB_TOKEN/GITHUB_APP_* configured
        at all. Without this, that connection hits an unknown-host-key
        prompt against a container with no known_hosts/SSH agent, and since
        stdin is closed (see test_stdin_is_explicitly_closed above) ssh
        can't read an answer - it hangs until the timeout, and the
        orchestrator just retries the same failing call forever."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                _run(["git", "clone", "git@github.com:example/repo.git", "/tmp/repo"])

        ssh_command = mock_subprocess_run.call_args.kwargs["env"]["GIT_SSH_COMMAND"]
        self.assertIn("BatchMode=yes", ssh_command)
        self.assertIn("StrictHostKeyChecking=accept-new", ssh_command)

    def test_non_git_command_does_not_get_ssh_options(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                _run(["gh", "auth", "status"])

        self.assertNotIn("GIT_SSH_COMMAND", mock_subprocess_run.call_args.kwargs["env"])

    def test_git_ssh_command_respects_an_explicit_override(self):
        """env.setdefault - a caller/deployment that already mounted real
        SSH keys and set its own GIT_SSH_COMMAND must not be overridden."""
        with patch.dict("os.environ", {"GIT_SSH_COMMAND": "ssh -i /custom/key"}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                _run(["git", "clone", "git@github.com:example/repo.git", "/tmp/repo"])

        self.assertEqual(mock_subprocess_run.call_args.kwargs["env"]["GIT_SSH_COMMAND"], "ssh -i /custom/key")

    def test_git_terminal_prompt_disabled_by_default(self):
        """A real incident: git rejecting a stale credential over HTTPS
        stalled for the entire timeout instead of failing immediately, even
        with stdin closed - GIT_TERMINAL_PROMPT=0 is git's own explicit
        opt-out of every terminal credential prompt, belt-and-suspenders
        with DEVNULL."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                _run(["git", "push"])

        self.assertEqual(mock_subprocess_run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_git_terminal_prompt_respects_an_explicit_override(self):
        with patch.dict("os.environ", {"GIT_TERMINAL_PROMPT": "1"}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                _run(["git", "push"])

        self.assertEqual(mock_subprocess_run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "1")


class TestRunRetriesOnStaleGitHubAppToken(unittest.TestCase):
    """
    Acceptance Criteria: a GitHub App installation token is only valid for
    60 minutes (GitHub's own hard limit) - a real incident saw every git
    push/gh call fail with "Bad credentials" for the rest of a long sprint,
    because configure_github_app only ever mints a token once per session
    and nothing ever refreshed it. _run must transparently re-mint and
    retry exactly once on that specific failure, and must never do so for
    an unrelated failure or when there's no GitHub App to refresh from.
    """

    def _tool_context(self):
        tc = MagicMock()
        tc.state = {"github_token": "stale-token"}
        return tc

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_retries_once_after_refreshing_a_stale_token(self, mock_configure):
        mock_configure.return_value = {"status": "ok"}
        with patch.dict("os.environ", {
            "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2",
        }, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.side_effect = [
                    MagicMock(returncode=1, stdout="", stderr='{"message":"Bad credentials"}'),
                    MagicMock(returncode=0, stdout="pushed", stderr=""),
                ]
                result = _run(["git", "push", "origin", "develop"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stdout"], "pushed")
        mock_configure.assert_called_once_with("1", "key", "2", tool_context=unittest.mock.ANY)
        self.assertEqual(mock_subprocess_run.call_count, 2)

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_does_not_retry_when_not_using_github_app_auth(self, mock_configure):
        """No GITHUB_APP_* env vars set (e.g. a plain GITHUB_TOKEN personal
        access token) - nothing to refresh, must not retry or loop."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="Bad credentials")
                result = _run(["git", "push"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "error")
        mock_configure.assert_not_called()
        self.assertEqual(mock_subprocess_run.call_count, 1)

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_does_not_retry_an_unrelated_failure(self, mock_configure):
        with patch.dict("os.environ", {
            "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2",
        }, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal: bad revision 'develop'")
                result = _run(["git", "checkout", "develop"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "error")
        mock_configure.assert_not_called()
        self.assertEqual(mock_subprocess_run.call_count, 1)

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_does_not_loop_forever_if_the_refreshed_token_still_fails(self, mock_configure):
        """The App's permissions were actually revoked, not just expired -
        refresh "succeeds" (mints a token) but the retried call fails the
        exact same way. Must surface that failure, not retry indefinitely."""
        mock_configure.return_value = {"status": "ok"}
        with patch.dict("os.environ", {
            "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2",
        }, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="Bad credentials")
                result = _run(["git", "push"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "error")
        self.assertEqual(mock_configure.call_count, 1)
        self.assertEqual(mock_subprocess_run.call_count, 2)

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_does_not_retry_when_refresh_itself_fails(self, mock_configure):
        mock_configure.return_value = {"status": "error", "message": "invalid private key"}
        with patch.dict("os.environ", {
            "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2",
        }, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="Bad credentials")
                result = _run(["git", "push"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "error")
        self.assertEqual(mock_subprocess_run.call_count, 1)

    @patch("agents.scrum_team.tools.github.configure_github_app")
    def test_gh_commands_are_retried_too_not_just_git(self, mock_configure):
        mock_configure.return_value = {"status": "ok"}
        with patch.dict("os.environ", {
            "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2",
        }, clear=True):
            with patch("subprocess.run") as mock_subprocess_run:
                mock_subprocess_run.side_effect = [
                    MagicMock(returncode=1, stdout="", stderr="gh: Bad credentials (HTTP 401)"),
                    MagicMock(returncode=0, stdout="ok", stderr=""),
                ]
                result = _run(["gh", "pr", "create", "--title", "x"], tool_context=self._tool_context())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_subprocess_run.call_count, 2)


class TestEnsureGitSafeDirectory(unittest.TestCase):
    """
    Acceptance Criteria: a real ADK eval run's start_feature_branch calls
    all failed with "fatal: detected dubious ownership in repository at
    '/app/state_repo'" - the scratch state repo is created on the HOST by
    run_adk_eval.py's prepare_scratch_state_repo(), then bind-mounted into
    the agent container at a different EUID, tripping git's own
    CVE-2022-24765 safety check. _ensure_git_safe_directory must configure
    `git config --global --add safe.directory *` for exactly this case -
    but must never shell out at all when the path is already owned by the
    current user (the common case for every test's own tmp_path, and for a
    normal non-bind-mounted STATE_REPO_PATH), since this runs on every
    single _configured_repo_root call across the whole test suite.

    Marks `*`, not the specific path, since a later run also hit "dubious
    ownership in repository at './.state-repo-remote.git'" -
    prepare_scratch_state_repo's own local bare "origin" remote, a
    *separate* nested repository under the same working tree that git
    checks ownership of independently when git_push accesses it over the
    local filesystem transport. Marking only the parent working tree safe
    never covers that nested repo too.
    """

    def setUp(self):
        patcher = patch.object(base_module, "_SAFE_DIRECTORIES_CONFIGURED", set())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher2 = patch.object(base_module, "_SAFE_DIRECTORY_WILDCARD_SET", False)
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def test_does_not_shell_out_for_a_same_owner_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("subprocess.run") as mock_subprocess_run:
                _ensure_git_safe_directory(Path(tmp_dir))
            mock_subprocess_run.assert_not_called()

    def test_configures_safe_directory_for_a_different_owner_path(self):
        fake_path = Path("/app/state_repo")
        fake_stat = MagicMock(st_uid=12345)
        with (
            patch("pathlib.Path.stat", return_value=fake_stat),
            patch("os.geteuid", return_value=0, create=True),
            patch("subprocess.run") as mock_subprocess_run,
        ):
            mock_subprocess_run.return_value = MagicMock(returncode=0)
            _ensure_git_safe_directory(fake_path)

        mock_subprocess_run.assert_called_once()
        actual_cmd = mock_subprocess_run.call_args[0][0]
        self.assertEqual(actual_cmd, ["git", "config", "--global", "--add", "safe.directory", "*"])

    def test_only_runs_once_per_path_even_when_called_repeatedly(self):
        fake_path = Path("/app/state_repo")
        fake_stat = MagicMock(st_uid=12345)
        with (
            patch("pathlib.Path.stat", return_value=fake_stat),
            patch("os.geteuid", return_value=0, create=True),
            patch("subprocess.run") as mock_subprocess_run,
        ):
            mock_subprocess_run.return_value = MagicMock(returncode=0)
            _ensure_git_safe_directory(fake_path)
            _ensure_git_safe_directory(fake_path)
            _ensure_git_safe_directory(fake_path)

        mock_subprocess_run.assert_called_once()

    def test_a_second_different_mismatched_path_does_not_shell_out_again(self):
        """The scratch state repo's own local bare remote
        (./.state-repo-remote.git) is a genuinely different path from the
        working tree - once the wildcard is already set from fixing the
        first one, a second mismatched path must be covered for free, not
        trigger a second git config subprocess call."""
        first_path = Path("/app/state_repo")
        second_path = Path("/app/state_repo/.state-repo-remote.git")
        fake_stat = MagicMock(st_uid=12345)
        with (
            patch("pathlib.Path.stat", return_value=fake_stat),
            patch("os.geteuid", return_value=0, create=True),
            patch("subprocess.run") as mock_subprocess_run,
        ):
            mock_subprocess_run.return_value = MagicMock(returncode=0)
            _ensure_git_safe_directory(first_path)
            _ensure_git_safe_directory(second_path)

        mock_subprocess_run.assert_called_once()

    def test_swallows_a_failure_instead_of_raising(self):
        fake_path = Path("/app/state_repo")
        fake_stat = MagicMock(st_uid=12345)
        with (
            patch("pathlib.Path.stat", return_value=fake_stat),
            patch("os.geteuid", return_value=0, create=True),
            patch("subprocess.run", side_effect=OSError("git not found")),
        ):
            _ensure_git_safe_directory(fake_path)  # must not raise

    def test_configured_repo_root_calls_through_for_internal_state_repo_path(self):
        """_configured_repo_root's highest-priority branch (Docker mount
        point) is exactly the one that hit this in the real eval run -
        confirm the wiring actually reaches _ensure_git_safe_directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict("os.environ", {"INTERNAL_STATE_REPO_PATH": tmp_dir}, clear=True),
                patch("agents.scrum_team.tools.base._ensure_git_safe_directory") as mock_ensure,
            ):
                resolved = _configured_repo_root(tool_context=None)

        mock_ensure.assert_called_once_with(resolved)


if __name__ == "__main__":
    unittest.main()
