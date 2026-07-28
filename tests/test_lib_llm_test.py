import os

import lib_llm_test
from conftest import REPO_ROOT


class TestLlmActiveConfigPath:
    """
    Acceptance Criteria (GH issue #36): setup_llm.py's Local/Ollama flow
    only ever writes config/model-templates/litellm.local-ollama.yaml,
    never the root litellm.yaml - so doctor.py/run.py must pick between the
    two by freshness (mtime), not just always look at litellm.yaml.
    """

    def _write(self, path, mtime_offset=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("model_list: []\n")
        if mtime_offset:
            stat = path.stat()
            os.utime(path, (stat.st_atime, stat.st_mtime + mtime_offset))

    def test_only_cloud_file_exists(self, tmp_path):
        self._write(tmp_path / "litellm.yaml")
        result = lib_llm_test.llm_active_config_path(tmp_path)
        assert result == tmp_path / "litellm.yaml"

    def test_only_local_file_exists(self, tmp_path):
        self._write(tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml")
        result = lib_llm_test.llm_active_config_path(tmp_path)
        assert result == tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"

    def test_neither_file_exists_defaults_to_cloud_path(self, tmp_path):
        result = lib_llm_test.llm_active_config_path(tmp_path)
        assert result == tmp_path / "litellm.yaml"

    def test_local_file_written_more_recently_wins(self, tmp_path):
        self._write(tmp_path / "litellm.yaml")
        self._write(tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml", mtime_offset=10)
        result = lib_llm_test.llm_active_config_path(tmp_path)
        assert result == tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"

    def test_stale_local_file_does_not_win_over_fresher_cloud_file(self, tmp_path):
        self._write(tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml")
        self._write(tmp_path / "litellm.yaml", mtime_offset=10)
        result = lib_llm_test.llm_active_config_path(tmp_path)
        assert result == tmp_path / "litellm.yaml"


class TestLlmActiveProvider:
    def test_missing_file_returns_unknown(self, tmp_path):
        assert lib_llm_test.llm_active_provider(tmp_path / "nope.yaml") == "unknown"

    def test_gemini_template(self):
        assert lib_llm_test.llm_active_provider(REPO_ROOT / "litellm.yaml") == "gemini"

    def test_anthropic_template(self):
        path = REPO_ROOT / "config/model-templates/litellm.cloud-anthropic.yaml"
        assert lib_llm_test.llm_active_provider(path) == "anthropic"

    def test_openai_template(self):
        path = REPO_ROOT / "config/model-templates/litellm.cloud-openai.yaml"
        assert lib_llm_test.llm_active_provider(path) == "openai"

    def test_local_ollama_template(self):
        path = REPO_ROOT / "config/model-templates/litellm.local-ollama.yaml"
        assert lib_llm_test.llm_active_provider(path) == "local"

    def test_file_with_no_recognizable_model_line_is_unknown(self, tmp_path):
        f = tmp_path / "litellm.yaml"
        f.write_text("model_list:\n  - model_name: foo\n")
        assert lib_llm_test.llm_active_provider(f) == "unknown"


class TestLlmProviderKeyVar:
    def test_gemini(self):
        assert lib_llm_test.llm_provider_key_var("gemini") == "GOOGLE_API_KEY"

    def test_anthropic(self):
        assert lib_llm_test.llm_provider_key_var("anthropic") == "ANTHROPIC_API_KEY"

    def test_openai(self):
        assert lib_llm_test.llm_provider_key_var("openai") == "OPENAI_API_KEY"

    def test_local_has_no_key_var(self):
        assert lib_llm_test.llm_provider_key_var("local") == ""

    def test_unknown_has_no_key_var(self):
        assert lib_llm_test.llm_provider_key_var("unknown") == ""


class TestLlmWaitForProxy:
    def test_reachable_returns_true_immediately(self, mock_proxy):
        base_url, _ = mock_proxy
        assert lib_llm_test.llm_wait_for_proxy(base_url, timeout_secs=5) is True

    def test_unreachable_times_out_and_returns_false(self):
        assert lib_llm_test.llm_wait_for_proxy("http://127.0.0.1:1", timeout_secs=1) is False


class TestLlmTestAlias:
    def test_success(self, mock_proxy):
        base_url, behavior = mock_proxy
        ok, detail = lib_llm_test.llm_test_alias(base_url, behavior["valid_key"], "scrum-po", timeout_secs=5)
        assert ok is True
        assert 'scrum-po' in detail
        assert "OK" in detail

    def test_auth_failure(self, mock_proxy):
        base_url, _ = mock_proxy
        ok, detail = lib_llm_test.llm_test_alias(base_url, "wrong-key", "scrum-po", timeout_secs=5)
        assert ok is False
        assert "401" in detail
        assert "auth failed" in detail

    def test_model_not_found(self, mock_proxy):
        base_url, behavior = mock_proxy
        behavior["missing_model"] = "scrum-eval-cheap"
        ok, detail = lib_llm_test.llm_test_alias(base_url, behavior["valid_key"], "scrum-eval-cheap", timeout_secs=5)
        assert ok is False
        assert "404" in detail
        assert "not found" in detail

    def test_unreachable(self):
        ok, detail = lib_llm_test.llm_test_alias("http://127.0.0.1:1", "any-key", "scrum-po", timeout_secs=2)
        assert ok is False
        assert "could not reach" in detail
