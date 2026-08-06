import json
import subprocess
from pathlib import Path

import pytest

import run_adk_eval


class TestParseArgs:
    def test_default_is_not_dry_run_ci_host_ollama_docker_ollama_or_debug(self):
        args = run_adk_eval.parse_args([])
        assert args.dry_run is False
        assert args.ci is False
        assert args.host_ollama is False
        assert args.docker_ollama is False
        assert args.debug is False
        assert args.env_file == ".env"

    def test_dry_run_flag(self):
        assert run_adk_eval.parse_args(["--dry-run"]).dry_run is True

    def test_ci_flag(self):
        assert run_adk_eval.parse_args(["--ci"]).ci is True

    def test_host_ollama_flag(self):
        assert run_adk_eval.parse_args(["--host-ollama"]).host_ollama is True

    def test_docker_ollama_flag(self):
        assert run_adk_eval.parse_args(["--docker-ollama"]).docker_ollama is True

    def test_ci_and_host_ollama_are_mutually_exclusive(self):
        try:
            run_adk_eval.parse_args(["--ci", "--host-ollama"])
        except SystemExit as e:
            assert e.code == 2  # argparse's own usage-error exit code
        else:
            raise AssertionError("expected SystemExit")

    def test_host_ollama_and_docker_ollama_are_mutually_exclusive(self):
        try:
            run_adk_eval.parse_args(["--host-ollama", "--docker-ollama"])
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("expected SystemExit")

    def test_debug_flag(self):
        assert run_adk_eval.parse_args(["--debug"]).debug is True

    def test_env_file_flag(self):
        assert run_adk_eval.parse_args(["--env-file", ".env.adk-eval"]).env_file == ".env.adk-eval"


class TestResolveHostOllama:
    """
    Acceptance Criteria: instead of always requiring an explicit
    --host-ollama flag, auto-detect the platform - Docker Desktop for Mac
    has no GPU passthrough at all (GH issue #93), so a dockerized Ollama
    there can never use the GPU, unlike Linux/Windows (where the dockerized
    `ollama` service already works fine, with optional NVIDIA GPU
    passthrough via docker-compose.gpu.yaml) - same precedent as
    setup_llm.py's host_ollama_default_enable. Explicit flags always win
    over the platform-based default either way.
    """

    def test_defaults_to_true_on_macos(self):
        args = run_adk_eval.parse_args([])
        assert run_adk_eval.resolve_host_ollama(args, platform="darwin") is True

    def test_defaults_to_false_elsewhere(self):
        args = run_adk_eval.parse_args([])
        assert run_adk_eval.resolve_host_ollama(args, platform="linux") is False
        assert run_adk_eval.resolve_host_ollama(args, platform="win32") is False

    def test_explicit_host_ollama_wins_regardless_of_platform(self):
        args = run_adk_eval.parse_args(["--host-ollama"])
        assert run_adk_eval.resolve_host_ollama(args, platform="linux") is True

    def test_explicit_docker_ollama_wins_even_on_macos(self):
        args = run_adk_eval.parse_args(["--docker-ollama"])
        assert run_adk_eval.resolve_host_ollama(args, platform="darwin") is False


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
        assert cmd[0:4] == ["python3", "eval/adk/run_eval_shim.py", "eval", "eval/adk/agent/scrum_team"]

    def test_uses_the_sequential_eval_runner_not_the_bare_adk_command(self):
        """
        Acceptance Criteria: `adk eval` runs 4 eval cases concurrently by
        default (its own hardcoded InferenceConfig/EvaluateConfig
        parallelism=4, no CLI flag to override) - interleaving 4 scripted
        conversations' tool-call logs made it impossible to tell which log
        line belonged to which scenario. run_eval_shim.py is a
        drop-in wrapper that forces parallelism=1 before delegating to the
        same CLI.
        """
        cmd = run_adk_eval.adk_eval_command()
        assert "adk" not in cmd
        assert cmd[0] == "python3"
        assert cmd[1] == run_adk_eval.EVAL_RUNNER_SHIM_PATH

    def test_includes_eval_set_and_config(self):
        """The generated evalset (real per-agent keys injected - see
        provision_and_generate_eval_set), not the checked-in template
        directly."""
        cmd = run_adk_eval.adk_eval_command()
        assert "eval/adk/scrum_team.evalset.generated.json" in cmd
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

    def test_host_ollama_mode_uses_hostollama_stack_and_config(self):
        """
        Acceptance Criteria: Docker Desktop (macOS/Windows) has no GPU
        passthrough, so a dockerized `ollama` service always runs CPU-only -
        --host-ollama must target the host-native compose file and its own
        dedicated config, with no OLLAMA_MODEL override (there's no
        dockerized `ollama` service to read it - ensure_host_ollama_ready
        handles the model directly on the host instead).
        """
        compose_args, extra_env = run_adk_eval.compose_setup(ci=False, host_ollama=True)
        assert compose_args[:2] == ["-f", "docker-compose.local-hostollama.yaml"]
        assert extra_env["LITELLM_CONFIG_PATH"] == run_adk_eval.HOST_OLLAMA_LITELLM_CONFIG
        assert "OLLAMA_MODEL" not in extra_env

    def test_all_modes_use_the_dedicated_eval_project_name(self):
        for ci, host_ollama in ((True, False), (False, False), (False, True)):
            compose_args, _ = run_adk_eval.compose_setup(ci=ci, host_ollama=host_ollama)
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


