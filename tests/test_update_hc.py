"""Tests for update_hc.py.

Git plumbing (tags, branches, fetch, checkout) is exercised against real,
local-only git repos (an "origin" + a clone of it, both under tmp_path) -
no network, no Docker - rather than mocking git itself, since the whole
point of this module is getting that plumbing right. Only the
Docker/dashboard/doctor edges (actually starting/stopping a stack, actually
rebuilding images, actually running every doctor.py check) are mocked.
"""
import subprocess
from pathlib import Path

import pytest

import dashboard
import doctor
import update_hc


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-c", "user.email=test@test.invalid", "-c", "user.name=test", *args],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stdout}\n{result.stderr}"
    return result


def _init_origin(tmp_path: Path, extra_files: dict = None) -> Path:
    """A real git repo (origin), a single commit on `main`, VERSION=0.1.0,
    tagged v0.1.0 - the published starting point every test clones from."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    (origin / "VERSION").write_text("0.1.0\n")
    (origin / ".env.example").write_text("EXISTING_KEY=default\n")
    for name, content in (extra_files or {}).items():
        (origin / name).write_text(content)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "v0.1.0")
    _git(origin, "tag", "v0.1.0")
    return origin


def _clone(origin: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, capture_output=True, text=True)
    (dest / ".env").write_text("")
    return dest


@pytest.fixture(autouse=True)
def _stub_stack_lifecycle(monkeypatch):
    """Every test gets Docker/doctor fully stubbed out by default (fast,
    deterministic, no real containers) - tests about the stop/start/rebuild
    *decisions* override the specific stub they care about and assert on
    calls recorded here."""
    calls = {"stop": [], "start": [], "rebuild": []}
    monkeypatch.setattr(dashboard, "_stop_stack", lambda hc_path: calls["stop"].append(hc_path) or "Stopped.")
    monkeypatch.setattr(dashboard, "_start_stack", lambda hc_path: calls["start"].append(hc_path) or "Started.")
    monkeypatch.setattr(update_hc.lib_docker, "compose_running_services", lambda *a, **k: [])
    monkeypatch.setattr(update_hc.doctor, "check", lambda *a, **k: doctor.DoctorResult(items=[]))
    monkeypatch.setattr(
        update_hc.rebuild_images, "rebuild",
        lambda *a, **k: calls["rebuild"].append(a) or 0,
    )
    return calls


class TestResolveChannelRef:
    def test_main_channel_targets_origin_main(self, tmp_path):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        assert update_hc.resolve_channel_ref(hc_path, "main") == ("origin/main", "main")

    def test_release_channel_picks_highest_semver_tag(self, tmp_path):
        origin = _init_origin(tmp_path)
        (origin / "VERSION").write_text("0.2.0\n")
        _git(origin, "commit", "-q", "-am", "v0.2.0")
        _git(origin, "tag", "v0.2.0")
        (origin / "VERSION").write_text("0.10.0\n")
        _git(origin, "commit", "-q", "-am", "v0.10.0")
        _git(origin, "tag", "v0.10.0")
        hc_path = _clone(origin, tmp_path / "clone")
        update_hc._git(hc_path, "fetch", "origin", "--tags")

        # Version sort, not lexical - v0.10.0 must beat v0.2.0.
        assert update_hc.resolve_channel_ref(hc_path, "release") == ("v0.10.0", "v0.10.0")

    def test_release_channel_falls_back_to_main_with_no_tags_yet(self, tmp_path):
        origin = tmp_path / "origin"
        origin.mkdir()
        _git(origin, "init", "-q")
        _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
        (origin / "VERSION").write_text("0.1.0\n")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "initial, unreleased")
        hc_path = _clone(origin, tmp_path / "clone")

        ref, label = update_hc.resolve_channel_ref(hc_path, "release")
        assert ref == "origin/main"
        assert "no release tags found" in label

    def test_unknown_channel(self, tmp_path):
        ref, label = update_hc.resolve_channel_ref(tmp_path, "nightly")
        assert ref is None
        assert "nightly" in label


class TestNeedsRebuild:
    def test_no_trigger_files_changed(self):
        assert update_hc.needs_rebuild(["VERSION", "README.md"]) is False

    def test_requirements_txt_changed(self):
        assert update_hc.needs_rebuild(["agents/foo.py", "requirements.txt"]) is True

    def test_dockerfile_changed(self):
        assert update_hc.needs_rebuild(["agent.Dockerfile"]) is True

    def test_empty_changed_list(self):
        assert update_hc.needs_rebuild([]) is False


class TestSyncNewEnvKeys:
    def test_adds_only_missing_keys(self, tmp_path):
        (tmp_path / ".env").write_text("EXISTING_KEY=already-set\n")
        (tmp_path / ".env.example").write_text("EXISTING_KEY=default\nNEW_KEY=<placeholder>\n")

        added = update_hc._sync_new_env_keys(tmp_path)

        assert added == ["NEW_KEY"]
        env_text = (tmp_path / ".env").read_text()
        assert "EXISTING_KEY=already-set" in env_text  # untouched
        assert "NEW_KEY=<placeholder>" in env_text

    def test_noop_when_nothing_new(self, tmp_path):
        (tmp_path / ".env").write_text("EXISTING_KEY=already-set\n")
        (tmp_path / ".env.example").write_text("EXISTING_KEY=default\n")
        assert update_hc._sync_new_env_keys(tmp_path) == []

    def test_noop_when_env_missing(self, tmp_path):
        (tmp_path / ".env.example").write_text("NEW_KEY=x\n")
        assert update_hc._sync_new_env_keys(tmp_path) == []


class TestRunUpdate:
    def test_not_a_git_checkout(self, tmp_path):
        message = update_hc.run_update(tmp_path, channel="release")
        assert "cannot update" in message

    def test_unknown_channel(self, tmp_path):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        assert "Unknown channel" in update_hc.run_update(hc_path, channel="nightly")

    def test_dirty_checkout_blocks_update_without_force(self, tmp_path, _stub_stack_lifecycle):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        (hc_path / "VERSION").write_text("locally-edited\n")

        message = update_hc.run_update(hc_path, channel="release")

        assert "local changes" in message
        assert (hc_path / "VERSION").read_text() == "locally-edited\n"  # never touched

    def test_force_overrides_dirty_checkout(self, tmp_path):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        (hc_path / "VERSION").write_text("locally-edited\n")

        message = update_hc.run_update(hc_path, channel="release", force=True)

        assert "Already up to date" in message
        assert (hc_path / "VERSION").read_text() == "0.1.0\n"

    def test_already_up_to_date_still_stops_and_restarts_a_running_stack(self, tmp_path, monkeypatch, _stub_stack_lifecycle):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        monkeypatch.setattr(update_hc.lib_docker, "compose_running_services", lambda *a, **k: ["agent"])

        message = update_hc.run_update(hc_path, channel="release")

        assert "Already up to date" in message
        assert message.endswith("Started.")
        assert _stub_stack_lifecycle["stop"] == [hc_path]
        assert _stub_stack_lifecycle["start"] == [hc_path]
        assert _stub_stack_lifecycle["rebuild"] == []

    def test_leaves_a_stopped_stack_stopped(self, tmp_path):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        message = update_hc.run_update(hc_path, channel="release")
        assert "leaving it stopped" in message

    def test_updates_to_latest_release_tag(self, tmp_path):
        origin = _init_origin(tmp_path)
        hc_path = _clone(origin, tmp_path / "clone")
        _git(hc_path, "checkout", "-q", "--detach", "v0.1.0")

        (origin / "VERSION").write_text("0.2.0\n")
        _git(origin, "commit", "-q", "-am", "v0.2.0")
        _git(origin, "tag", "v0.2.0")

        message = update_hc.run_update(hc_path, channel="release")

        assert "Updated v0.1.0" in message
        assert "v0.2.0 v0.2.0" in message or "v0.2.0" in message
        assert (hc_path / "VERSION").read_text() == "0.2.0\n"

    def test_updates_to_latest_main_commit_and_rebuilds_when_warranted(self, tmp_path, _stub_stack_lifecycle):
        origin = _init_origin(tmp_path)
        hc_path = _clone(origin, tmp_path / "clone")

        (origin / "VERSION").write_text("0.2.0\n")
        (origin / "requirements.txt").write_text("some-new-dependency==1.0\n")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "bump a dependency")

        message = update_hc.run_update(hc_path, channel="main")

        assert "Updated v0.1.0" in message
        assert (hc_path / "VERSION").read_text() == "0.2.0\n"
        assert "rebuilding images" in message
        assert len(_stub_stack_lifecycle["rebuild"]) == 1

    def test_env_example_additions_are_synced_into_local_env(self, tmp_path):
        origin = _init_origin(tmp_path)
        hc_path = _clone(origin, tmp_path / "clone")

        (origin / ".env.example").write_text("EXISTING_KEY=default\nNEW_FEATURE_FLAG=false\n")
        _git(origin, "commit", "-q", "-am", "introduce NEW_FEATURE_FLAG")

        message = update_hc.run_update(hc_path, channel="main")

        assert "NEW_FEATURE_FLAG" in message
        assert "NEW_FEATURE_FLAG=false" in (hc_path / ".env").read_text()

    def test_doctor_errors_are_surfaced_not_swallowed(self, tmp_path, monkeypatch):
        hc_path = _clone(_init_origin(tmp_path), tmp_path / "clone")
        monkeypatch.setattr(
            update_hc.doctor, "check",
            lambda *a, **k: doctor.DoctorResult(items=[doctor.ActionableItem(severity="error", message="boom")]),
        )
        message = update_hc.run_update(hc_path, channel="release")
        assert "1 error(s)" in message


class TestMain:
    def test_exits_nonzero_on_failure_message(self, monkeypatch, capsys):
        monkeypatch.setattr(update_hc, "run_update", lambda *a, **k: "git fetch failed:\nboom")
        monkeypatch.setattr(update_hc.sys, "argv", ["update_hc.py"])
        with pytest.raises(SystemExit) as exc_info:
            update_hc.main()
        assert exc_info.value.code == 1

    def test_exits_zero_on_success_message(self, monkeypatch, capsys):
        monkeypatch.setattr(update_hc, "run_update", lambda *a, **k: "Already up to date (main, v0.1.0, abc12345).")
        monkeypatch.setattr(update_hc.sys, "argv", ["update_hc.py"])
        update_hc.main()  # must not raise
