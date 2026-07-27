import json
import urllib.error
from unittest import mock

import pytest
import yaml

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
