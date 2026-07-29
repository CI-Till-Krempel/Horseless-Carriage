import json
import subprocess
import urllib.error
from unittest import mock

import pytest
import yaml

import lib_env
import setup_llm


class TestPromptText:
    def test_empty_input_uses_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert setup_llm.prompt_text("Name", "DevTeam") == "DevTeam"

    def test_explicit_value_with_spaces(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "Jane Doe")
        assert setup_llm.prompt_text("Name", "DevTeam") == "Jane Doe"

    def test_invalid_then_valid_retries(self, monkeypatch, capsys):
        answers = iter(["not-an-email", "still bad", "jane@example.com"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        result = setup_llm.prompt_text(
            "Email", "devteam@company.com",
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "bad email",
        )
        assert result == "jane@example.com"
        assert capsys.readouterr().err.count("bad email") == 2


class TestPromptNumber:
    def test_delegates_to_prompt_text_with_number_error(self, monkeypatch, capsys):
        answers = iter(["abc", "42"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        result = setup_llm.prompt_number("Overhead", "20", r"^[0-9]+(\.[0-9]+)?$")
        assert result == "42"
        assert "Please enter a number." in capsys.readouterr().err

    def test_default_on_empty_input(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert setup_llm.prompt_number("Budget", "0.50", r"^[0-9]+(\.[0-9]+)?$") == "0.50"


class TestSelectModel:
    def test_numbered_choice(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "2")
        assert setup_llm.select_model("Pick", ["a", "b", "c"]) == "b"

    def test_empty_input_defaults_to_first(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert setup_llm.select_model("Pick", ["a", "b", "c"]) == "a"

    def test_custom_entry(self, monkeypatch):
        answers = iter(["4", "custom-model-x"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        assert setup_llm.select_model("Pick", ["a", "b", "c"]) == "custom-model-x"

    def test_invalid_selection_exits(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "99")
        with pytest.raises(SystemExit):
            setup_llm.select_model("Pick", ["a", "b", "c"])

    def test_current_in_options_becomes_default_and_marked(self, monkeypatch, capsys):
        prompts = []
        monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
        result = setup_llm.select_model("Pick", ["a", "b", "c"], current="b")
        assert result == "b"
        assert "2) b (current)" in capsys.readouterr().out
        assert prompts == ["Pick [2]: "]

    def test_current_in_options_can_still_be_overridden(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "1")
        assert setup_llm.select_model("Pick", ["a", "b", "c"], current="b") == "a"

    def test_current_not_in_options_kept_on_empty_input(self, monkeypatch):
        prompts = []
        monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
        result = setup_llm.select_model("Pick", ["a", "b", "c"], current="deprecated-model")
        assert result == "deprecated-model"
        assert prompts == ["Pick [deprecated-model]: "]

    def test_current_not_in_options_still_allows_numbered_choice(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "2")
        assert setup_llm.select_model("Pick", ["a", "b", "c"], current="deprecated-model") == "b"


class TestDetectCheapHint:
    def test_finds_hint_case_insensitively(self):
        assert setup_llm.detect_cheap_hint(["gpt-4.1", "gpt-4o", "gpt-4o-MINI"]) == 3

    def test_returns_none_when_no_hint(self):
        assert setup_llm.detect_cheap_hint(["claude-sonnet-5", "claude-opus-4-8"]) is None

    def test_matches_flash_lite_haiku_nano_small(self):
        assert setup_llm.detect_cheap_hint(["big-model", "some-flash-variant"]) == 2
        assert setup_llm.detect_cheap_hint(["big-model", "x-lite"]) == 2
        assert setup_llm.detect_cheap_hint(["big-model", "x-haiku"]) == 2
        assert setup_llm.detect_cheap_hint(["big-model", "x-nano"]) == 2
        assert setup_llm.detect_cheap_hint(["big-model", "x-small"]) == 2


class TestWriteLitellmYaml:
    ROLES_PLUS_EXTRAS = [
        "scrum-orchestrator", "scrum-po", "scrum-sm", "scrum-dev", "scrum-qa",
        "scrum-arch", "scrum-quality", "scrum-eval-cheap", "scrum-test-mock",
    ]

    def _models_by_alias(self, out_file):
        data = yaml.safe_load(out_file.read_text())
        return {e["model_name"]: e["litellm_params"]["model"] for e in data["model_list"]}

    def test_gemini(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("gemini", "gemini-2.5-pro", "gemini-2.5-flash", out_file)
        models = self._models_by_alias(out_file)
        assert set(models) == set(self.ROLES_PLUS_EXTRAS)
        assert models["scrum-po"] == "gemini/gemini-2.5-pro"
        assert models["scrum-eval-cheap"] == "gemini/gemini-2.5-flash"
        assert models["scrum-test-mock"] == "openai/gpt-3.5-turbo"
        data = yaml.safe_load(out_file.read_text())
        assert "general_settings" in data
        po_entry = next(e for e in data["model_list"] if e["model_name"] == "scrum-po")
        assert "safety_settings" in po_entry["litellm_params"]

    def test_anthropic(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("anthropic", "claude-sonnet-5", "claude-haiku-4-5-20251001", out_file)
        models = self._models_by_alias(out_file)
        assert models["scrum-po"] == "anthropic/claude-sonnet-5"
        assert models["scrum-eval-cheap"] == "anthropic/claude-haiku-4-5-20251001"

    def test_openai_no_substring_collision_between_main_and_cheap(self, tmp_path):
        # gpt-4o is a substring of gpt-4o-mini - make sure every main-role
        # entry gets the main model, not accidentally the cheap one or vice versa.
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("openai", "gpt-4o", "gpt-4o-mini", out_file)
        models = self._models_by_alias(out_file)
        for role in ["scrum-orchestrator", "scrum-po", "scrum-sm", "scrum-dev",
                     "scrum-qa", "scrum-arch", "scrum-quality"]:
            assert models[role] == "openai/gpt-4o"
        assert models["scrum-eval-cheap"] == "openai/gpt-4o-mini"

    def test_local_ollama_uses_api_base_not_api_key(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("local", "llama3.1:8b", "llama3.1:8b", out_file)
        data = yaml.safe_load(out_file.read_text())
        po_entry = next(e for e in data["model_list"] if e["model_name"] == "scrum-po")
        assert po_entry["litellm_params"]["model"] == "ollama/llama3.1:8b"
        assert po_entry["litellm_params"]["api_base"] == "http://ollama:11434"
        assert "api_key" not in po_entry["litellm_params"]

    def test_creates_parent_directories(self, tmp_path):
        out_file = tmp_path / "nested" / "dir" / "litellm.yaml"
        setup_llm.write_litellm_yaml("openai", "gpt-4o", "gpt-4o-mini", out_file)
        assert out_file.is_file()


class TestCurrentModelForRole:
    """
    Acceptance Criteria: re-running setup_llm.py must prefill the model
    prompts with whatever's already configured (read back from a file this
    script itself previously wrote via write_litellm_yaml/emit_model_entry)
    instead of always defaulting to the freshly fetched list's first entry.
    """

    def test_reads_back_main_role_model(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("gemini", "gemini-2.5-pro", "gemini-2.5-flash", out_file)
        assert setup_llm.current_model_for_role(out_file, "scrum-po") == "gemini-2.5-pro"

    def test_reads_back_cheap_role_model(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("openai", "gpt-4o", "gpt-4o-mini", out_file)
        assert setup_llm.current_model_for_role(out_file, "scrum-eval-cheap") == "gpt-4o-mini"

    def test_ollama_tag_with_colon_is_read_back_whole(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("local", "llama3.1:8b", "llama3.1:8b", out_file)
        assert setup_llm.current_model_for_role(out_file, "scrum-po") == "llama3.1:8b"

    def test_missing_file_returns_empty_string(self, tmp_path):
        assert setup_llm.current_model_for_role(tmp_path / "does-not-exist.yaml", "scrum-po") == ""

    def test_role_not_present_returns_empty_string(self, tmp_path):
        out_file = tmp_path / "litellm.yaml"
        setup_llm.write_litellm_yaml("gemini", "gemini-2.5-pro", "gemini-2.5-flash", out_file)
        assert setup_llm.current_model_for_role(out_file, "scrum-nonexistent-role") == ""


class TestDetectNvidiaGpu:
    def test_macos_is_always_false(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "darwin")
        assert setup_llm.detect_nvidia_gpu() is False

    def test_missing_binary_is_false(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "linux")
        monkeypatch.setattr(setup_llm.shutil, "which", lambda _cmd: None)
        assert setup_llm.detect_nvidia_gpu() is False

    def test_binary_present_and_reports_a_gpu_is_true(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "linux")
        monkeypatch.setattr(setup_llm.shutil, "which", lambda _cmd: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="NVIDIA GeForce RTX 4090\n"),
        )
        assert setup_llm.detect_nvidia_gpu() is True

    def test_binary_present_but_errors_is_false(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "linux")
        monkeypatch.setattr(setup_llm.shutil, "which", lambda _cmd: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess([], 1, stdout=""),
        )
        assert setup_llm.detect_nvidia_gpu() is False

    def test_binary_present_but_empty_output_is_false(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "linux")
        monkeypatch.setattr(setup_llm.shutil, "which", lambda _cmd: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="   \n"),
        )
        assert setup_llm.detect_nvidia_gpu() is False

    def test_exception_running_nvidia_smi_does_not_crash_and_is_false(self, monkeypatch):
        monkeypatch.setattr(setup_llm.sys, "platform", "linux")
        monkeypatch.setattr(setup_llm.shutil, "which", lambda _cmd: "/usr/bin/nvidia-smi")

        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)
        monkeypatch.setattr(setup_llm.subprocess, "run", raise_timeout)
        assert setup_llm.detect_nvidia_gpu() is False


class TestGpuDefaultEnable:
    """
    Acceptance Criteria: an explicit prior choice (re-running setup_llm.py)
    takes priority over the fresh detection result - prefilling the user's
    own current setup wins over a recommendation.
    """

    def test_no_prior_choice_follows_detection_true(self):
        assert setup_llm.gpu_default_enable(gpu_detected=True, current_value="") is True

    def test_no_prior_choice_follows_detection_false(self):
        assert setup_llm.gpu_default_enable(gpu_detected=False, current_value="") is False

    def test_prior_true_wins_even_if_now_undetected(self):
        assert setup_llm.gpu_default_enable(gpu_detected=False, current_value="true") is True

    def test_prior_false_wins_even_if_now_detected(self):
        assert setup_llm.gpu_default_enable(gpu_detected=True, current_value="false") is False


class TestCurrentInteractionLevelChoice:
    """
    Acceptance Criteria: re-running setup_llm.py must default the Human
    Interaction Level prompt to whatever's already configured, not always
    reset to "1" (Product) regardless of the existing .env.
    """

    def test_no_env_file_defaults_to_product(self, tmp_path):
        assert setup_llm.current_interaction_level_choice(tmp_path / ".env") == "1"

    def test_reads_back_stakeholder(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        lib_env.update_env_var(env_path, "INTERACTION_LEVEL", "Stakeholder")
        assert setup_llm.current_interaction_level_choice(env_path) == "2"

    def test_reads_back_ceo(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        lib_env.update_env_var(env_path, "INTERACTION_LEVEL", "CEO")
        assert setup_llm.current_interaction_level_choice(env_path) == "3"

    def test_unrecognized_value_defaults_to_product(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        lib_env.update_env_var(env_path, "INTERACTION_LEVEL", "Bogus")
        assert setup_llm.current_interaction_level_choice(env_path) == "1"


class TestRunConfigurationTest:
    """
    Acceptance Criteria (GH issue #36): when `docker compose up -d` fails,
    setup_llm.py must show the real error instead of a blind "is the
    Docker daemon running?" guess - on at least one real Windows run the
    daemon *was* running and this fired anyway, with the actual cause
    hidden because stdout/stderr were sent to DEVNULL.
    """

    def test_compose_up_failure_surfaces_real_stderr(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["docker", "compose", "version"]:
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error response from daemon: some specific real docker error\n")

        def fake_check_run(cmd, **kwargs):
            result = fake_run(cmd, **kwargs)
            if kwargs.get("check") and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
            return result

        monkeypatch.setattr(setup_llm.subprocess, "run", fake_check_run)
        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("gemini", "gemini-2.5-pro", env_path)

        out = capsys.readouterr().out
        assert "some specific real docker error" in out
        assert "is the Docker daemon running?" not in out

    def test_offers_to_stop_an_existing_stack_before_starting(self, tmp_path, monkeypatch):
        """A leftover stack from an earlier run must get a chance to be
        stopped+recreated before this starts a new one on top of it (see
        lib_docker.maybe_stop_existing_stack) - and that check must happen
        for the right compose file (docker-compose.local.yaml for a local
        provider) before the actual `up -d` call, not after."""
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))

        calls = []
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: calls.append(("stop_check", compose_args)))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)

        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path)

        assert len(calls) == 1
        _, compose_args = calls[0]
        assert compose_args[:2] == ["-f", "docker-compose.local.yaml"]

    def test_gpu_enabled_adds_gpu_compose_file(self, tmp_path, monkeypatch):
        """OLLAMA_GPU_ENABLED=true in .env must merge in docker-compose.gpu.yaml
        for the live test too, not just for run.py later - a GPU
        misconfiguration should surface here, not only after setup."""
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))

        calls = []
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: calls.append(compose_args))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)

        env_path = tmp_path / ".env"
        env_path.write_text("")
        lib_env.update_env_var(env_path, "OLLAMA_GPU_ENABLED", "true")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path)

        assert calls[0][:4] == ["-f", "docker-compose.local.yaml", "-f", "docker-compose.gpu.yaml"]

    def test_gpu_disabled_omits_gpu_compose_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))

        calls = []
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: calls.append(compose_args))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)

        env_path = tmp_path / ".env"
        env_path.write_text("")
        lib_env.update_env_var(env_path, "OLLAMA_GPU_ENABLED", "false")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path)

        assert calls[0][:2] == ["-f", "docker-compose.local.yaml"]
        assert "docker-compose.gpu.yaml" not in calls[0]

    def test_dev_mode_rebuilds_ollama_before_starting_it_for_local_provider(self, tmp_path, monkeypatch):
        """
        Acceptance Criteria (ISSUE-0028): developer mode must be settled
        before any container work - if dev=True and the provider is Local/
        Ollama, the ollama image is rebuilt fresh (rebuild_images.rebuild)
        BEFORE this test starts a container with it, not after (which
        would validate a stale image dev mode was about to replace anyway).
        """
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)

        events = []
        monkeypatch.setattr(setup_llm.rebuild_images, "rebuild", lambda compose_args: events.append(("rebuild", compose_args)) or 0)
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: events.append(("stop_check", compose_args)))

        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path, dev=True)

        assert [name for name, _ in events] == ["rebuild", "stop_check"]
        assert events[0][1][:2] == ["-f", "docker-compose.local.yaml"]

    def test_dev_mode_false_does_not_rebuild(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: None)

        def fail_if_called(compose_args):
            raise AssertionError("rebuild_images.rebuild should not run when dev=False")
        monkeypatch.setattr(setup_llm.rebuild_images, "rebuild", fail_if_called)

        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path, dev=False)

    def test_dev_mode_has_no_effect_for_cloud_providers(self, tmp_path, monkeypatch):
        """Cloud providers never start a locally-built image (litellm is a
        pulled release image, db is postgres) - dev=True must not trigger
        a rebuild for them."""
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: None)

        def fail_if_called(compose_args):
            raise AssertionError("rebuild_images.rebuild should not run for a cloud provider")
        monkeypatch.setattr(setup_llm.rebuild_images, "rebuild", fail_if_called)

        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("gemini", "gemini-2.5-pro", env_path, dev=True)

    def test_rebuild_failure_warns_but_still_starts_containers(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: "/usr/bin/docker")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(setup_llm.subprocess, "run", fake_run)
        monkeypatch.setattr(setup_llm.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)
        monkeypatch.setattr(setup_llm.lib_docker, "maybe_stop_existing_stack", lambda compose_args: None)
        monkeypatch.setattr(setup_llm.rebuild_images, "rebuild", lambda compose_args: 1)

        env_path = tmp_path / ".env"
        env_path.write_text("")

        setup_llm.run_configuration_test("local", "llama3.1:8b", env_path, dev=True)

        captured = capsys.readouterr()
        assert "Rebuilding the ollama image failed" in captured.out + captured.err
        assert any(c[:2] == ["docker", "compose"] and "up" in c for c in calls)


