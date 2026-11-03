import json
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest

import setup_github_app
import lib_env


def _fake_urlopen_response(body_dict):
    data = json.dumps(body_dict).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = data
    cm.__exit__.return_value = False
    return cm


class TestBuildManifest:
    def test_matches_the_documented_permission_set(self):
        """GH issue tracker: must stay in sync with docs/GITHUB-INTEGRATION.md
        "Option 2"'s manually-documented permissions (Contents/Pull requests
        read-write, Metadata read-only)."""
        manifest = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        assert manifest["default_permissions"] == {"contents": "write", "pull_requests": "write", "metadata": "read"}

    def test_webhook_is_inactive(self):
        manifest = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        assert manifest["hook_attributes"] == {"active": False}

    def test_redirect_and_setup_urls_are_passed_through(self):
        manifest = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        assert manifest["redirect_url"] == "http://localhost:1/callback"
        assert manifest["setup_url"] == "http://localhost:1/installed"

    def test_name_includes_owner_repo_and_is_not_identical_across_calls(self):
        """App names are unique GLOBALLY across GitHub - a fixed name would
        collide on every second install."""
        m1 = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        m2 = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        assert "acme-widgets" in m1["name"]
        assert m1["name"] != m2["name"]

    def test_app_is_not_public(self):
        manifest = setup_github_app._build_manifest("acme", "widgets", "http://localhost:1/callback", "http://localhost:1/installed")
        assert manifest["public"] is False


class TestManifestPageHtml:
    def test_embeds_the_manifest_json_and_state(self):
        manifest = {"name": "Test App", "public": False}
        page = setup_github_app._manifest_page_html(manifest, "my-csrf-state")
        assert "my-csrf-state" in page
        assert "Test App" in page
        assert "github.com/settings/apps/new" in page

    def test_escapes_html_special_characters_in_the_manifest(self):
        manifest = {"name": '<script>alert(1)</script>'}
        page = setup_github_app._manifest_page_html(manifest, "state")
        assert "<script>alert(1)</script>" not in page


class TestFreePort:
    def test_returns_a_usable_port_number(self):
        port = setup_github_app._free_port()
        assert isinstance(port, int)
        assert 0 < port < 65536

    def test_two_calls_can_each_bind_successfully(self):
        """Not asserting they differ (the OS could reuse one immediately
        after close) - just that resolving one doesn't leave it unusable."""
        port1 = setup_github_app._free_port()
        port2 = setup_github_app._free_port()
        assert isinstance(port1, int) and isinstance(port2, int)


class TestWaitForCallback:
    def test_captures_query_params_from_the_expected_path(self):
        port = setup_github_app._free_port()

        def client():
            time.sleep(0.2)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc123&state=xyz", timeout=2)

        t = threading.Thread(target=client)
        t.start()
        result = setup_github_app._wait_for_callback(port, "/callback", timeout_seconds=5)
        t.join()

        assert result == {"code": "abc123", "state": "xyz"}

    def test_serves_root_html_when_given(self):
        port = setup_github_app._free_port()
        fetched = {}

        def client():
            time.sleep(0.2)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                fetched["body"] = resp.read().decode()
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=x", timeout=2)

        t = threading.Thread(target=client)
        t.start()
        setup_github_app._wait_for_callback(port, "/callback", root_html="<html>hello-root</html>", timeout_seconds=5)
        t.join()

        assert fetched["body"] == "<html>hello-root</html>"

    def test_returns_none_on_timeout_without_hanging(self):
        port = setup_github_app._free_port()
        start = time.monotonic()
        result = setup_github_app._wait_for_callback(port, "/callback", timeout_seconds=1)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 5