class TestEnsureHostOllamaReady:
    """
    Acceptance Criteria: --host-ollama's preflight - ollama CLI present,
    a native Ollama instance reachable, and the pinned model present
    (pulling it if not) - runs entirely on the host, before any docker
    compose command, so (unlike the dockerized case) there's no race to
    poll for: a synchronous `ollama pull` here can't race with anything
    since nothing else has started yet.
    """

    def test_fails_when_ollama_cli_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: None)
        assert run_adk_eval.ensure_host_ollama_ready("llama3.1:8b") is False
        assert "ollama" in capsys.readouterr().out.lower()

    def test_fails_when_ollama_not_reachable(self, monkeypatch, capsys):
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/local/bin/ollama")
        monkeypatch.setattr(run_adk_eval.lib_docker, "host_ollama_reachable", lambda: False)
        assert run_adk_eval.ensure_host_ollama_ready("llama3.1:8b") is False
        assert "ollama serve" in capsys.readouterr().out

    def test_succeeds_without_pulling_when_model_already_present(self, monkeypatch):
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/local/bin/ollama")
        monkeypatch.setattr(run_adk_eval.lib_docker, "host_ollama_reachable", lambda: True)

        calls = []

        class FakeResult:
            returncode = 0
            stdout = "llama3.1:8b\n"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        assert run_adk_eval.ensure_host_ollama_ready("llama3.1:8b") is True
        assert len(calls) == 1  # only `ollama list` - no pull needed
        assert calls[0] == ["ollama", "list"]

    def test_pulls_the_model_when_missing(self, monkeypatch):
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/local/bin/ollama")
        monkeypatch.setattr(run_adk_eval.lib_docker, "host_ollama_reachable", lambda: True)

        calls = []

        class ListResult:
            returncode = 0
            stdout = "some-other-model:7b\n"

        class PullResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return ListResult() if cmd == ["ollama", "list"] else PullResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        assert run_adk_eval.ensure_host_ollama_ready("llama3.1:8b") is True
        assert calls == [["ollama", "list"], ["ollama", "pull", "llama3.1:8b"]]

    def test_fails_when_pull_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/local/bin/ollama")
        monkeypatch.setattr(run_adk_eval.lib_docker, "host_ollama_reachable", lambda: True)

        class ListResult:
            returncode = 0
            stdout = ""

        class FailedPullResult:
            returncode = 1

        def fake_run(cmd, **kwargs):
            return ListResult() if cmd == ["ollama", "list"] else FailedPullResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        assert run_adk_eval.ensure_host_ollama_ready("llama3.1:8b") is False
        assert "pull" in capsys.readouterr().out.lower()


