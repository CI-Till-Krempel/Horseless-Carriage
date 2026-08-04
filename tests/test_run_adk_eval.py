import run_adk_eval


class TestParseArgs:
    def test_default_is_not_dry_run_or_ci(self):
        args = run_adk_eval.parse_args([])
        assert args.dry_run is False
        assert args.ci is False
        assert args.env_file == ".env"

    def test_dry_run_flag(self):
        assert run_adk_eval.parse_args(["--dry-run"]).dry_run is True

    def test_ci_flag(self):
        assert run_adk_eval.parse_args(["--ci"]).ci is True

    def test_env_file_flag(self):
        assert run_adk_eval.parse_args(["--env-file", ".env.adk-eval"]).env_file == ".env.adk-eval"


class TestAdkEvalCommand:
    """
    Acceptance Criteria: the exact command shape verified against the real
    installed `adk eval` CLI (see eval/adk/README.md's "The command to run
    it for real") - the agent-module path must be the loader shim
    (eval/adk/agent/scrum_team), not agents/scrum_team or agents directly
    (adk eval's loader is stricter than adk web/adk run - see that doc's
    "Deviation: a loader shim was required").
    """

    def test_uses_the_loader_shim_path(self):
        cmd = run_adk_eval.adk_eval_command()
        assert cmd[0:3] == ["adk", "eval", "eval/adk/agent/scrum_team"]

    def test_includes_eval_set_and_config(self):
        cmd = run_adk_eval.adk_eval_command()
        assert "eval/adk/scrum_team.evalset.json" in cmd
        assert "--config_file_path" in cmd
        assert "eval/adk/test_config.json" in cmd

    def test_includes_detailed_results_flag(self):
        assert "--print_detailed_results" in run_adk_eval.adk_eval_command()


class TestComposeSetup:
    """
    Acceptance Criteria: a real eval run once failed outright because the
    shared config/model-templates/litellm.local-ollama.yaml (freely rewritten
    by setup_llm.py for whatever this developer's own dev stack is configured
    for) had drifted out of sync with .env's OLLAMA_MODEL. compose_setup must
    NEVER depend on that shared, driftable config - local and --ci modes each
    resolve to a fixed, dedicated LiteLLM config + compose stack regardless
    of the calling machine's own setup.
    """

    def test_local_mode_uses_pinned_ollama_config_and_model(self):
        compose_args, extra_env = run_adk_eval.compose_setup(ci=False)
        assert compose_args[:2] == ["-f", "docker-compose.local.yaml"]
        assert extra_env["LITELLM_CONFIG_PATH"] == run_adk_eval.LOCAL_LITELLM_CONFIG
        assert extra_env["OLLAMA_MODEL"] == run_adk_eval.LOCAL_OLLAMA_MODEL

    def test_ci_mode_uses_cloud_stack_and_cheap_config(self):
        compose_args, extra_env = run_adk_eval.compose_setup(ci=True)
        # No -f docker-compose.local.yaml - the cloud stack (docker-compose.yaml,
        # no Ollama) is Compose's implicit default with no -f flag at all.
        assert "docker-compose.local.yaml" not in compose_args
        assert extra_env["LITELLM_CONFIG_PATH"] == run_adk_eval.CI_LITELLM_CONFIG
        assert "OLLAMA_MODEL" not in extra_env

    def test_both_modes_use_the_dedicated_eval_project_name(self):
        for ci in (True, False):
            compose_args, _ = run_adk_eval.compose_setup(ci=ci)
            assert "-p" in compose_args
            assert compose_args[compose_args.index("-p") + 1] == "horseless-carriage-eval"


class TestWaitForLitellmReady:
    def test_returns_true_on_first_successful_response(self, monkeypatch):
        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert run_adk_eval.wait_for_litellm_ready(timeout_seconds=5) is True

    def test_returns_false_on_timeout(self, monkeypatch):
        def always_fails(*a, **k):
            raise run_adk_eval.urllib.error.URLError("connection refused")

        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", always_fails)
        monkeypatch.setattr(run_adk_eval.time, "sleep", lambda _s: None)
        assert run_adk_eval.wait_for_litellm_ready(timeout_seconds=0.01) is False


