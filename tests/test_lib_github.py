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


class TestCheckBranchExists:
    def test_branch_exists(self, monkeypatch):
        calls = []

        def fake_get(path, token):
            calls.append(path)
            return 200, json.dumps({"name": "develop"})
        monkeypatch.setattr(lib_github, "_api_get", fake_get)

        ok, detail = lib_github.check_branch_exists("example", "example", "develop", "tok")

        assert ok is True
        assert "exists" in detail
        assert calls == ["/repos/example/example/branches/develop"]

    def test_branch_missing(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (404, "Branch not found"))

        ok, detail = lib_github.check_branch_exists("example", "example", "develop", "tok")

        assert ok is False
        assert "does not exist" in detail

    def test_other_http_error_reported_distinctly_from_missing(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (403, "forbidden"))

        ok, detail = lib_github.check_branch_exists("example", "example", "develop", "tok")

        assert ok is False
        assert "does not exist" not in detail
        assert "could not check" in detail
        assert "403" in detail


class TestLatestRelease:
    def test_returns_tag_and_resolved_commit_sha(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"tag_name": "v1.0.0"})),
            (200, json.dumps({"sha": "e83912ed1234567890abcdef1234567890abcdef"})),
        ])
        calls = []

        def fake_get(path, token):
            calls.append(path)
            return next(responses)
        monkeypatch.setattr(lib_github, "_api_get", fake_get)

        release = lib_github.latest_release("example", "example", "tok")

        assert release == {"tag_name": "v1.0.0", "commit_sha": "e83912ed1234567890abcdef1234567890abcdef"}
        assert calls == [
            "/repos/example/example/releases/latest",
            "/repos/example/example/commits/v1.0.0",
        ]

    def test_no_releases_returns_none(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (404, "Not Found"))
        assert lib_github.latest_release("example", "example", "tok") is None

    def test_commit_lookup_failure_still_returns_tag_with_no_commit(self, monkeypatch):
        responses = iter([
            (200, json.dumps({"tag_name": "v1.0.0"})),
            (403, "forbidden"),
        ])
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: next(responses))

        release = lib_github.latest_release("example", "example", "tok")

        assert release == {"tag_name": "v1.0.0", "commit_sha": None}


class TestGetDefaultBranch:
    def test_returns_default_branch_field(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (200, json.dumps({"default_branch": "master"})))
        assert lib_github.get_default_branch("example", "example", "tok") == "master"

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (404, "not found"))
        assert lib_github.get_default_branch("example", "example", "tok") is None


class TestGetBranchSha:
    def test_returns_tip_sha(self, monkeypatch):
        calls = []

        def fake_get(path, token):
            calls.append(path)
            return 200, json.dumps({"commit": {"sha": "abc123"}})
        monkeypatch.setattr(lib_github, "_api_get", fake_get)

        assert lib_github.get_branch_sha("example", "example", "main", "tok") == "abc123"
        assert calls == ["/repos/example/example/branches/main"]

    def test_returns_none_when_branch_missing(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_get", lambda path, token: (404, "not found"))
        assert lib_github.get_branch_sha("example", "example", "develop", "tok") is None


class TestCreateBranch:
    def test_success(self, monkeypatch):
        calls = []

        def fake_post(path, token, payload):
            calls.append((path, payload))
            return 201, json.dumps({"ref": "refs/heads/develop"})
        monkeypatch.setattr(lib_github, "_api_post", fake_post)

        ok, detail = lib_github.create_branch("example", "example", "develop", "abc123", "tok")

        assert ok is True
        assert "created branch 'develop'" in detail
        assert calls == [("/repos/example/example/git/refs", {"ref": "refs/heads/develop", "sha": "abc123"})]

    def test_failure_reports_http_status(self, monkeypatch):
        monkeypatch.setattr(lib_github, "_api_post", lambda path, token, payload: (403, "forbidden"))

        ok, detail = lib_github.create_branch("example", "example", "develop", "abc123", "tok")

        assert ok is False
        assert "could not create branch 'develop'" in detail
        assert "403" in detail
