import run_adk_eval


class TestParseArgs:
    def test_default_is_not_dry_run(self):
        assert run_adk_eval.parse_args([]) is False

    def test_dry_run_flag(self):
        assert run_adk_eval.parse_args(["--dry-run"]) is True


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

    def test_dry_run_prints_commands_without_running_docker(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.lib_docker, "compose_file_args", lambda repo_root: [])
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--dry-run"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", lambda *a, **k: calls.append(a) or None)

        run_adk_eval.main()

        assert calls == []
        out = capsys.readouterr().out
        assert "adk eval" in out
        assert "up -d db litellm" in out

    def test_real_run_brings_up_services_then_runs_eval(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.lib_docker, "compose_file_args", lambda repo_root: [])
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        assert len(calls) == 2
        assert calls[0][-3:] == ["up", "-d", "db"] or "litellm" in calls[0]
        assert "run" in calls[1]
        assert "adk" in calls[1]

    def test_up_failure_stops_before_running_eval(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.lib_docker, "compose_file_args", lambda repo_root: [])
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        calls = []

        class FakeResult:
            def __init__(self, code):
                self.returncode = code

        results = iter([FakeResult(1)])

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return next(results)

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        assert len(calls) == 1