class TestWaitForOllamaModel:
    """
    Acceptance Criteria: a real eval run kept failing with "model
    'llama3.1:8b' not found" even after the model config and OLLAMA_MODEL
    were made consistent - ollama-entrypoint.sh backgrounds `ollama serve`
    (accepting connections immediately) and only pulls the model as a
    separate step afterward, which `docker compose up -d`'s own return code
    never waits for. This must poll until the model genuinely shows up in
    `ollama list`, not just until the container process has started.
    """

    def test_returns_true_once_model_appears(self, monkeypatch):
        class FakeResult:
            returncode = 0
            stdout = "NAME              ID              SIZE\nllama3.1:8b       abc123          4.7 GB\n"

        monkeypatch.setattr(run_adk_eval.subprocess, "run", lambda *a, **k: FakeResult())
        assert run_adk_eval.wait_for_ollama_model([], ".env", {}, "llama3.1:8b", timeout_seconds=5) is True

    def test_returns_false_on_timeout_while_still_pulling(self, monkeypatch):
        class FakeResult:
            returncode = 0
            stdout = "NAME    ID    SIZE\n"  # model not listed yet - still pulling

        monkeypatch.setattr(run_adk_eval.subprocess, "run", lambda *a, **k: FakeResult())
        monkeypatch.setattr(run_adk_eval.time, "sleep", lambda _s: None)
        assert run_adk_eval.wait_for_ollama_model([], ".env", {}, "llama3.1:8b", timeout_seconds=0.01) is False

    def test_tolerates_exec_failing_before_ollama_is_ready(self, monkeypatch):
        """`docker compose exec` can itself fail transiently right after the
        container starts, before Ollama's own serve process is listening -
        this must keep polling, not treat that as a hard failure."""
        calls = []

        class FailResult:
            returncode = 1
            stdout = ""

        class OkResult:
            returncode = 0
            stdout = "llama3.1:8b\n"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FailResult() if len(calls) == 1 else OkResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)
        monkeypatch.setattr(run_adk_eval.time, "sleep", lambda _s: None)
        assert run_adk_eval.wait_for_ollama_model([], ".env", {}, "llama3.1:8b", timeout_seconds=5) is True
        assert len(calls) == 2


class TestHcVersionAndCommit:
    """GH issue #167/#168: unlike the team-performance harness's
    run_eval.py (which runs inside the agent container, whose image
    deliberately excludes .git - GH issue #123), this script runs on the
    host, so it can read VERSION/git directly rather than depending on an
    env var being pre-set."""

    def test_reads_real_version_and_commit(self):
        version, commit = run_adk_eval.hc_version_and_commit()
        assert version != "unknown"
        assert commit != "unknown"
        assert len(commit) == 40  # a full git SHA

    def test_falls_back_to_unknown_when_git_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            run_adk_eval.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no git")),
        )
        _, commit = run_adk_eval.hc_version_and_commit()
        assert commit == "unknown"

    def test_falls_back_to_unknown_when_version_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        version, _ = run_adk_eval.hc_version_and_commit()
        assert version == "unknown"