class TestReadEnvFileValue:
    def test_reads_plain_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("LITELLM_MASTER_KEY=sk-abc123\nOTHER=1\n")
        assert run_adk_eval._read_env_file_value(str(env_file), "LITELLM_MASTER_KEY") == "sk-abc123"

    def test_strips_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('LITELLM_MASTER_KEY="sk-abc123"\n')
        assert run_adk_eval._read_env_file_value(str(env_file), "LITELLM_MASTER_KEY") == "sk-abc123"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nLITELLM_MASTER_KEY=sk-abc123\n")
        assert run_adk_eval._read_env_file_value(str(env_file), "LITELLM_MASTER_KEY") == "sk-abc123"

    def test_missing_key_returns_empty_string(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=1\n")
        assert run_adk_eval._read_env_file_value(str(env_file), "LITELLM_MASTER_KEY") == ""

    def test_missing_file_returns_empty_string(self, tmp_path):
        assert run_adk_eval._read_env_file_value(str(tmp_path / "nope.env"), "LITELLM_MASTER_KEY") == ""


class TestProvisionLitellmKeys:
    """
    Acceptance Criteria: a real eval run showed 7/10 cases failing with
    "no LiteLLM virtual key yet" - not because the model misbehaved, but
    because the evalset template's fixture only pre-seeds ONE agent's key
    per case, and every OTHER role a model transferred to had none at all.
    provision_litellm_keys mints a real key for every specialist role up
    front, against the run's own live proxy, before the eval ever starts.
    """

    def _fake_urlopen(self, responses):
        import io

        class FakeResponse:
            def __init__(self, body):
                self._body = json.dumps(body).encode("utf-8")

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return FakeResponse(responses[len(calls) - 1])

        return fake_urlopen, calls

    def test_mints_one_key_per_agent(self, monkeypatch):
        responses = [{"key": f"sk-{i}"} for i in range(3)]
        fake_urlopen, calls = self._fake_urlopen(responses)
        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", fake_urlopen)

        keys = run_adk_eval.provision_litellm_keys("sk-master", ["ProductOwner", "DevTeam", "QA"])

        assert keys == {"ProductOwner": "sk-0", "DevTeam": "sk-1", "QA": "sk-2"}
        assert len(calls) == 3
        assert all(req.get_header("Authorization") == "Bearer sk-master" for req in calls)

    def test_raises_when_no_key_in_response(self, monkeypatch):
        fake_urlopen, _ = self._fake_urlopen([{"error": "boom"}])
        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(RuntimeError):
            run_adk_eval.provision_litellm_keys("sk-master", ["ProductOwner"])

    def test_does_not_send_a_key_alias(self, monkeypatch):
        """
        Acceptance Criteria: a real second local run failed with "Key with
        alias 'adk-eval-productowner' already exists" - local mode's `db`
        container's postgres_data volume is never dropped between runs
        (only --ci's teardown does `down -v`), so a fixed, deterministic
        key_alias collides with whatever a previous run already minted.
        """
        responses = [{"key": "sk-0"}]
        fake_urlopen, calls = self._fake_urlopen(responses)
        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", fake_urlopen)

        run_adk_eval.provision_litellm_keys("sk-master", ["ProductOwner"])

        sent_payload = json.loads(calls[0].data.decode("utf-8"))
        assert "key_alias" not in sent_payload

    def test_surfaces_response_body_on_http_error(self, monkeypatch):
        """The bare HTTPError alone (e.g. "HTTP Error 400: Bad Request") gave
        no clue what was actually wrong - LiteLLM's own error body (the
        exact validation message) must be included in the raised error."""
        import io
        import urllib.error

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url="http://localhost:4000/key/generate", code=400, msg="Bad Request",
                hdrs=None, fp=io.BytesIO(b'{"error": {"message": "Key with alias already exists"}}'),
            )

        monkeypatch.setattr(run_adk_eval.urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="Key with alias already exists"):
            run_adk_eval.provision_litellm_keys("sk-master", ["ProductOwner"])


class TestBuildEvalSetWithRealKeys:
    def _write_template(self, tmp_path, eval_ids_and_keys):
        cases = []
        for eval_id, fixture_keys in eval_ids_and_keys:
            cases.append({
                "eval_id": eval_id,
                "conversation": [],
                "session_input": {"state": {"litellm_keys": fixture_keys}},
            })
        template = tmp_path / "template.json"
        template.write_text(json.dumps({"eval_set_id": "x", "eval_cases": cases}))
        return template

    def test_injects_real_keys_for_every_case(self, tmp_path):
        template = self._write_template(tmp_path, [
            ("case_a", {"DevTeam": "eval-fixture-key-devteam"}),
            ("case_b", {"ProductOwner": "eval-fixture-key-po"}),
        ])
        output = tmp_path / "generated.json"
        real_keys = {"DevTeam": "sk-real-dev", "ProductOwner": "sk-real-po", "QA": "sk-real-qa"}

        run_adk_eval.build_eval_set_with_real_keys(str(template), str(output), real_keys)

        data = json.loads(output.read_text())
        for case in data["eval_cases"]:
            assert case["session_input"]["state"]["litellm_keys"] == real_keys

    def test_leaves_no_key_fixture_cases_untouched(self, tmp_path):
        no_key_id = next(iter(run_adk_eval.NO_KEY_FIXTURE_EVAL_IDS))
        template = self._write_template(tmp_path, [(no_key_id, {})])
        output = tmp_path / "generated.json"

        run_adk_eval.build_eval_set_with_real_keys(str(template), str(output), {"DevTeam": "sk-real-dev"})

        data = json.loads(output.read_text())
        assert data["eval_cases"][0]["session_input"]["state"]["litellm_keys"] == {}


