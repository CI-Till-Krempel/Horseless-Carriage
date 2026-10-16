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


def _populate_valid_repo(repo_root):
    """Writes a valid .env + litellm.yaml + state repo under repo_root -
    everything doctor.py needs to pass end-to-end. Shared by valid_repo
    below and TestDashboardRegistryCheck's own nested-repo fixture, which
    needs this same content at a path other than tmp_path itself."""
    state_repo = repo_root / "state_repo"
    (state_repo / "specs").mkdir(parents=True)

    (repo_root / ".env").write_text(
        'LITELLM_MASTER_KEY="testkey"\n'
        f'STATE_REPO_PATH="{state_repo}"\n'
        'GIT_USER_NAME="Test"\n'
        'GIT_USER_EMAIL="test@example.com"\n'
        'LOG_LEVEL="info"\n'
        'GITHUB_REPO_URL="git@github.com:example/example.git"\n'
        'GITHUB_TOKEN="dummy"\n'
        'GOOGLE_API_KEY="real-key-value"\n'
    )
    (repo_root / "litellm.yaml").write_text(
        "model_list:\n"
        "  - model_name: scrum-po\n"
        "    litellm_params:\n"
        "      model: gemini/gemini-2.5-pro\n"
        "      api_key: os.environ/GOOGLE_API_KEY\n"
    )


@pytest.fixture
def valid_repo(tmp_path, monkeypatch):
    """A tmp_path with a valid .env + litellm.yaml + state repo - everything
    doctor.py needs to pass end-to-end. docker/docker-compose/gh are all
    "present and happy" by default; individual tests override as needed."""
    _populate_valid_repo(tmp_path)

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

    def test_docker_daemon_not_running_errors(self, valid_repo, monkeypatch, capsys):
        """
        Acceptance Criteria (GH issue #107): the docker CLI binary being
        present doesn't mean the daemon is actually running (e.g. Docker
        Desktop installed but not started) - `docker compose version` alone
        can't catch this, since it only needs the CLI, not a live daemon.
        """
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})

        def fake_run(cmd, **kwargs):
            if cmd == ["docker", "info"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)
        _patch_proxy_unreachable(monkeypatch)

        code = doctor.run(valid_repo)
        assert code == 1
        assert "Docker daemon isn't running" in capsys.readouterr().out

    def test_docker_daemon_running_reports_no_error(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        _patch_subprocess_ok(monkeypatch)
        _patch_proxy_unreachable(monkeypatch)

        code = doctor.run(valid_repo)
        assert "Docker daemon isn't running" not in capsys.readouterr().out

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

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "auth"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "gh CLI is not authenticated" in capsys.readouterr().out
        assert code == 0

    def test_gh_auth_check_skipped_when_github_app_fully_configured(self, valid_repo, monkeypatch, capsys):
        """GH issue #243: PREFLIGHT.md says `gh auth status` only matters
        for the Personal Account auth method and should be skipped for a
        GitHub App setup - doctor.py shouldn't warn about it (even if gh
        itself is missing or unauthenticated) once the GitHub App trio is
        fully configured."""
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "auth"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)
        _patch_proxy_unreachable(monkeypatch)

        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_APP_ID="1"\nGITHUB_APP_PRIVATE_KEY="key"\nGITHUB_APP_INSTALLATION_ID="2"\n'
        env.write_text(text)

        code = doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "gh CLI is not authenticated" not in out
        assert "'gh' command not found" not in out
        assert code == 0


