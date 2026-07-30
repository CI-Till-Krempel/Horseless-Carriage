import subprocess

import pytest

import doctor


def _patch_which(monkeypatch, **available):
    """available maps command name -> bool (True = found)."""
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if available.get(cmd) else None
    monkeypatch.setattr(doctor.shutil, "which", fake_which)


def _patch_subprocess_ok(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)


def _patch_subprocess_fail(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)


def _patch_proxy_unreachable(monkeypatch):
    """llm_wait_for_proxy's real implementation retries for wall-clock
    seconds regardless of how fast the connection fails - not worth paying
    for in tests that don't care about proxy reachability at all."""
    monkeypatch.setattr(doctor.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)


@pytest.fixture
def valid_repo(tmp_path, monkeypatch):
    """A tmp_path with a valid .env + litellm.yaml + state repo - everything
    doctor.py needs to pass end-to-end. docker/docker-compose/gh are all
    "present and happy" by default; individual tests override as needed."""
    state_repo = tmp_path / "state_repo"
    (state_repo / "specs").mkdir(parents=True)

    (tmp_path / ".env").write_text(
        'LITELLM_MASTER_KEY="testkey"\n'
        f'STATE_REPO_PATH="{state_repo}"\n'
        'GIT_USER_NAME="Test"\n'
        'GIT_USER_EMAIL="test@example.com"\n'
        'LOG_LEVEL="info"\n'
        'GITHUB_REPO_URL="git@github.com:example/example.git"\n'
        'GITHUB_TOKEN="dummy"\n'
        'GOOGLE_API_KEY="real-key-value"\n'
    )
    (tmp_path / "litellm.yaml").write_text(
        "model_list:\n"
        "  - model_name: scrum-po\n"
        "    litellm_params:\n"
        "      model: gemini/gemini-2.5-pro\n"
        "      api_key: os.environ/GOOGLE_API_KEY\n"
    )

    _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
    _patch_subprocess_ok(monkeypatch)
    # No test in this file cares about the live GitHub access check by
    # default (it needs real network) - individual tests in
    # TestGithubAccessCheck override this again to exercise it directly.
    monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: (None, ""))
    return tmp_path