class TestMain:
    """os.chdir is no-op'd in every test the same way tests/test_run.py and
    tests/test_setup_project.py do it for their own os.chdir(Path(__file__)
    ...) pattern - combined with pytest's own monkeypatch.chdir(tmp_path),
    this keeps main() operating against an isolated tmp_path instead of the
    real repo's working directory/.env."""

    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval.os, "chdir", lambda _path: None)
        # hc_version_and_commit() shells out to `git` - unrelated to what
        # these tests exercise, and would otherwise be swept up by the
        # docker-focused subprocess.run monkeypatches below.
        monkeypatch.setattr(run_adk_eval, "hc_version_and_commit", lambda: ("0.1.0", "abc1234"))
        # Both readiness waits are covered by their own dedicated test
        # classes above - default them to "ready immediately" here so the
        # rest of these tests aren't all forced to fake out real polling/
        # sleep loops just to reach the code they actually exercise.
        monkeypatch.setattr(run_adk_eval, "wait_for_litellm_ready", lambda *a, **k: True)
        monkeypatch.setattr(run_adk_eval, "wait_for_ollama_model", lambda *a, **k: True)

    def _fake_run_recording(self, calls, returncodes=None):
        results = iter(returncodes) if returncodes is not None else None

        class FakeResult:
            def __init__(self, code):
                self.returncode = code

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult(next(results) if results is not None else 0)

        return fake_run

    def test_missing_docker_exits(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")
        assert "docker" in capsys.readouterr().out.lower()

    def test_prints_version_and_commit_before_anything_else(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        try:
            run_adk_eval.main()
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "--- Horseless Carriage v0.1.0 (commit abc1234) ---"

    def test_missing_env_exits(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")
        assert ".env" in capsys.readouterr().out

    def test_missing_custom_env_file_exits(self, tmp_path, monkeypatch, capsys):
        """--env-file must actually be honored for the missing-file check too,
        not just hardcode ".env"."""
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--env-file", ".env.adk-eval"])
        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")
        assert ".env.adk-eval" in capsys.readouterr().out

    def test_dry_run_prints_commands_without_running_docker(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--dry-run"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", lambda *a, **k: calls.append(a) or None)

        run_adk_eval.main()

        assert calls == []
        out = capsys.readouterr().out
        assert "adk eval" in out
        assert "up -d db litellm" in out
        assert "down" in out

    def test_real_run_brings_up_services_runs_eval_then_tears_down(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        # Acceptance Criteria: the eval stack must be torn down after the run,
        # not just brought up and left running (`restart: unless-stopped` +
        # no teardown previously meant every run leaked containers).
        assert len(calls) == 3
        assert calls[0][-3:] == ["up", "-d", "db"] or "litellm" in calls[0]
        assert "run" in calls[1]
        assert "adk" in calls[1]
        assert "down" in calls[2]
        assert "-v" not in calls[2]  # local mode keeps the pulled-model volume

    def test_ci_teardown_removes_volumes(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--ci"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        assert "-v" in calls[2]  # ephemeral CI runner - matches eval.yml's own `down -v`

    def test_eval_run_forces_debug_log_level(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria: this eval set exists to catch gate-enforcement
        regressions against a live model - seeing the full request/response
        trace (e.g. the real LiteLLM error behind a canned "[CONNECTION
        ERROR]" response) matters every time it runs, so LOG_LEVEL=debug is
        forced here regardless of whatever the shared .env's LOG_LEVEL is
        set to for the normal dev stack (run.py).
        """
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("LOG_LEVEL=info\n")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        eval_run_cmd = calls[1]
        assert "-e" in eval_run_cmd
        assert eval_run_cmd[eval_run_cmd.index("-e") + 1] == "LOG_LEVEL=debug"

    def test_up_failure_still_tears_down_but_does_not_run_eval(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls, returncodes=[1, 0]))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        # up failed -> the eval itself must not run, but teardown still must.
        assert len(calls) == 2
        assert "adk" not in calls[1]
        assert "down" in calls[1]

    def test_litellm_not_ready_stops_before_running_eval(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        monkeypatch.setattr(run_adk_eval, "wait_for_litellm_ready", lambda *a, **k: False)

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        # up ran, the eval itself must not, but teardown still must.
        assert len(calls) == 2
        assert "adk" not in calls[1]
        assert "down" in calls[1]

    def test_ollama_model_not_ready_stops_before_running_eval_local_only(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        monkeypatch.setattr(run_adk_eval, "wait_for_ollama_model", lambda *a, **k: False)

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        assert len(calls) == 2
        assert "adk" not in calls[1]
        assert "down" in calls[1]

    def test_ci_mode_never_waits_for_an_ollama_model(self, tmp_path, monkeypatch):
        """--ci uses a cloud model - there's no Ollama pull step to wait for,
        and no Ollama container in that stack at all."""
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--ci"])

        ollama_wait_calls = []
        monkeypatch.setattr(run_adk_eval, "wait_for_ollama_model", lambda *a, **k: ollama_wait_calls.append(1) or True)

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        assert ollama_wait_calls == []
        assert len(calls) == 3  # up, run, down - eval still actually ran

    def test_eval_failure_exit_code_propagates_after_teardown(self, tmp_path, monkeypatch):
        """Acceptance Criteria: teardown must never swallow the eval's own
        pass/fail signal - a CI job needs main() to still exit non-zero when
        the eval set itself reports failures, even though a `down` call runs
        afterward and succeeds."""
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls, returncodes=[0, 1, 0]))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        assert len(calls) == 3
        assert "down" in calls[2]