class TestProvisionAndGenerateEvalSet:
    def test_returns_false_and_prints_error_without_master_key(self, capsys):
        assert run_adk_eval.provision_and_generate_eval_set("") is False
        assert "LITELLM_MASTER_KEY" in capsys.readouterr().out

    def test_returns_false_and_prints_error_on_provisioning_failure(self, monkeypatch, capsys):
        def raise_error(*a, **k):
            raise RuntimeError("proxy unreachable")

        monkeypatch.setattr(run_adk_eval, "provision_litellm_keys", raise_error)

        assert run_adk_eval.provision_and_generate_eval_set("sk-master") is False
        assert "proxy unreachable" in capsys.readouterr().out

    def test_writes_generated_eval_set_on_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        template_dir = tmp_path / "eval" / "adk"
        template_dir.mkdir(parents=True)
        (template_dir / "scrum_team.evalset.json").write_text(json.dumps({
            "eval_set_id": "x",
            "eval_cases": [{"eval_id": "case_a", "session_input": {"state": {"litellm_keys": {"DevTeam": "eval-fixture-key-devteam"}}}}],
        }))
        monkeypatch.setattr(run_adk_eval, "EVAL_SET_PATH", "eval/adk/scrum_team.evalset.json")
        monkeypatch.setattr(run_adk_eval, "GENERATED_EVAL_SET_PATH", "eval/adk/scrum_team.evalset.generated.json")
        monkeypatch.setattr(run_adk_eval, "provision_litellm_keys", lambda master_key, names: {n: f"sk-{n.lower()}" for n in names})

        assert run_adk_eval.provision_and_generate_eval_set("sk-master") is True

        data = json.loads((template_dir / "scrum_team.evalset.generated.json").read_text())
        assert data["eval_cases"][0]["session_input"]["state"]["litellm_keys"]["DevTeam"] == "sk-devteam"