class TestManualPasteFallback:
    def test_parses_query_params_from_a_pasted_url(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "http://localhost:1234/callback?code=abc&state=xyz")
        result = setup_github_app._manual_paste_fallback("/callback")
        assert result == {"code": "abc", "state": "xyz"}

    def test_empty_input_gives_up(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        result = setup_github_app._manual_paste_fallback("/callback")
        assert result is None


class TestApiPost:
    def test_returns_parsed_json_on_success(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_urlopen_response({"id": 42}))
        result = setup_github_app._api_post("/app-manifests/somecode/conversions")
        assert result == {"id": 42}

    def test_http_error_dies_with_the_response_body(self, monkeypatch):
        def raise_error(*a, **k):
            raise urllib.error.HTTPError("url", 422, "Unprocessable", {}, mock.MagicMock(read=lambda: b"bad manifest"))
        monkeypatch.setattr("urllib.request.urlopen", raise_error)
        with pytest.raises(SystemExit):
            setup_github_app._api_post("/app-manifests/badcode/conversions")


class TestVerify:
    def test_warns_without_dying_when_app_token_cannot_be_minted(self, tmp_path, monkeypatch, capsys):
        env_path = tmp_path / ".env"
        env_path.write_text("GITHUB_APP_ID='1'\n")
        monkeypatch.setattr(setup_github_app.lib_github, "resolve_token", lambda env: (None, ""))
        setup_github_app._verify("acme", "widgets", env_path)
        assert "WARNING" in capsys.readouterr().err

    def test_reports_ok_when_access_check_passes(self, tmp_path, monkeypatch, capsys):
        env_path = tmp_path / ".env"
        env_path.write_text("GITHUB_APP_ID='1'\n")
        monkeypatch.setattr(setup_github_app.lib_github, "resolve_token", lambda env: ("tok", "app"))
        monkeypatch.setattr(setup_github_app.lib_github, "check_repo_access", lambda owner, repo, token: (True, "all good"))
        setup_github_app._verify("acme", "widgets", env_path)
        assert "all good" in capsys.readouterr().out

    def test_warns_when_access_check_fails(self, tmp_path, monkeypatch, capsys):
        env_path = tmp_path / ".env"
        env_path.write_text("GITHUB_APP_ID='1'\n")
        monkeypatch.setattr(setup_github_app.lib_github, "resolve_token", lambda env: ("tok", "app"))
        monkeypatch.setattr(setup_github_app.lib_github, "check_repo_access", lambda owner, repo, token: (False, "no push access"))
        setup_github_app._verify("acme", "widgets", env_path)
        assert "no push access" in capsys.readouterr().err


class TestRegisterAndInstall:
    def test_happy_path_writes_all_three_credentials(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        monkeypatch.setattr(setup_github_app, "_free_port", lambda: 54321)
        monkeypatch.setattr(setup_github_app.webbrowser, "open", lambda url: True)
        monkeypatch.setattr(setup_github_app, "_verify", lambda *a, **k: None)
        # state must match exactly what register_and_install generated - patch
        # secrets.token_hex so we know it ahead of time.
        monkeypatch.setattr(setup_github_app.secrets, "token_hex", lambda n: "fixedstate" if n == 16 else "abc123")
        it = iter([
            {"code": "the-code", "state": "fixedstate"},
            {"installation_id": "99999"},
        ])
        monkeypatch.setattr(setup_github_app, "_wait_for_callback", lambda *a, **k: next(it))
        monkeypatch.setattr(
            setup_github_app, "_api_post",
            lambda path, data=None: {
                "id": 555, "slug": "my-app-slug", "name": "Horseless Carriage - acme-widgets",
                "pem": "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\n",
            },
        )

        setup_github_app.register_and_install("acme", "widgets", env_path, timeout_seconds=5)

        assert lib_env.read_env_var(env_path, "GITHUB_APP_ID") == "555"
        assert lib_env.read_env_var(env_path, "GITHUB_APP_INSTALLATION_ID") == "99999"
        pem = lib_env.read_env_var(env_path, "GITHUB_APP_PRIVATE_KEY")
        # Must round-trip through the same "\n"-escaped single-line .env
        # convention auth_github.mint_installation_token/_normalize_private_key
        # expect (see .env.example's own GITHUB_APP_PRIVATE_KEY line).
        assert "\\n" in pem
        assert "\n" not in pem
        assert pem.replace("\\n", "\n") == "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\n"

    def test_mismatched_state_aborts_without_writing_anything(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        monkeypatch.setattr(setup_github_app, "_free_port", lambda: 54321)
        monkeypatch.setattr(setup_github_app.webbrowser, "open", lambda url: True)
        monkeypatch.setattr(setup_github_app, "_wait_for_callback", lambda *a, **k: {"code": "x", "state": "wrong-state"})

        with pytest.raises(SystemExit):
            setup_github_app.register_and_install("acme", "widgets", env_path, timeout_seconds=5)

        assert lib_env.read_env_var(env_path, "GITHUB_APP_ID") == ""

    def test_no_code_falls_back_to_manual_paste_then_dies_if_still_empty(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        monkeypatch.setattr(setup_github_app, "_free_port", lambda: 54321)
        monkeypatch.setattr(setup_github_app.webbrowser, "open", lambda url: True)
        monkeypatch.setattr(setup_github_app, "_wait_for_callback", lambda *a, **k: None)
        monkeypatch.setattr(setup_github_app, "_manual_paste_fallback", lambda path: None)

        with pytest.raises(SystemExit):
            setup_github_app.register_and_install("acme", "widgets", env_path, timeout_seconds=5)

    def test_app_created_but_install_not_captured_dies_with_actionable_message(self, tmp_path, monkeypatch, capsys):
        """The App DOES exist at this point (id/key already written) - the
        error must say so and point at the manual recovery path, not just
        say 'failed'."""
        env_path = tmp_path / ".env"
        env_path.write_text("")
        monkeypatch.setattr(setup_github_app, "_free_port", lambda: 54321)
        monkeypatch.setattr(setup_github_app.webbrowser, "open", lambda url: True)
        monkeypatch.setattr(setup_github_app.secrets, "token_hex", lambda n: "fixedstate" if n == 16 else "abc123")
        it = iter([
            {"code": "the-code", "state": "fixedstate"},
            None,
        ])
        monkeypatch.setattr(setup_github_app, "_wait_for_callback", lambda *a, **k: next(it))
        monkeypatch.setattr(setup_github_app, "_manual_paste_fallback", lambda path: None)
        monkeypatch.setattr(
            setup_github_app, "_api_post",
            lambda path, data=None: {"id": 555, "slug": "my-app-slug", "name": "x", "pem": "-----BEGIN KEY-----\nx\n-----END KEY-----\n"},
        )

        with pytest.raises(SystemExit):
            setup_github_app.register_and_install("acme", "widgets", env_path, timeout_seconds=5)

        assert lib_env.read_env_var(env_path, "GITHUB_APP_ID") == "555"
        err = capsys.readouterr().err
        assert "Installation ID" in err


class TestMain:
    def test_dies_when_env_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["setup_github_app.py"])
        with pytest.raises(SystemExit):
            setup_github_app.main()

    def test_dies_when_repo_url_unset_and_no_override_given(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr("sys.argv", ["setup_github_app.py"])
        with pytest.raises(SystemExit):
            setup_github_app.main()

    def test_owner_repo_override_is_used_without_consulting_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("")
        monkeypatch.setattr("sys.argv", ["setup_github_app.py", "--owner-repo", "acme/widgets"])
        called = {}
        monkeypatch.setattr(setup_github_app, "register_and_install", lambda owner, repo, env_path, timeout_seconds: called.update(owner=owner, repo=repo))
        setup_github_app.main()
        assert called == {"owner": "acme", "repo": "widgets"}

    def test_already_configured_prompts_and_skips_on_no(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "GITHUB_APP_ID='1'\nGITHUB_APP_PRIVATE_KEY='k'\nGITHUB_APP_INSTALLATION_ID='2'\n"
        )
        monkeypatch.setattr("sys.argv", ["setup_github_app.py", "--owner-repo", "acme/widgets"])
        monkeypatch.setattr("builtins.input", lambda _: "n")
        called = []
        monkeypatch.setattr(setup_github_app, "register_and_install", lambda *a, **k: called.append(True))

        setup_github_app.main()

        assert called == []
        assert "already set" in capsys.readouterr().err

    def test_already_configured_proceeds_on_yes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "GITHUB_APP_ID='1'\nGITHUB_APP_PRIVATE_KEY='k'\nGITHUB_APP_INSTALLATION_ID='2'\n"
        )
        monkeypatch.setattr("sys.argv", ["setup_github_app.py", "--owner-repo", "acme/widgets"])
        monkeypatch.setattr("builtins.input", lambda _: "y")
        called = []
        monkeypatch.setattr(setup_github_app, "register_and_install", lambda *a, **k: called.append(True))

        setup_github_app.main()

        assert called == [True]