def _fake_urlopen_response(body_dict):
    data = json.dumps(body_dict).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = data
    cm.__exit__.return_value = False
    return cm


class TestFetchGeminiModels:
    def test_filters_non_chat_and_strips_prefix(self, monkeypatch):
        body = {"models": [
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]}
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_urlopen_response(body))
        result = setup_llm.fetch_gemini_models("fake-key")
        assert result == ["gemini-2.5-pro", "gemini-2.5-flash"]

    def test_http_error_returns_empty_list(self, monkeypatch, capsys):
        def raise_error(*a, **k):
            raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        monkeypatch.setattr("urllib.request.urlopen", raise_error)
        assert setup_llm.fetch_gemini_models("bad-key") == []
        assert "401" in capsys.readouterr().err


class TestFetchAnthropicModels:
    def test_sorted_newest_first(self, monkeypatch):
        body = {"data": [
            {"id": "claude-haiku-4-5-20251001", "created_at": "2025-10-01T00:00:00Z"},
            {"id": "claude-sonnet-5", "created_at": "2026-05-01T00:00:00Z"},
            {"id": "claude-opus-4-8", "created_at": "2026-01-01T00:00:00Z"},
        ]}
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_urlopen_response(body))
        result = setup_llm.fetch_anthropic_models("fake-key")
        assert result == ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001"]


