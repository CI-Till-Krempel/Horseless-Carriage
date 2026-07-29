import json

import lib_github


class TestParseOwnerRepo:
    def test_ssh_url(self):
        assert lib_github.parse_owner_repo("git@github.com:example/example.git") == ("example", "example")

    def test_https_url_with_dot_git(self):
        assert lib_github.parse_owner_repo("https://github.com/example/example.git") == ("example", "example")

    def test_https_url_without_dot_git(self):
        assert lib_github.parse_owner_repo("https://github.com/example/example") == ("example", "example")

    def test_empty_string_returns_none(self):
        assert lib_github.parse_owner_repo("") is None

    def test_non_github_url_returns_none(self):
        assert lib_github.parse_owner_repo("https://gitlab.com/example/example.git") is None


class TestResolveToken:
    def test_github_token_takes_priority(self):
        env = {"GITHUB_TOKEN": "tok123", "GITHUB_APP_ID": "1"}
        assert lib_github.resolve_token(env) == ("tok123", "token")

    def test_nothing_configured_returns_empty_source(self):
        assert lib_github.resolve_token({}) == (None, "")

    def test_incomplete_app_trio_returns_empty_source(self):
        env = {"GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key"}  # missing installation id
        assert lib_github.resolve_token(env) == (None, "")

    def test_full_app_trio_mints_token_via_auth_github(self, monkeypatch):
        import auth_github
        monkeypatch.setattr(auth_github, "mint_installation_token", lambda a, p, i: "minted-token")
        env = {"GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2"}
        assert lib_github.resolve_token(env) == ("minted-token", "app")

    def test_mint_failure_returns_none_with_app_source(self, monkeypatch):
        import auth_github

        def raise_error(a, p, i):
            raise RuntimeError("boom")
        monkeypatch.setattr(auth_github, "mint_installation_token", raise_error)
        env = {"GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "key", "GITHUB_APP_INSTALLATION_ID": "2"}
        assert lib_github.resolve_token(env) == (None, "app")


class TestCheckRepoAccess:
    def test_all_reads_succeed_with_push_permission(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"permissions": {"push": True}})),
            (200, "[]"),
            (200, "[]"),
        ])
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: next(responses))
        ok, detail = lib_github.check_repo_access("example", "example", "tok")
        assert ok is True
        assert "issues" in detail and "pull requests" in detail

    def test_reads_succeed_without_push_permission_is_not_ok(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"permissions": {"push": False}})),
            (200, "[]"),
            (200, "[]"),
        ])
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: next(responses))
        ok, detail = lib_github.check_repo_access("example", "example", "tok")
        assert ok is False
        assert "do NOT include push" in detail

    def test_repo_read_failure_short_circuits(self, monkeypatch):
        calls = []

        def fake_get(path, token):
            calls.append(path)
            return 404, "not found"
        monkeypatch.setattr(lib_github, "_api_get", fake_get)
        ok, detail = lib_github.check_repo_access("example", "example", "tok")
        assert ok is False
        assert "repo read failed" in detail
        assert calls == ["/repos/example/example"]  # never got to issues/pulls

    def test_issues_read_failure_reported(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"permissions": {"push": True}})),
            (403, "forbidden"),
        ])
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: next(responses))
        ok, detail = lib_github.check_repo_access("example", "example", "tok")
        assert ok is False
        assert "issues read failed" in detail

    def test_pulls_read_failure_reported(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"permissions": {"push": True}})),
            (200, "[]"),
            (403, "forbidden"),
        ])
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: next(responses))
        ok, detail = lib_github.check_repo_access("example", "example", "tok")
        assert ok is False
        assert "pull requests read failed" in detail