class TestComposeProjectNamePrefixMigration:
    """ISSUE-0063 follow-up: new_project.py now keeps 'horseless-carriage_'
    on the front of an auto-derived COMPOSE_PROJECT_NAME_PREFIX, but an
    installation from before that change has one that's just the bare
    target-repo name - doctor.py should flag it (with migration steps),
    never silently rewrite .env itself."""

    def test_old_style_prefix_warns_with_migration_steps(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'COMPOSE_PROJECT_NAME_PREFIX="heinzelmann"\n')

        code = doctor.run(valid_repo)
        out = capsys.readouterr().out

        assert "COMPOSE_PROJECT_NAME_PREFIX='heinzelmann' predates" in out
        assert "horseless-carriage_heinzelmann" in out
        assert "docker compose -p heinzelmann-dev down" in out
        assert "docker compose -p horseless-carriage_heinzelmann-dev up -d" in out
        assert code == 0

    def test_already_migrated_prefix_does_not_warn(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'COMPOSE_PROJECT_NAME_PREFIX="horseless-carriage_heinzelmann"\n')

        code = doctor.run(valid_repo)
        out = capsys.readouterr().out

        assert "predates" not in out
        assert code == 0

    def test_bare_default_prefix_does_not_warn(self, valid_repo, monkeypatch, capsys):
        """A standalone checkout with no target project may have this
        explicitly set to the bare default too - not itself a problem."""
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'COMPOSE_PROJECT_NAME_PREFIX="horseless-carriage"\n')

        code = doctor.run(valid_repo)
        out = capsys.readouterr().out

        assert "predates" not in out
        assert code == 0

    def test_unset_prefix_does_not_warn(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        out = capsys.readouterr().out

        assert "predates" not in out
        assert code == 0


class TestDashboardRegistryCheck:
    """GH issue #310: a nested installation (repo_root is a
    horseless-carriage/ submodule of some other project) should be
    registered in project_registry.py so dashboard.py can list it -
    whether new_project.py did that or it was set up by hand. doctor.py
    must never read/write the real ~/.horseless-carriage/projects.json -
    every test here patches REGISTRY_PATH to a tmp_path location first."""

    @pytest.fixture
    def nested_repo(self, tmp_path, monkeypatch):
        """repo_root nested one level under a parent that's itself a git
        repo (the shape new_project.py's own _install_if_nested detects) -
        same valid .env/litellm.yaml content as the top-level valid_repo
        fixture, just at this different path."""
        target_repo = tmp_path / "target"
        (target_repo / ".git").mkdir(parents=True)
        repo_root = target_repo / "horseless-carriage"
        repo_root.mkdir()
        _populate_valid_repo(repo_root)

        _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
        _patch_subprocess_ok(monkeypatch)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: (None, ""))
        monkeypatch.setattr(doctor.project_registry, "REGISTRY_PATH", tmp_path / "registry.json")
        return repo_root

    def test_nested_and_unregistered_warns(self, nested_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(nested_repo)
        out = capsys.readouterr().out

        assert "isn't registered in the dashboard project registry" in out
        assert "python3 new_project.py" in out
        assert code == 0

    def test_nested_and_already_registered_does_not_warn(self, nested_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        doctor.project_registry.register("target", nested_repo, nested_repo.parent)

        code = doctor.run(nested_repo)
        out = capsys.readouterr().out

        assert "isn't registered in the dashboard project registry" not in out
        assert code == 0

    def test_standalone_checkout_does_not_warn(self, valid_repo, monkeypatch, capsys):
        """valid_repo's tmp_path has no parent .git - not a nested
        installation at all, so this check must stay silent regardless of
        the (patched-to-real-home-by-default, but never actually touched
        here since the guard short-circuits first) registry state."""
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        out = capsys.readouterr().out

        assert "isn't registered in the dashboard project registry" not in out
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
        assert "docker compose -p horseless-carriage-dev up -d db litellm" in out

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
        assert "docker-compose.local.yaml -p horseless-carriage-dev up -d db litellm ollama" in out

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
        assert "docker-compose.local.yaml -p horseless-carriage-dev up -d db litellm ollama" in out

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

    def test_warns_when_a_configured_alias_resolves_to_zero_cost(self, valid_repo, mock_proxy, capsys):
        """
        Acceptance Criteria (GH issue #298): a chat completion succeeding
        (LLM connectivity: OK) says nothing about whether LiteLLM's bundled
        pricing table actually has a price for that model id - doctor.py
        must check that separately and warn if it doesn't.
        """
        base_url, behavior = mock_proxy
        behavior["model_info"] = [
            {
                "model_name": "scrum-po",
                "litellm_params": {"model": "gemini/gemini-1.5-pro"},
                "model_info": {"input_cost_per_token": 0, "output_cost_per_token": 0},
            }
        ]
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('LITELLM_MASTER_KEY="testkey"\n', f'LITELLM_MASTER_KEY="{behavior["valid_key"]}"\n'))
        code = doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert "resolves to $0 cost per token" in out
        assert "gemini/gemini-1.5-pro" in out
        assert code == 0  # a pricing gap is a warning, not a hard failure

    def test_no_pricing_warning_when_every_alias_is_priced(self, valid_repo, mock_proxy, capsys):
        base_url, behavior = mock_proxy
        behavior["model_info"] = [
            {
                "model_name": "scrum-po",
                "litellm_params": {"model": "gemini/gemini-flash-lite-latest"},
                "model_info": {"input_cost_per_token": 0.0000001, "output_cost_per_token": 0.0000004},
            }
        ]
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('LITELLM_MASTER_KEY="testkey"\n', f'LITELLM_MASTER_KEY="{behavior["valid_key"]}"\n'))
        doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert "resolves to $0 cost per token" not in out


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

    def test_missing_identity_files_are_migrated_with_a_note(self, valid_repo, monkeypatch, capsys):
        """Acceptance Criteria: a state repo that predates the per-role
        identity.md convention gets each role's default identity content
        materialized automatically on a plain doctor.py run, not just via
        the standalone check_state_repo.py script."""
        from agents.scrum_team.prompts import ROLE_NAMES

        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        doctor.check(valid_repo)
        for role in ROLE_NAMES:
            assert (state_repo / f"{role}-identity.md").is_file()
        assert "Created default identity.md in the state repository for 6 role(s)" in capsys.readouterr().out

    def test_existing_identity_file_survives_a_doctor_run(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        custom = "# ProductOwner\n\nMy team's own notes.\n"
        (state_repo / "ProductOwner-identity.md").write_text(custom, encoding="utf-8")

        doctor.check(valid_repo)

        assert (state_repo / "ProductOwner-identity.md").read_text(encoding="utf-8") == custom
        note_line = next(line for line in capsys.readouterr().out.splitlines() if "Created default identity.md" in line)
        assert "5 role(s)" in note_line
        assert "ProductOwner" not in note_line

    def test_already_migrated_repo_produces_no_note(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        doctor.check_state_repo.ensure_role_identity_defaults(state_repo)
        capsys.readouterr()  # discard output from the setup call above

        doctor.check(valid_repo)

        out = capsys.readouterr().out
        assert "Created default identity.md" not in out


class TestRunningStateRepoMountMismatch:
    """A real incident: a standalone checkout's STATE_REPO_PATH changed
    while a different project's agent was still running under the same
    (default) Compose project name - the next `docker compose up` would
    have silently stopped and recreated it. doctor.py must catch this
    before that happens, as a hard ERROR (not a warning) since the
    consequence is destructive."""

    def test_no_container_running_produces_no_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_docker, "running_state_repo_mount", lambda *a, **k: None)
        result = doctor.check(valid_repo)
        assert not any("ALREADY RUNNING" in e.message for e in result.errors())

    def test_running_container_with_matching_mount_produces_no_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        monkeypatch.setattr(doctor.lib_docker, "running_state_repo_mount", lambda *a, **k: str(state_repo))
        result = doctor.check(valid_repo)
        assert not any("ALREADY RUNNING" in e.message for e in result.errors())

    def test_running_container_with_different_mount_is_an_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        other_project = valid_repo / "some-other-project"
        other_project.mkdir()
        monkeypatch.setattr(doctor.lib_docker, "running_state_repo_mount", lambda *a, **k: str(other_project))
        result = doctor.check(valid_repo)
        assert any("ALREADY RUNNING" in e.message and str(other_project) in e.message for e in result.errors())
        assert result.has_errors

    def test_host_mnt_prefixed_mount_is_normalized_before_comparing(self, valid_repo, monkeypatch):
        """Docker Desktop on macOS can report a bind mount's source with a
        /host_mnt prefix - must not be treated as a mismatch against the
        same, unprefixed host path."""
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        monkeypatch.setattr(doctor.lib_docker, "running_state_repo_mount", lambda *a, **k: f"/host_mnt{state_repo}")
        result = doctor.check(valid_repo)
        assert not any("ALREADY RUNNING" in e.message for e in result.errors())


class TestUnsupportedEcosystemAndJvmChecks:
    """ISSUE-0064: the Tested gate's test-runner registry only supports
    Python/Gradle/Maven - a target repo for anything else (Go/Rust/Ruby/PHP/
    .NET), or a Gradle/Maven repo with no JDK available to the agent image,
    should be flagged here at doctor/startup time, not discovered mid-sprint
    as a confusing Tested-gate failure."""

    def test_no_warning_for_a_plain_python_repo(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(valid_repo)
        assert not any("JVM" in w.message or "test-runner adapter" in w.message for w in result.warnings())

    def test_go_mod_warns_with_an_issue_link(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        (state_repo / "go.mod").write_text("module example.com/foo\n")

        result = doctor.check(valid_repo)

        matches = [w.message for w in result.warnings() if "Go project" in w.message]
        assert len(matches) == 1
        assert "github.com/CI-Till-Krempel/Horseless-Carriage/issues/new" in matches[0]

    def test_package_json_alone_does_not_warn(self, valid_repo, monkeypatch):
        """Deliberately not treated as an unsupported ecosystem - too common
        alongside an unrelated primary Python test suite (frontend tooling,
        lint configs) to flag on its mere presence."""
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        (state_repo / "package.json").write_text("{}")

        result = doctor.check(valid_repo)

        assert not any("test-runner adapter" in w.message for w in result.warnings())

    def test_gradle_project_with_placeholder_workbench_warns(self, valid_repo, monkeypatch):
        """ISSUE-0064: a Gradle/Maven manifest with no workbench customized
        yet (still the auto-created placeholder, or missing entirely) means
        the agent image has no JDK - nudge toward propose_framework_change
        rather than leaving a silent gap."""
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        (state_repo / "build.gradle.kts").write_text("")

        result = doctor.check(valid_repo)

        assert any("Gradle/Maven (JVM) project" in w.message for w in result.warnings())
        assert any("propose_framework_change" in w.message for w in result.warnings())

    def test_gradle_project_with_customized_workbench_does_not_warn(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        (state_repo / "build.gradle.kts").write_text("")
        (state_repo / ".hc").mkdir(parents=True, exist_ok=True)
        (state_repo / ".hc" / "workbench-setup.sh").write_text(
            "#!/bin/sh\napt-get install -y default-jdk\n", encoding="utf-8"
        )

        result = doctor.check(valid_repo)

        assert not any("Gradle/Maven (JVM) project" in w.message for w in result.warnings())

    def test_doctor_run_creates_the_default_workbench_script(self, valid_repo, monkeypatch):
        """Purely additive migration (ensure_workbench_script_default),
        same shape as the identity.md one right below it in doctor.py."""
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"

        doctor.check(valid_repo)

        assert (state_repo / ".hc" / "workbench-setup.sh").is_file()

    def test_pom_xml_nested_in_a_subdirectory_is_detected(self, valid_repo, monkeypatch):
        """The manifest search is recursive (**/pom.xml) - a Maven module
        several directories deep must still be found, not just one sitting
        directly at STATE_REPO_PATH's root."""
        _patch_proxy_unreachable(monkeypatch)
        state_repo = valid_repo / "state_repo"
        nested = state_repo / "backend" / "service"
        nested.mkdir(parents=True)
        (nested / "pom.xml").write_text("")

        result = doctor.check(valid_repo)

        assert any("Gradle/Maven (JVM) project" in w.message for w in result.warnings())


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
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", lambda o, r, b, t: (True, f"branch '{b}' exists"))
        result = doctor.check(valid_repo)
        assert result.warnings() == []
        assert result.errors() == []
        assert "GitHub access: read access to example/example confirmed" in capsys.readouterr().out

    def test_access_problem_warns(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: ("tok", "token"))
        monkeypatch.setattr(doctor.lib_github, "check_repo_access", lambda o, r, t: (False, "issues read failed (HTTP 403)"))
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", lambda o, r, b, t: (True, f"branch '{b}' exists"))
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


class TestGithubBranchExistenceCheck:
    """A real incident: a freshly `git init`-ed product repo had only its
    initial branch pushed, no `develop` ever created - this surfaced only
    mid-sprint as a confusing "no develop branch" failure in the agent's own
    GitFlow tooling, instead of here at setup time. Checked whenever repo
    access itself was checked (regardless of whether access_ok came back
    True or False - a read-only token can still answer "does this branch
    exist")."""

    def _with_token(self, valid_repo, monkeypatch, access_ok=True):
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: ("tok", "token"))
        monkeypatch.setattr(doctor.lib_github, "check_repo_access", lambda o, r, t: (access_ok, "access checked"))
        return valid_repo

    def test_both_branches_present_is_clean(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        self._with_token(valid_repo, monkeypatch)
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", lambda o, r, b, t: (True, f"branch '{b}' exists on {o}/{r}"))

        result = doctor.check(valid_repo)

        assert result.errors() == []
        out = capsys.readouterr().out
        assert "branch 'main' exists" in out
        assert "branch 'develop' exists" in out

    def test_missing_develop_branch_is_an_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        self._with_token(valid_repo, monkeypatch)

        def fake_check(owner, repo, branch, token):
            if branch == "develop":
                return False, f"branch '{branch}' does not exist on {owner}/{repo}"
            return True, f"branch '{branch}' exists on {owner}/{repo}"
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", fake_check)

        result = doctor.check(valid_repo)

        assert any("develop" in e.message and "does not exist" in e.message for e in result.errors())
        assert result.has_errors

    def test_missing_configured_default_branch_is_an_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'GITHUB_REPO_BRANCH="trunk"\n')
        self._with_token(valid_repo, monkeypatch)

        def fake_check(owner, repo, branch, token):
            if branch == "trunk":
                return False, f"branch '{branch}' does not exist on {owner}/{repo}"
            return True, f"branch '{branch}' exists on {owner}/{repo}"
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", fake_check)

        result = doctor.check(valid_repo)

        assert any("trunk" in e.message and "does not exist" in e.message for e in result.errors())

    def test_custom_develop_branch_name_is_respected(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'GITHUB_DEVELOP_BRANCH="integration"\n')
        self._with_token(valid_repo, monkeypatch)
        checked_branches = []

        def fake_check(owner, repo, branch, token):
            checked_branches.append(branch)
            return True, f"branch '{branch}' exists on {owner}/{repo}"
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", fake_check)

        doctor.check(valid_repo)

        assert "integration" in checked_branches
        assert "develop" not in checked_branches

    def test_checked_even_when_repo_access_itself_failed(self, valid_repo, monkeypatch):
        """access_ok=False (e.g. a read-only token) must not skip the branch
        check - reading branch existence needs less than reading issues/PRs."""
        _patch_proxy_unreachable(monkeypatch)
        self._with_token(valid_repo, monkeypatch, access_ok=False)
        calls = []
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", lambda o, r, b, t: calls.append(b) or (True, "ok"))

        doctor.check(valid_repo)

        assert set(calls) == {"main", "develop"}

    def test_app_token_mint_failure_skips_branch_check_too(self, valid_repo, monkeypatch):
        """No token at all means no way to call the GitHub API - same guard
        clause check_repo_access itself already relies on."""
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_APP_ID="1"\nGITHUB_APP_PRIVATE_KEY="key"\nGITHUB_APP_INSTALLATION_ID="2"\n'
        env.write_text(text)
        monkeypatch.setattr(doctor.lib_github, "resolve_token", lambda env: (None, "app"))

        def fail_if_called(*a, **k):
            raise AssertionError("check_branch_exists should not run without a token")
        monkeypatch.setattr(doctor.lib_github, "check_branch_exists", fail_if_called)

        doctor.check(valid_repo)


class TestDefaultAndDevelopBranchNameCollision:
    """Pure .env sanity check, no network/token needed - GITHUB_DEVELOP_
    BRANCH resolving to the same name as GITHUB_REPO_BRANCH would make every
    GitFlow release PR (develop -> main) have identical base and head,
    which GitHub flatly refuses to open."""

    def test_defaults_do_not_collide(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        result = doctor.check(valid_repo)
        assert not any("identical base and head" in e.message for e in result.errors())

    def test_explicit_same_name_is_an_error(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'GITHUB_DEVELOP_BRANCH="main"\n')

        result = doctor.check(valid_repo)

        assert any("GITHUB_REPO_BRANCH and GITHUB_DEVELOP_BRANCH are both 'main'" in e.message for e in result.errors())

    def test_custom_but_distinct_names_do_not_collide(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text() + 'GITHUB_REPO_BRANCH="trunk"\nGITHUB_DEVELOP_BRANCH="integration"\n')

        result = doctor.check(valid_repo)

        assert not any("identical base and head" in e.message for e in result.errors())

    def test_checked_even_without_a_token_or_network(self, valid_repo, monkeypatch):
        """No token configured at all - the existence check can't run, but
        this one needs no network and must still catch the misconfiguration."""
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_DEVELOP_BRANCH="main"\n'
        env.write_text(text)

        result = doctor.check(valid_repo)

        assert any("GITHUB_REPO_BRANCH and GITHUB_DEVELOP_BRANCH are both 'main'" in e.message for e in result.errors())


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


class TestPrintOllamaGpuConfirmation:
    """Acceptance Criteria (GH issue #235): a caller like run.py that just
    brought the local/Ollama stack up (rather than running the whole
    check() punch list) can call this standalone helper to get the same
    one-line GPU/host-Ollama confirmation, proactively, right after
    startup - not only when a user happens to separately run
    `python3 doctor.py`."""

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

    def test_silent_for_cloud_provider(self, valid_repo, capsys):
        """valid_repo defaults to a cloud (Gemini) provider - no local/GPU
        Ollama setup at all, so this must print nothing."""
        doctor.print_ollama_gpu_confirmation(valid_repo)
        assert capsys.readouterr().out == ""

    def test_confirms_gpu_when_working(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: ["ollama"])
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", lambda compose_args: "cuda")

        doctor.print_ollama_gpu_confirmation(repo)

        assert "GPU acceleration active" in capsys.readouterr().out

    def test_warns_when_fallen_back_to_cpu(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: ["ollama"])
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", lambda compose_args: "cpu")

        doctor.print_ollama_gpu_confirmation(repo)

        out = capsys.readouterr().out
        assert "running on CPU" in out
        assert "docker compose" in out and "logs ollama" in out

    def test_silent_when_status_not_yet_determinable(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: ["ollama"])
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", lambda compose_args: None)

        doctor.print_ollama_gpu_confirmation(repo)

        assert capsys.readouterr().out == ""

    def test_silent_when_ollama_container_not_running(self, valid_repo, monkeypatch, capsys):
        repo = self._local_gpu_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", lambda compose_args: [])

        def fail_if_called(compose_args):
            raise AssertionError("ollama_gpu_status should not run when the ollama container isn't up yet")
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", fail_if_called)

        doctor.print_ollama_gpu_confirmation(repo)

        assert capsys.readouterr().out == ""

    def test_confirms_host_ollama_when_reachable(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: True)

        doctor.print_ollama_gpu_confirmation(repo)

        assert "GPU acceleration active" in capsys.readouterr().out

    def test_warns_when_host_ollama_unreachable(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: False)

        doctor.print_ollama_gpu_confirmation(repo)

        assert "not reachable at http://localhost:11434" in capsys.readouterr().out

    def test_host_mode_never_runs_container_gpu_check(self, valid_repo, monkeypatch, capsys):
        repo = self._local_host_mode_repo(valid_repo)
        env = repo / ".env"
        env.write_text(env.read_text() + 'OLLAMA_GPU_ENABLED="true"\n')
        monkeypatch.setattr(doctor.lib_docker, "host_ollama_reachable", lambda: True)

        def fail_if_called(*a, **k):
            raise AssertionError("the dockerized-ollama GPU check should not run in host mode")
        monkeypatch.setattr(doctor.lib_docker, "compose_running_services", fail_if_called)
        monkeypatch.setattr(doctor.lib_docker, "ollama_gpu_status", fail_if_called)

        doctor.print_ollama_gpu_confirmation(repo)
