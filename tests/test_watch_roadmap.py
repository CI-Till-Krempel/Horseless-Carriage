import subprocess

import watch_roadmap


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


class TestCountStoriesReadyForNextStage:
    def test_missing_file_returns_zero(self, tmp_path):
        assert watch_roadmap.count_stories_ready_for_next_stage(tmp_path / "state.json") == 0

    def test_invalid_json_returns_zero(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{not valid json", encoding="utf-8")
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 0

    def test_ready_but_not_implemented_counts(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Ready"]}], "product_backlog": []}', encoding="utf-8")
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 1

    def test_draft_but_not_ready_counts_too(self, tmp_path):
        """GH issue #94: Draft is now the first real STORY_STAGES stage -
        a story stuck there (not yet Ready) is genuinely "ready for the
        Product Owner to move forward", same as any other stage gap."""
        state_file = tmp_path / "state.json"
        state_file.write_text('{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Draft"]}], "product_backlog": []}', encoding="utf-8")
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 1

    def test_reviewed_but_not_tested_counts_too(self, tmp_path):
        """Not just the "Ready" stage specifically - any story one stage
        short of the next owner picking it up counts (e.g. ready for QA)."""
        state_file = tmp_path / "state.json"
        state_file.write_text(
            '{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Ready", "Implemented", "Reviewed"]}], "product_backlog": []}',
            encoding="utf-8",
        )
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 1

    def test_fully_accepted_story_does_not_count(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(
            '{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Ready", "Implemented", "Reviewed", "Tested", "Accepted"]}], "product_backlog": []}',
            encoding="utf-8",
        )
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 0

    def test_story_with_no_stages_completed_does_not_count(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"sprint_backlog": [{"id": "US-1"}], "product_backlog": []}', encoding="utf-8")
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 0

    def test_counts_across_both_backlogs(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(
            '{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Ready"]}], '
            '"product_backlog": [{"id": "US-2", "stages_completed": ["Ready", "Implemented", "Reviewed"]}]}',
            encoding="utf-8",
        )
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 2

    def test_non_dict_top_level_returns_zero(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("[1, 2, 3]", encoding="utf-8")
        assert watch_roadmap.count_stories_ready_for_next_stage(state_file) == 0


class TestDevelopBranchHead:
    def test_returns_stripped_sha_on_success(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _completed(cmd, 0)
            return _completed(cmd, 0, "abc123\n")
        monkeypatch.setattr(watch_roadmap.subprocess, "run", fake_run)
        assert watch_roadmap.develop_branch_head(tmp_path, "develop") == "abc123"

    def test_rev_parse_failure_returns_empty_string(self, monkeypatch, tmp_path):
        monkeypatch.setattr(watch_roadmap.subprocess, "run", lambda cmd, **k: _completed(cmd, 1))
        assert watch_roadmap.develop_branch_head(tmp_path, "develop") == ""

    def test_exception_returns_empty_string_instead_of_raising(self, monkeypatch, tmp_path):
        def raise_missing(*a, **k):
            raise FileNotFoundError("no such file: git")
        monkeypatch.setattr(watch_roadmap.subprocess, "run", raise_missing)
        assert watch_roadmap.develop_branch_head(tmp_path, "develop") == ""


class TestCheckOnce:
    def _env(self, tmp_path, state_repo):
        env_path = tmp_path / ".env"
        env_path.write_text(f'STATE_REPO_PATH="{state_repo}"\nGITHUB_DEVELOP_BRANCH="develop"\n', encoding="utf-8")
        return env_path

    def test_no_state_repo_path_never_triggers(self, tmp_path, monkeypatch, capsys):
        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")

        def fail_if_called(*a, **k):
            raise AssertionError("should not touch git when STATE_REPO_PATH isn't set")
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", fail_if_called)

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "")
        assert triggered is False
        assert ready_count == 0
        assert capsys.readouterr().err == ""

    def test_first_check_with_no_backlog_work_does_not_trigger(self, tmp_path, monkeypatch, capsys):
        state_repo = tmp_path / "state_repo"
        (state_repo / ".hc").mkdir(parents=True)
        env_path = self._env(tmp_path, state_repo)
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", lambda repo, branch: "abc123")

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "")

        assert triggered is False
        assert new_head == "abc123"
        assert ready_count == 0
        assert capsys.readouterr().err == ""

    def test_new_commits_since_last_check_triggers_and_notifies(self, tmp_path, monkeypatch, capsys):
        state_repo = tmp_path / "state_repo"
        (state_repo / ".hc").mkdir(parents=True)
        env_path = self._env(tmp_path, state_repo)
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", lambda repo, branch: "def456")

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "abc123")

        assert triggered is True
        assert new_head == "def456"
        err = capsys.readouterr().err
        assert "NEW WORK" in err
        assert "develop" in err

    def test_same_head_as_last_check_does_not_trigger(self, tmp_path, monkeypatch, capsys):
        state_repo = tmp_path / "state_repo"
        (state_repo / ".hc").mkdir(parents=True)
        env_path = self._env(tmp_path, state_repo)
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", lambda repo, branch: "abc123")

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "abc123")

        assert triggered is False
        assert capsys.readouterr().err == ""

    def test_ready_story_triggers_even_without_new_commits(self, tmp_path, monkeypatch, capsys):
        state_repo = tmp_path / "state_repo"
        (state_repo / ".hc").mkdir(parents=True)
        (state_repo / ".hc" / "state.json").write_text(
            '{"sprint_backlog": [{"id": "US-1", "stages_completed": ["Ready"]}], "product_backlog": []}',
            encoding="utf-8",
        )
        env_path = self._env(tmp_path, state_repo)
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", lambda repo, branch: "abc123")

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "abc123")

        assert triggered is True
        assert ready_count == 1
        assert "ready for the next pipeline stage" in capsys.readouterr().err

    def test_unreachable_remote_does_not_crash_and_does_not_treat_as_new(self, tmp_path, monkeypatch, capsys):
        state_repo = tmp_path / "state_repo"
        (state_repo / ".hc").mkdir(parents=True)
        env_path = self._env(tmp_path, state_repo)
        monkeypatch.setattr(watch_roadmap, "develop_branch_head", lambda repo, branch: "")

        triggered, new_head, ready_count = watch_roadmap.check_once(env_path, "abc123")

        assert triggered is False
        assert new_head == "abc123"  # kept the last-known-good head, not blanked out
        assert capsys.readouterr().err == ""


class TestMain:
    def test_once_flag_exits_zero_when_triggered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_roadmap, "check_once", lambda env_path, last_seen_head: (True, "abc123", 1))

        try:
            watch_roadmap.main(argv=["--once"], repo_root=tmp_path)
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code == 0

    def test_once_flag_exits_one_when_not_triggered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_roadmap, "check_once", lambda env_path, last_seen_head: (False, "", 0))

        try:
            watch_roadmap.main(argv=["--once"], repo_root=tmp_path)
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code == 1

    def test_invalid_poll_interval_falls_back_to_default(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".env").write_text('WATCH_POLL_INTERVAL_SECONDS="not-a-number"\n', encoding="utf-8")
        monkeypatch.setattr(watch_roadmap, "check_once", lambda env_path, last_seen_head: (False, "", 0))

        try:
            watch_roadmap.main(argv=["--once"], repo_root=tmp_path)
        except SystemExit:
            pass

        assert f"every {watch_roadmap.DEFAULT_POLL_INTERVAL_SECONDS}s" in capsys.readouterr().out