class TestFetchOpenaiModels:
    def test_excludes_non_chat_models_and_sorts_newest_first(self, monkeypatch):
        body = {"data": [
            {"id": "gpt-4o", "created": 1000},
            {"id": "gpt-4o-mini", "created": 1500},
            {"id": "text-embedding-3-large", "created": 2000},
            {"id": "whisper-1", "created": 500},
            {"id": "gpt-4.1", "created": 1800},
        ]}
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_urlopen_response(body))
        result = setup_llm.fetch_openai_models("fake-key")
        assert result == ["gpt-4.1", "gpt-4o-mini", "gpt-4o"]


@pytest.fixture
def fake_git_remote(tmp_path):
    """A local bare git repo with one commit on 'main' - a clone source that
    needs no network access, standing in for a real GitHub remote."""
    remote_dir = tmp_path / "fake-remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote_dir)], check=True)

    seed_dir = tmp_path / "_seed"
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(seed_dir)], check=True)
    subprocess.run(["git", "-C", str(seed_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(seed_dir), "config", "user.name", "Test"], check=True)
    (seed_dir / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(seed_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(seed_dir), "commit", "--quiet", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(seed_dir), "remote", "add", "origin", str(remote_dir)], check=True)
    subprocess.run(["git", "-C", str(seed_dir), "push", "--quiet", "-u", "origin", "main"], check=True)

    return str(remote_dir)


def _run_setup_state_repo(monkeypatch, env_path, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(it))
    setup_llm._setup_state_repo(env_path)


class TestGitSshHost:
    def test_scp_style_url(self):
        assert setup_llm._git_ssh_host("git@github.com:owner/repo.git") == "github.com"

    def test_ssh_scheme_url_with_user(self):
        assert setup_llm._git_ssh_host("ssh://git@github.com/owner/repo.git") == "github.com"

    def test_ssh_scheme_url_without_user(self):
        assert setup_llm._git_ssh_host("ssh://github.com/owner/repo.git") == "github.com"

    def test_https_url_returns_none(self):
        assert setup_llm._git_ssh_host("https://github.com/owner/repo.git") is None

    def test_local_path_returns_none(self):
        assert setup_llm._git_ssh_host("/tmp/some/local/repo.git") is None


class TestCheckGitSshAuth:
    """
    Acceptance Criteria (GH issue #30): setup_llm.py must verify SSH auth
    works before attempting a clone, and must never crash regardless of
    what the check finds (missing ssh binary, timeout, unrecognized host
    behavior) - this is a best-effort hint, not a hard gate.
    """

    def test_successfully_authenticated_output_is_true(self, monkeypatch):
        # GitHub deliberately exits non-zero here even on real success -
        # detection must be text-based, not exit-code-based.
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: mock.Mock(
                stdout="Hi someone! You've successfully authenticated, but GitHub does not provide shell access.\n",
                stderr="",
            ),
        )
        assert setup_llm._check_git_ssh_auth("github.com") is True

    def test_permission_denied_output_is_false(self, monkeypatch):
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: mock.Mock(stdout="", stderr="git@github.com: Permission denied (publickey).\n"),
        )
        assert setup_llm._check_git_ssh_auth("github.com") is False

    def test_could_not_resolve_hostname_is_false(self, monkeypatch):
        monkeypatch.setattr(
            setup_llm.subprocess, "run",
            lambda *a, **k: mock.Mock(stdout="", stderr="ssh: Could not resolve hostname bogus.example\n"),
        )
        assert setup_llm._check_git_ssh_auth("bogus.example") is False

    def test_ssh_binary_missing_does_not_crash_and_defaults_true(self, monkeypatch):
        def raise_missing(*a, **k):
            raise FileNotFoundError("no such file: ssh")
        monkeypatch.setattr(setup_llm.subprocess, "run", raise_missing)
        assert setup_llm._check_git_ssh_auth("github.com") is True

    def test_timeout_does_not_crash_and_defaults_true(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=15)
        monkeypatch.setattr(setup_llm.subprocess, "run", raise_timeout)
        assert setup_llm._check_git_ssh_auth("github.com") is True

    def test_unrecognized_output_defaults_true(self, monkeypatch):
        monkeypatch.setattr(setup_llm.subprocess, "run", lambda *a, **k: mock.Mock(stdout="", stderr=""))
        assert setup_llm._check_git_ssh_auth("github.com") is True