class TestPrepareScratchStateRepo:
    """
    Acceptance Criteria: a real eval run's git_push calls were REAL git
    operations against whatever this developer's own .env STATE_REPO_PATH
    pointed at (their actual working project) - a real run committed
    __pycache__ files and fake spec/story markdown into it, and prompted to
    accept an unknown SSH host key for github.com. This must instead use its
    own disposable scratch repo with a local bare remote, wiped and
    recreated fresh on every call.
    """

    def test_creates_working_repo_with_initial_commit_on_main(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        path = run_adk_eval.prepare_scratch_state_repo()

        work_dir = tmp_path / "work"
        assert path == str(work_dir.resolve())
        assert (work_dir / ".git").is_dir()
        assert (work_dir / "README.md").is_file()
        log = subprocess.run(["git", "log", "--oneline"], cwd=work_dir, capture_output=True, text=True)
        assert log.returncode == 0
        assert len(log.stdout.strip().splitlines()) == 1
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=work_dir, capture_output=True, text=True)
        assert branch.stdout.strip() == "main"

    def test_creates_develop_branch_too(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        run_adk_eval.prepare_scratch_state_repo()

        branches = subprocess.run(["git", "branch"], cwd=tmp_path / "work", capture_output=True, text=True).stdout
        assert "develop" in branches

    def test_local_bare_remote_has_no_network_dependency(self, tmp_path, monkeypatch):
        """git_push's real `git push` must succeed against this remote with
        zero network access and no host-key prompt - a real bare local repo,
        not a placeholder URL."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        run_adk_eval.prepare_scratch_state_repo()

        work_dir = tmp_path / "work"
        push = subprocess.run(
            ["git", "checkout", "-B", "feature/test"], cwd=work_dir, capture_output=True, text=True,
        )
        assert push.returncode == 0
        (work_dir / "new.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "test commit"], cwd=work_dir, check=True)
        push = subprocess.run(
            ["git", "push", "-u", "origin", "feature/test"], cwd=work_dir, capture_output=True, text=True,
        )
        assert push.returncode == 0

    def test_remote_is_registered_as_a_relative_path_inside_the_working_tree(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria: a real eval run failed with "fatal: '/Users/.../
        eval-output/adk-state-repo-remote.git' does not appear to be a git
        repository" - a first attempt put the bare remote in a *sibling*
        directory and registered it via an absolute host path, which only
        the working tree itself is bind-mounted into the container
        (docker-compose.*.yaml's STATE_REPO_PATH -> /app/state_repo), so
        that absolute path resolved to nothing once git_push actually ran
        inside the container. The remote must live *inside* the working
        tree and be registered via a relative URL, so the same reference
        resolves correctly under any absolute path it's mounted at.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        run_adk_eval.prepare_scratch_state_repo()

        work_dir = tmp_path / "work"
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=work_dir, capture_output=True, text=True,
        ).stdout.strip()
        assert not Path(remote_url).is_absolute()
        assert (work_dir / run_adk_eval.STATE_REPO_SCRATCH_REMOTE_SUBDIR).is_dir()

        # Simulate the container seeing this same working tree at a
        # completely different absolute path than the host used to set it
        # up - a relative remote URL must still resolve correctly there.
        container_path = tmp_path / "elsewhere" / "state_repo"
        container_path.parent.mkdir()
        work_dir.rename(container_path)
        push = subprocess.run(
            ["git", "checkout", "-B", "feature/test"], cwd=container_path, capture_output=True, text=True,
        )
        assert push.returncode == 0
        (container_path / "new.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=container_path, check=True)
        subprocess.run(["git", "commit", "-m", "test commit"], cwd=container_path, check=True)
        push = subprocess.run(
            ["git", "push", "-u", "origin", "feature/test"], cwd=container_path, capture_output=True, text=True,
        )
        assert push.returncode == 0, push.stderr

    def test_remote_subdir_is_excluded_from_working_tree_commits(self, tmp_path, monkeypatch):
        """The bare remote living inside the working tree must never itself
        get swept up by `git add -A` as a nested repo/gitlink."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        run_adk_eval.prepare_scratch_state_repo()

        work_dir = tmp_path / "work"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=work_dir, capture_output=True, text=True).stdout
        assert run_adk_eval.STATE_REPO_SCRATCH_REMOTE_SUBDIR not in status

    def test_wipes_and_recreates_fresh_on_repeat_calls(self, tmp_path, monkeypatch):
        """A second run must start from the same clean state, not
        accumulate branches/commits left over from a previous run."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(run_adk_eval, "STATE_REPO_SCRATCH_DIR", "work")

        run_adk_eval.prepare_scratch_state_repo()
        work_dir = tmp_path / "work"
        subprocess.run(["git", "checkout", "-B", "feature/leftover"], cwd=work_dir, check=True, capture_output=True)

        run_adk_eval.prepare_scratch_state_repo()

        branches = subprocess.run(["git", "branch"], cwd=work_dir, capture_output=True, text=True).stdout
        assert "feature/leftover" not in branches


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
        # Covered by its own dedicated test classes below - default to
        # "provisioned fine" here so the rest of these tests (which predate
        # this step) aren't forced to fake out a real LiteLLM /key/generate
        # call just to reach the code they actually exercise.
        monkeypatch.setattr(run_adk_eval, "provision_and_generate_eval_set", lambda *a, **k: True)
        # Covered by its own dedicated test class below - default to a
        # no-op here so the rest of these tests (which predate this step,
        # and monkeypatch subprocess.run themselves to record docker compose
        # calls) aren't forced to fake out real `git` subprocess calls too.
        monkeypatch.setattr(run_adk_eval, "prepare_scratch_state_repo", lambda: None)
        # Pin resolve_host_ollama's platform-based default to "not macOS" so
        # every test below that doesn't pass --host-ollama/--docker-ollama
        # explicitly stays on the (dockerized) local mode they were written
        # against, deterministically, regardless of which OS actually runs
        # this suite - see TestResolveHostOllama for the platform-detection
        # behavior itself, and the host-ollama-specific tests below (which
        # pass --host-ollama explicitly, so this pin doesn't affect them).
        monkeypatch.setattr(run_adk_eval.sys, "platform", "linux")

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
        assert "run_eval_shim.py eval" in out
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
        assert run_adk_eval.EVAL_RUNNER_SHIM_PATH in calls[1]
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

    def test_default_run_does_not_force_debug_log_level(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria: LOG_LEVEL=debug used to be forced on every run
        regardless of --debug, which flooded the shell for routine runs -
        it must now be opt-in only.
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
        assert "LOG_LEVEL=debug" not in eval_run_cmd

    def test_debug_flag_forces_debug_log_level(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria: this eval set exists to catch gate-enforcement
        regressions against a live model - seeing the full request/response
        trace (e.g. the real LiteLLM error behind a canned "[CONNECTION
        ERROR]" response) matters when actually diagnosing a failure - --debug
        opts into that verbosity for this one run.
        """
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("LOG_LEVEL=info\n")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--debug"])

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

    def test_key_provisioning_failure_stops_before_running_eval(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])
        monkeypatch.setattr(run_adk_eval, "provision_and_generate_eval_set", lambda *a, **k: False)

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        # up ran, litellm was ready, but provisioning failed - the eval
        # itself must not run, but teardown still must.
        assert len(calls) == 2
        assert "adk" not in calls[1]
        assert "down" in calls[1]

    def test_master_key_resolved_from_env_file_when_not_in_process_environment(self, tmp_path, monkeypatch):
        """Local mode: docker compose reads --env-file directly, never into
        this host process's own os.environ - provision_and_generate_eval_set
        must still get the real LITELLM_MASTER_KEY via the .env-file fallback
        (_read_env_file_value), not an empty string."""
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        (tmp_path / ".env").write_text("LITELLM_MASTER_KEY=sk-from-env-file\n")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py"])

        seen_keys = []
        monkeypatch.setattr(
            run_adk_eval, "provision_and_generate_eval_set",
            lambda master_key: seen_keys.append(master_key) or True,
        )
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording([]))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        assert seen_keys == ["sk-from-env-file"]

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

    def test_host_ollama_mode_runs_full_lifecycle_without_dockerized_ollama_wait(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria: --host-ollama must never call wait_for_ollama_model
        (there's no dockerized `ollama` service to `docker compose exec`
        into in this mode) - ensure_host_ollama_ready handles the model
        directly on the host instead, before any docker compose command.
        """
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.setattr(run_adk_eval.lib_docker, "host_ollama_reachable", lambda: True)
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--host-ollama"])

        ollama_wait_calls = []
        monkeypatch.setattr(run_adk_eval, "wait_for_ollama_model", lambda *a, **k: ollama_wait_calls.append(1) or True)

        calls = []

        class FakeResult:
            def __init__(self, code=0, stdout=""):
                self.returncode = code
                self.stdout = stdout

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == ["ollama", "list"]:
                return FakeResult(stdout="llama3.1:8b\n")  # already present - no pull needed
            return FakeResult()

        monkeypatch.setattr(run_adk_eval.subprocess, "run", fake_run)

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 0

        assert ollama_wait_calls == []
        assert calls[0] == ["ollama", "list"]
        assert "docker-compose.local-hostollama.yaml" in calls[1]
        assert "run" in calls[2] and run_adk_eval.EVAL_RUNNER_SHIM_PATH in calls[2]
        assert "down" in calls[3]
        assert "-v" not in calls[3]  # host-ollama mode keeps host-managed volumes/state alone

    def test_host_ollama_preflight_failure_exits_before_any_docker_call(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text("")
        # docker present, ollama CLI missing.
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--host-ollama"])

        calls = []
        monkeypatch.setattr(run_adk_eval.subprocess, "run", self._fake_run_recording(calls))

        try:
            run_adk_eval.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError("expected SystemExit")

        assert calls == []  # no docker compose command (up/run/down) ever ran

    def test_auto_detects_host_ollama_on_macos_when_no_flag_passed(self, tmp_path, monkeypatch, capsys):
        """Acceptance Criteria: no --host-ollama flag needed on macOS - the
        platform itself is enough, since Docker Desktop there has no GPU
        passthrough at all regardless of what any individual developer
        wants. --dry-run is enough to observe this - it returns before any
        real docker/ollama command runs."""
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.sys, "platform", "darwin")  # override _isolate's own "linux" pin
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--dry-run"])

        run_adk_eval.main()

        out = capsys.readouterr().out
        assert "Detected macOS" in out
        assert "docker-compose.local-hostollama.yaml" in out

    def test_docker_ollama_flag_overrides_macos_auto_detection(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_adk_eval.sys, "platform", "darwin")
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(run_adk_eval.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(run_adk_eval.sys, "argv", ["run_adk_eval.py", "--dry-run", "--docker-ollama"])

        run_adk_eval.main()

        out = capsys.readouterr().out
        assert "Detected macOS" not in out
        assert "docker-compose.local-hostollama.yaml" not in out
        assert "docker-compose.local.yaml" in out

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