class TestGuardClauses:
    """
    Acceptance Criteria (ISSUE-0021): none of these are early returns
    anymore - check() keeps going and collects every ActionableItem, so
    each of these tests must mock the proxy-reachability wait (a later
    check that now always runs) the same way TestWarningsDoNotBlock/
    TestLlmConfigurationSection already do, to avoid a real multi-second
    network wait in the test suite.
    """

    def test_missing_env_file_errors(self, tmp_path, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(tmp_path)
        assert code == 1
        assert ".env file not found" in capsys.readouterr().out

    def test_missing_litellm_master_key_errors(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        (valid_repo / ".env").write_text(f'STATE_REPO_PATH="{valid_repo / "state_repo"}"\n')
        code = doctor.run(valid_repo)
        assert code == 1
        assert "LITELLM_MASTER_KEY is not set" in capsys.readouterr().out

    def test_missing_state_repo_path_errors(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        (valid_repo / ".env").write_text('LITELLM_MASTER_KEY="testkey"\n')
        code = doctor.run(valid_repo)
        assert code == 1
        assert "STATE_REPO_PATH is not set" in capsys.readouterr().out

    def test_state_repo_directory_missing_errors(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        (valid_repo / ".env").write_text(
            'LITELLM_MASTER_KEY="testkey"\nSTATE_REPO_PATH="/definitely/missing/xyz"\n'
        )
        code = doctor.run(valid_repo)
        assert code == 1
        assert "does not exist" in capsys.readouterr().out

    def test_docker_missing_errors(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch)  # nothing available
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert code == 1
        assert "'docker' command not found" in capsys.readouterr().out

    def test_docker_compose_missing_errors(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True)
        _patch_subprocess_fail(monkeypatch)
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert code == 1
        assert "'docker-compose' or 'docker compose' command not found" in capsys.readouterr().out

    def test_multiple_errors_are_all_collected_not_just_the_first(self, tmp_path, monkeypatch, capsys):
        """The whole point of the punch-list refactor: a completely
        unconfigured repo should surface every blocking problem in one
        pass, not just the first one encountered."""
        _patch_which(monkeypatch)  # nothing available: docker, docker-compose, gh all missing
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(tmp_path)
        out = capsys.readouterr().out
        assert code == 1
        assert "'docker' command not found" in out
        assert "'docker-compose' or 'docker compose' command not found" in out
        assert ".env file not found" in out
        assert "LITELLM_MASTER_KEY is not set" in out
        assert "STATE_REPO_PATH is not set" in out


class TestCheckStructuredResult:
    """
    Acceptance Criteria (ISSUE-0021): check() returns a DoctorResult -
    the actual punch list, not just an exit code - so other scripts
    (run.py's gatekeeper, setup.py's guided flow) can inspect exactly
    what's wrong instead of parsing printed text.
    """

    def test_clean_setup_has_no_items(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(valid_repo)
        assert result.ok is True
        assert result.has_errors is False
        assert result.items == []

    def test_errors_and_warnings_are_both_collected_and_classified(self, tmp_path, monkeypatch):
        _patch_which(monkeypatch)  # nothing available
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(tmp_path)
        assert result.has_errors is True
        assert result.ok is False
        assert any("docker" in i.message and i.severity == "error" for i in result.errors())
        # gh missing is a warning, not an error - must not count toward has_errors.
        assert any("'gh' command not found" in i.message for i in result.warnings())
        assert all(i.severity == "warning" for i in result.warnings())

    def test_warnings_only_setup_has_no_errors(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GIT_USER_NAME="Test"\n', ""))
        result = doctor.check(valid_repo)
        assert result.has_errors is False
        assert result.ok is False
        assert len(result.warnings()) >= 1

    def test_run_returns_1_iff_result_has_errors(self, tmp_path, valid_repo, monkeypatch):
        """run() is a thin exit-code wrapper around check() - verify the
        two stay in lockstep rather than testing run()'s int in isolation
        everywhere."""
        _patch_proxy_unreachable(monkeypatch)
        _patch_which(monkeypatch)  # nothing available - guarantees an error
        assert doctor.check(tmp_path).has_errors is True
        assert doctor.run(tmp_path) == 1

        _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
        assert doctor.check(valid_repo).has_errors is False
        assert doctor.run(valid_repo) == 0

    def test_print_summary_lists_every_item(self, tmp_path, monkeypatch, capsys):
        _patch_which(monkeypatch)  # nothing available
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(tmp_path)
        capsys.readouterr()  # discard check()'s own output
        result.print_summary()
        out = capsys.readouterr().out
        assert "[ERROR]" in out
        assert ".env file not found" in out

    def test_print_summary_on_clean_result_says_so(self, capsys):
        doctor.DoctorResult(items=[]).print_summary()
        assert "No actionable items" in capsys.readouterr().out


class TestWarningsDoNotBlock:
    def test_missing_git_identity_warns_but_succeeds(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GIT_USER_NAME="Test"\n', "").replace('GIT_USER_EMAIL="test@example.com"\n', "")
        env.write_text(text)
        code = doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "GIT_USER_NAME is not set" in out
        assert "GIT_USER_EMAIL is not set" in out
        assert code == 0

    def test_github_token_reports_personal_access_token(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "Using Personal Access Token" in capsys.readouterr().out
        assert code == 0

    def test_github_app_trio_reports_github_app(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_APP_ID="1"\nGITHUB_APP_PRIVATE_KEY="key"\nGITHUB_APP_INSTALLATION_ID="2"\n'
        env.write_text(text)
        code = doctor.run(valid_repo)
        assert "Using GitHub App" in capsys.readouterr().out
        assert code == 0

    def test_no_github_auth_warns(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GITHUB_TOKEN="dummy"\n', ""))
        code = doctor.run(valid_repo)
        assert "No GitHub authentication method fully configured" in capsys.readouterr().out
        assert code == 0

    def test_sessions_directory_created_if_missing(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        assert not (valid_repo / "sessions").exists()
        doctor.run(valid_repo)
        assert (valid_repo / "sessions").is_dir()

    def test_gh_missing_warns(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "'gh' command not found" in capsys.readouterr().out
        assert code == 0

    def test_gh_not_authenticated_warns(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
        _patch_subprocess_fail(monkeypatch)
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "gh CLI is not authenticated" in capsys.readouterr().out
        assert code == 0


class TestLlmConfigurationSection:
    def test_active_provider_detected_and_placeholder_key_warns(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GOOGLE_API_KEY="real-key-value"\n', 'GOOGLE_API_KEY="<your_google_key>"\n'))
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "Active provider (litellm.yaml): gemini" in out
        assert "GOOGLE_API_KEY is not set (or still a placeholder)" in out

    def test_proxy_unreachable_cloud_hint(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "not reachable" in out
        assert "docker compose up -d db litellm" in out

    def test_skip_llm_probe_never_calls_wait_for_proxy(self, valid_repo, monkeypatch, capsys):
        """run.py's pre-flight gate passes skip_llm_probe=True since
        nothing's started yet - the live network check would only ever
        report "not reachable" and cost several real seconds for nothing."""
        def fail_if_called(*a, **k):
            raise AssertionError("llm_wait_for_proxy should not be called when skip_llm_probe=True")
        monkeypatch.setattr(doctor.lib_llm_test, "llm_wait_for_proxy", fail_if_called)
        result = doctor.check(valid_repo, skip_llm_probe=True)
        assert result.has_errors is False
        assert "Skipping live proxy reachability check" in capsys.readouterr().out

    def test_proxy_unreachable_local_hint(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        (valid_repo / "litellm.yaml").write_text(
            "model_list:\n"
            "  - model_name: scrum-po\n"
            "    litellm_params:\n"
            "      model: ollama/llama3.1:8b\n"
            "      api_base: http://ollama:11434\n"
        )
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "docker-compose.local.yaml up -d db litellm ollama" in out

    def test_local_ollama_setup_detected_via_model_templates_file(self, valid_repo, monkeypatch, capsys):
        """
        Regression test (GH issue #36): setup_llm.py's Local/Ollama flow
        writes config/model-templates/litellm.local-ollama.yaml, NEVER the
        root litellm.yaml (which stays whatever cloud provider was set up
        before, or the repo's shipped default - "gemini" here, from
        valid_repo's fixture). Before this fix, doctor.py always looked at
        the stale root file and reported "gemini" + warned about a missing
        GOOGLE_API_KEY, even though a real local/Ollama setup was active
        and GOOGLE_API_KEY was never needed at all.
        """
        _patch_proxy_unreachable(monkeypatch)
        local_yaml = valid_repo / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True, exist_ok=True)
        local_yaml.write_text(
            "model_list:\n"
            "  - model_name: scrum-po\n"
            "    litellm_params:\n"
            "      model: ollama/llama3.1:8b\n"
            "      api_base: http://ollama:11434\n"
        )
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "Active provider (config/model-templates/litellm.local-ollama.yaml): local" in out
        assert "GOOGLE_API_KEY is not set" not in out
        assert "docker-compose.local.yaml up -d db litellm ollama" in out

    def test_proxy_reachable_and_test_succeeds(self, valid_repo, mock_proxy, capsys):
        base_url, behavior = mock_proxy
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('LITELLM_MASTER_KEY="testkey"\n', f'LITELLM_MASTER_KEY="{behavior["valid_key"]}"\n'))
        code = doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert f"LiteLLM proxy: reachable at {base_url}" in out
        assert "LLM connectivity: OK" in out
        assert code == 0

    def test_proxy_reachable_but_auth_fails(self, valid_repo, mock_proxy, capsys):
        base_url, _behavior = mock_proxy
        # .env's LITELLM_MASTER_KEY ("testkey") does not match the mock's
        # default valid_key ("good-key") - simulates a real key mismatch.
        code = doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert "LLM connectivity test failed" in out
        assert "401" in out
        assert code == 0  # a failed live test is a warning, not a hard failure


class TestStateRepoStructureChecks:
    """Acceptance Criteria (GH issue #60): doctor.py now runs the cheap,
    filesystem-only part of check_state_repo.py's checks itself (specs/
    directory presence, stray TEMPLATE-*.md files) - previously nothing in
    the guided setup flow ever ran check_state_repo.py at all."""

    def test_valid_repo_has_specs_dir_and_no_warning(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(valid_repo)
        assert not any("specs" in w.message for w in result.warnings())

    def test_missing_specs_dir_warns(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        (state_repo / "specs").rmdir()
        result = doctor.check(valid_repo)
        assert any("no 'specs' directory yet" in w.message for w in result.warnings())
        assert "check_state_repo.py" in capsys.readouterr().out

    def test_stray_templates_warn(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        specs_dir = valid_repo / "state_repo" / "specs"
        (specs_dir / "TEMPLATE-ISSUE.md").write_text("blueprint")
        result = doctor.check(valid_repo)
        assert any("stray TEMPLATE-*.md" in w.message for w in result.warnings())
        assert "check_state_repo.py" in capsys.readouterr().out

    def test_no_state_repo_path_at_all_skips_specs_check(self, tmp_path, monkeypatch):
        """Guard clause: don't crash trying to inspect a specs/ dir under a
        STATE_REPO_PATH that was never even set - that's already its own
        ERROR item."""
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        _patch_proxy_unreachable(monkeypatch)
        (tmp_path / ".env").write_text('LITELLM_MASTER_KEY="testkey"\n')
        result = doctor.check(tmp_path)
        assert not any("specs" in w.message for w in result.warnings())


class TestGithubAccessCheck:
    """Acceptance Criteria (GH issue #60): "without the ability to read
    from, write to and read/write pull requests and issues, the setup is
    not complete" - doctor.py now actually verifies this live instead of
    only checking that a credential is present in .env."""

    def test_no_repo_url_skips_the_check(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GITHUB_REPO_URL="git@github.com:example/example.git"\n', ""))

        def fail_if_called(*a, **k):
            raise AssertionError("resolve_token should not run without a GITHUB_REPO_URL")
        monkeypatch.setattr(doctor.lib_github, "resolve_token", fail_if_called)

        doctor.check(valid_repo)

    def test_no_auth_configured_skips_the_check(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GITHUB_TOKEN="dummy"\n', ""))

        def fail_if_called(*a, **k):
            raise AssertionError("resolve_token should not run without any auth method configured")
        monkeypatch.setattr(doctor.lib_github, "resolve_token", fail_if_called)

        doctor.check(valid_repo)

    def test_skip_llm_probe_skips_the_check_too(self, valid_repo, monkeypatch):
        def fail_if_called(*a, **k):
            raise AssertionError("resolve_token should not run when skip_llm_probe=True")
        monkeypatch.setattr(doctor.lib_github, "resolve_token", fail_if_called)

        doctor.check(valid_repo, skip_llm_probe=True)

    def test_unparseable_repo_url_warns(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace(
            'GITHUB_REPO_URL="git@github.com:example/example.git"\n',
            'GITHUB_REPO_URL="not-a-github-url"\n',
        ))
        result = doctor.check(valid_repo)
        assert any("doesn't look like a github.com repo URL" in w.message for w in result.warnings())

    def test_full_access_confirmed_prints_and_does_not_warn(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: ("tok", "token"))
        monkeypatch.setattr(doctor.lib_github, "check_repo_access", lambda o, r, t: (True, "read access to example/example confirmed"))
        result = doctor.check(valid_repo)
        assert result.warnings() == []
        assert "GitHub access: read access to example/example confirmed" in capsys.readouterr().out

    def test_access_problem_warns(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: ("tok", "token"))
        monkeypatch.setattr(doctor.lib_github, "check_repo_access", lambda o, r, t: (False, "issues read failed (HTTP 403)"))
        result = doctor.check(valid_repo)
        assert any("issues read failed" in w.message for w in result.warnings())

    def test_app_token_mint_failure_warns(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_APP_ID="1"\nGITHUB_APP_PRIVATE_KEY="key"\nGITHUB_APP_INSTALLATION_ID="2"\n'
        env.write_text(text)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: (None, "app"))

        def fail_if_called(*a, **k):
            raise AssertionError("check_repo_access should not run without a token")
        monkeypatch.setattr(doctor.lib_github, "check_repo_access", fail_if_called)

        result = doctor.check(valid_repo)
        assert any("Could not mint a GitHub App installation token" in w.message for w in result.warnings())


class TestOllamaGpuWarning:
    """Acceptance Criteria (GH issue #49): a driver/WSL2 misconfiguration
    otherwise leaves Ollama silently running on CPU with no error from
    Docker - doctor.py must surface this loudly rather than the user only
    finding out from noticing slow responses."""

    def _local_gpu_repo(self, valid_repo):
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'OLLAMA_GPU_ENABLED="true"\n')
        (valid_repo / "config" / "model-templates").mkdir(parents=True, exist_ok=True)
        (valid_repo / "config" / "model-templates" / "litellm.local-ollama.yaml").write_text(
            "model_list:\n"
            "  - model_name: scrum-po\n"
            "    litellm_params:\n"
            "      model: ollama/llama3.1:8b\n"
            "      api_base: http://ollama:11434\n"
        )
        return valid_repo

    def test_warns_loudly_when_ollama_container_falls_back_to_cpu(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: ["ollama"])
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", lambda compose_args: "cpu")

        result = doctor.check(repo)
        out = capsys.readouterr().out

        assert "GPU" in out and "CPU" in out
        assert "!" * 10 in out  # a noticeable banner, not just another line among many
        assert any("running on CPU" in i.message for i in result.warnings())

    def test_confirms_gpu_when_working(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: ["ollama"])
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", lambda compose_args: "cuda")

        result = doctor.check(repo)
        out = capsys.readouterr().out

        assert "GPU acceleration confirmed" in out
        assert result.warnings() == []

    def test_no_check_when_gpu_not_enabled(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)

        def fail_if_called(compose_args):
            raise AssertionError("ollama_gpu_status should not run when OLLAMA_GPU_ENABLED isn't true")
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", fail_if_called)

        doctor.check(valid_repo)  # cloud provider, GPU flag absent entirely

    def test_no_check_when_ollama_container_not_running(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: [])

        def fail_if_called(compose_args):
            raise AssertionError("ollama_gpu_status should not run when the ollama container isn't up yet")
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", fail_if_called)

        doctor.check(repo)

    def test_skip_llm_probe_skips_gpu_check_too(self, valid_repo, monkeypatch, capsys):
        """run.py's pre-flight gate (skip_llm_probe=True) runs before any
        container is started - the GPU check would only ever find no
        running ollama container, exactly like the proxy-reachability
        check it's gated alongside."""
        repo = self._local_gpu_repo(valid_repo)

        def fail_if_called(compose_args):
            raise AssertionError("compose_running_services should not run when skip_llm_probe=True")
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", fail_if_called)

        doctor.check(repo, skip_llm_probe=True)


class TestHostOllamaModeCheck:
    """
    Acceptance Criteria (GH issue #93): host-Ollama mode has no `ollama`
    container for the GPU check above to inspect - Ollama runs natively on
    the host - so doctor.py must check reachability directly instead
    (lib_docker.host_ollama_reachable), and never run the container-based
    GPU check for this mode.
    """

    def _local_host_mode_repo(self, valid_repo):
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'OLLAMA_HOST_MODE="true"\n')
        (valid_repo / "config" / "model-templates").mkdir(parents=True, exist_ok=True)
        (valid_repo / "config" / "model-templates" / "litellm.local-ollama.yaml").write_text(
            "model_list:\n"
            "  - model_name: scrum-po\n"
            "    litellm_params:\n"
            "      model: ollama/llama3.1:8b\n"
            "      api_base: http://host.docker.internal:11434\n"
        )
        return valid_repo

    def test_warns_when_host_ollama_unreachable(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: False)

        result = doctor.check(repo)
        out = capsys.readouterr().out

        assert "not reachable at http://localhost:11434" in out
        assert any("not reachable" in w.message for w in result.warnings())

    def test_confirms_when_host_ollama_reachable(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: True)

        result = doctor.check(repo)
        out = capsys.readouterr().out

        assert "Host Ollama: reachable" in out
        assert not any("not reachable" in w.message for w in result.warnings())

    def test_gpu_container_check_not_run_in_host_mode(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        env = repo / ".env"
        env.write_text(env.read_text() + 'OLLAMA_GPU_ENABLED="true"\n')
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: True)

        def fail_if_called(*a, **k):
            raise AssertionError("the dockerized-ollama GPU check should not run in host mode")
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", fail_if_called)
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", fail_if_called)

        doctor.check(repo)

    def test_skip_llm_probe_skips_host_check_too(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)

        def fail_if_called(*a, **k):
            raise AssertionError("host_ollama_reachable should not run when skip_llm_probe=True")
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", fail_if_called)

        doctor.check(repo, skip_llm_probe=True)