class TestCloneStateRepo:
    def test_skips_clone_and_does_not_crash_when_ssh_auth_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(setup_llm, "_check_git_ssh_auth", lambda host: False)
        target = tmp_path / "ssh-broken-target"

        setup_llm._clone_state_repo("git@github.com:owner/repo.git", "main", target)

        result = capsys.readouterr()
        out = result.out + result.err
        assert "ssh -T git@github.com" in out
        assert not target.exists()

    def test_https_url_is_never_ssh_checked(self, tmp_path, monkeypatch):
        def fail_if_called(host):
            raise AssertionError("_check_git_ssh_auth should not be called for an HTTPS URL")
        monkeypatch.setattr(setup_llm, "_check_git_ssh_auth", fail_if_called)
        # Nonexistent local source, so the actual clone attempt fails fast -
        # only asserting the SSH check itself is skipped for this URL shape.
        setup_llm._clone_state_repo("https://example.com/owner/repo.git", "main", tmp_path / "target")


class TestSetupStateRepo:
    def test_clones_into_empty_directory(self, tmp_path, fake_git_remote, monkeypatch):
        target = tmp_path / "clone-target"
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), fake_git_remote, "main"])

        assert (target / ".git").is_dir()
        assert (target / "README.md").is_file()  # cloned content, proves it's a real clone
        assert (target / "specs").is_dir()
        assert lib_env.read_env_var(env_path, "STATE_REPO_PATH") == str(target)
        assert lib_env.read_env_var(env_path, "GITHUB_REPO_URL") == fake_git_remote
        assert lib_env.read_env_var(env_path, "GITHUB_REPO_BRANCH") == "main"

    def test_git_init_when_no_url_given(self, tmp_path, monkeypatch):
        target = tmp_path / "init-target"
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), "", "main"])

        assert (target / ".git").is_dir()
        assert (target / "specs").is_dir()
        assert lib_env.read_env_var(env_path, "STATE_REPO_PATH") == str(target)
        assert lib_env.read_env_var(env_path, "GITHUB_REPO_URL") == ""
        assert lib_env.read_env_var(env_path, "GITHUB_REPO_BRANCH") == "main"

    def test_leaves_existing_git_repo_alone_but_still_ensures_specs(self, tmp_path, fake_git_remote, monkeypatch, capsys):
        target = tmp_path / "already-git"
        target.mkdir()
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(target)], check=True)
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), fake_git_remote, "main"])

        assert "already a git repository" in capsys.readouterr().out
        assert (target / "specs").is_dir()

    def test_warns_on_remote_mismatch_but_does_not_change_it(self, tmp_path, fake_git_remote, monkeypatch, capsys):
        target = tmp_path / "mismatched"
        target.mkdir()
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "remote", "add", "origin", "https://example.com/other.git"], check=True)
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), fake_git_remote, "main"])

        result = capsys.readouterr()
        assert "does not match" in result.out + result.err
        remote = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert remote == "https://example.com/other.git"  # left unchanged

    def test_warns_on_dirty_nongit_directory_and_does_not_clone(self, tmp_path, fake_git_remote, monkeypatch, capsys):
        target = tmp_path / "dirty"
        target.mkdir()
        (target / "existing.txt").write_text("do not touch")
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), fake_git_remote, "main"])

        result = capsys.readouterr()
        out = result.out + result.err
        assert "already has files" in out
        assert not (target / ".git").is_dir()
        assert (target / "existing.txt").read_text() == "do not touch"

    def test_clone_failure_warns_instead_of_crashing(self, tmp_path, monkeypatch, capsys):
        # A local, nonexistent path fails fast with no network involved -
        # avoids any real network dependency/flakiness in this test.
        target = tmp_path / "bad-clone-target"
        bogus_source = tmp_path / "nonexistent-source.git"
        env_path = tmp_path / ".env"
        _run_setup_state_repo(monkeypatch, env_path, [str(target), str(bogus_source), "main"])

        result = capsys.readouterr()
        out = result.out + result.err
        assert "git clone failed" in out
        assert not (target / ".git").is_dir()

    def test_git_missing_skips_setup_but_still_writes_env(self, tmp_path, monkeypatch):
        target = tmp_path / "no-git-target"
        env_path = tmp_path / ".env"
        monkeypatch.setattr(setup_llm.shutil, "which", lambda cmd: None)
        _run_setup_state_repo(monkeypatch, env_path, [str(target), "", "main"])

        assert not target.exists()
        assert lib_env.read_env_var(env_path, "STATE_REPO_PATH") == str(target)
