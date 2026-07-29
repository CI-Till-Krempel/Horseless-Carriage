# agents/scrum_team/tests/test_state_persistence.py
import subprocess
import unittest
import os
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.scrum import (
    save_state_to_repo,
    load_state_from_repo,
    REPO_STATE_KEYS,
)
from agents.scrum_team.state import ScrumState


def _init_git_repo(repo_path: Path) -> None:
    """A real (not just os.environ-pointed-at) local git repo, so
    save_state_to_repo()'s checkpoint commit and load_state_from_repo()'s
    git-recovery fallback have something to actually operate on."""
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)


class TestStatePersistence(unittest.TestCase):
    def setUp(self):
        self.test_repo = Path("test_repo_state_persistence")
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        self.test_repo.mkdir(exist_ok=True)

        # Isolate from the real repository during tests
        self.old_internal_path = os.environ.get("INTERNAL_STATE_REPO_PATH")
        if "INTERNAL_STATE_REPO_PATH" in os.environ:
            del os.environ["INTERNAL_STATE_REPO_PATH"]

        os.environ["STATE_REPO_PATH"] = str(self.test_repo.absolute())

        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()

    def tearDown(self):
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        if "STATE_REPO_PATH" in os.environ:
            del os.environ["STATE_REPO_PATH"]
        if self.old_internal_path:
            os.environ["INTERNAL_STATE_REPO_PATH"] = self.old_internal_path

    def test_transcript_is_in_the_persisted_allowlist(self):
        self.assertIn("transcript", REPO_STATE_KEYS)

    def test_save_state_to_repo_persists_transcript(self):
        self.tool_context.state["transcript"] = [
            {"agent_name": "DevTeam", "role": "model", "content": "Implemented the feature."},
            {"agent_name": "QA", "role": "model", "content": "Reviewed and approved."},
        ]

        result = save_state_to_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        state_file = self.test_repo / ".hc" / "state.json"
        self.assertTrue(state_file.exists())
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["transcript"], self.tool_context.state["transcript"])

    def test_save_state_to_repo_handles_empty_transcript(self):
        # Edge case: no sub-agent turns yet — must persist without error.
        self.tool_context.state["transcript"] = []

        result = save_state_to_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["transcript"], [])

    def test_load_state_from_repo_restores_transcript_after_restart(self):
        transcript = [{"agent_name": "ProductOwner", "role": "model", "content": "Prioritized backlog."}]
        self.tool_context.state["transcript"] = transcript
        save_state_to_repo(tool_context=self.tool_context)

        # Simulate a restart: fresh state with no transcript in memory.
        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        self.assertEqual(fresh_context.state["transcript"], [])

        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(fresh_context.state["transcript"], transcript)

    def test_hc_version_is_in_the_persisted_allowlist(self):
        self.assertIn("hc_version", REPO_STATE_KEYS)

    def test_save_state_to_repo_persists_hc_version(self):
        self.tool_context.state["hc_version"] = "0.1.0"

        result = save_state_to_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["hc_version"], "0.1.0")

    @patch("agents.scrum_team.tools.scrum.migrate_state")
    def test_load_state_from_repo_runs_migrations_against_recorded_version(self, mock_migrate_state):
        """
        Acceptance Criteria (release process, see RELEASE.md "Migration
        scaffold"): a .hc/state.json's recorded hc_version is passed to
        migrate_state() before merging into the live session state, so an
        older persisted shape gets a chance to be fixed up.
        """
        self.tool_context.state["hc_version"] = "0.1.0"
        self.tool_context.state["transcript"] = [{"agent_name": "DevTeam", "role": "model", "content": "did stuff"}]
        save_state_to_repo(tool_context=self.tool_context)
        mock_migrate_state.side_effect = lambda data, from_version: data

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        mock_migrate_state.assert_called_once()
        called_data, called_from_version = mock_migrate_state.call_args[0]
        self.assertEqual(called_from_version, "0.1.0")
        self.assertEqual(called_data["hc_version"], "0.1.0")

    def test_save_state_to_repo_excludes_keys_outside_allowlist(self):
        # Confirm REPO_STATE_KEYS is a deliberate allowlist, not a full dump —
        # e.g. github_token (sensitive) must never be written to the state repo.
        self.tool_context.state["github_token"] = "should-not-be-persisted"

        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertNotIn("github_token", saved)

    def test_litellm_keys_are_never_persisted_to_the_state_repo(self):
        """
        Acceptance Criteria (security review ahead of the public v0.1.0
        release, see SECURITY.md): per-agent LiteLLM virtual API keys are a
        real secret and must never be written into the target repo's
        .hc/state.json, which is typically committed to git.
        """
        self.assertNotIn("litellm_keys", REPO_STATE_KEYS)
        self.tool_context.state["litellm_keys"] = {"DevTeam": "sk-should-not-be-persisted"}

        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertNotIn("litellm_keys", saved)


class TestStateCheckpointCommit(unittest.TestCase):
    """Acceptance Criteria (GH issue #59): save_state_to_repo() commits
    .hc/state.json to the repo's local git history as a checkpoint,
    whenever repo_root is actually a git repo - a plain directory (as in
    TestStatePersistence above) is left exactly as before (no crash, no
    git side effects)."""

    def setUp(self):
        self.test_repo = Path("test_repo_state_checkpoint")
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        self.test_repo.mkdir(exist_ok=True)
        _init_git_repo(self.test_repo)

        self.old_internal_path = os.environ.get("INTERNAL_STATE_REPO_PATH")
        if "INTERNAL_STATE_REPO_PATH" in os.environ:
            del os.environ["INTERNAL_STATE_REPO_PATH"]
        os.environ["STATE_REPO_PATH"] = str(self.test_repo.absolute())

        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()

    def tearDown(self):
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        if "STATE_REPO_PATH" in os.environ:
            del os.environ["STATE_REPO_PATH"]
        if self.old_internal_path:
            os.environ["INTERNAL_STATE_REPO_PATH"] = self.old_internal_path

    def _log(self):
        return subprocess.run(
            ["git", "log", "--oneline", "--", ".hc/state.json"],
            cwd=self.test_repo, capture_output=True, text=True,
        ).stdout

    def test_save_creates_a_checkpoint_commit(self):
        self.tool_context.state["sprint_goal"] = "Ship the thing"
        result = save_state_to_repo(tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertIn("checkpoint", self._log())

    def test_second_save_with_no_changes_does_not_error(self):
        save_state_to_repo(tool_context=self.tool_context)
        result = save_state_to_repo(tool_context=self.tool_context)  # nothing changed - "nothing to commit"
        self.assertEqual(result["status"], "ok")

    def test_successive_saves_each_add_a_checkpoint(self):
        self.tool_context.state["sprint_goal"] = "First goal"
        save_state_to_repo(tool_context=self.tool_context)
        self.tool_context.state["sprint_goal"] = "Second goal"
        save_state_to_repo(tool_context=self.tool_context)
        self.assertEqual(len(self._log().strip().splitlines()), 2)

    def test_plain_directory_state_repo_is_not_touched_by_git(self):
        plain_repo = Path("test_repo_state_checkpoint_plain")
        if plain_repo.exists():
            shutil.rmtree(plain_repo)
        plain_repo.mkdir()
        try:
            os.environ["STATE_REPO_PATH"] = str(plain_repo.absolute())
            result = save_state_to_repo(tool_context=self.tool_context)
            self.assertEqual(result["status"], "ok")
            self.assertFalse((plain_repo / ".git").exists())
        finally:
            shutil.rmtree(plain_repo)


class TestLoadStateGitRecovery(unittest.TestCase):
    """Acceptance Criteria (GH issue #59): "these checkpoints in git are
    the fallback option if the state gets corrupted" - load_state_from_repo
    recovers the last git-committed checkpoint when the working-tree
    state.json is corrupted, and repairs the file on disk with what it
    recovered."""

    def setUp(self):
        self.test_repo = Path("test_repo_state_recovery")
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        self.test_repo.mkdir(exist_ok=True)
        _init_git_repo(self.test_repo)

        self.old_internal_path = os.environ.get("INTERNAL_STATE_REPO_PATH")
        if "INTERNAL_STATE_REPO_PATH" in os.environ:
            del os.environ["INTERNAL_STATE_REPO_PATH"]
        os.environ["STATE_REPO_PATH"] = str(self.test_repo.absolute())

        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()

    def tearDown(self):
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        if "STATE_REPO_PATH" in os.environ:
            del os.environ["STATE_REPO_PATH"]
        if self.old_internal_path:
            os.environ["INTERNAL_STATE_REPO_PATH"] = self.old_internal_path

    def test_corrupted_state_file_recovers_from_last_checkpoint(self):
        self.tool_context.state["sprint_goal"] = "Good state, checkpointed"
        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        state_file.write_text("{not valid json", encoding="utf-8")  # simulate a torn/corrupted write

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result.get("recovered_from_git"))
        self.assertEqual(fresh_context.state["sprint_goal"], "Good state, checkpointed")

    def test_recovery_repairs_the_working_tree_file(self):
        self.tool_context.state["sprint_goal"] = "Good state, checkpointed"
        save_state_to_repo(tool_context=self.tool_context)
        state_file = self.test_repo / ".hc" / "state.json"
        state_file.write_text("{not valid json", encoding="utf-8")

        load_state_from_repo(tool_context=MagicMock(state=ScrumState().model_dump()))

        repaired = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(repaired["sprint_goal"], "Good state, checkpointed")

    def test_corrupted_file_with_no_prior_checkpoint_reports_error(self):
        state_dir = self.test_repo / ".hc"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text("{not valid json", encoding="utf-8")

        result = load_state_from_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("no recoverable git checkpoint", result["message"])

    def test_non_git_repo_with_corrupted_file_reports_error(self):
        plain_repo = Path("test_repo_state_recovery_plain")
        if plain_repo.exists():
            shutil.rmtree(plain_repo)
        state_dir = plain_repo / ".hc"
        state_dir.mkdir(parents=True)
        (state_dir / "state.json").write_text("{not valid json", encoding="utf-8")
        try:
            os.environ["STATE_REPO_PATH"] = str(plain_repo.absolute())
            result = load_state_from_repo(tool_context=self.tool_context)
            self.assertEqual(result["status"], "error")
        finally:
            shutil.rmtree(plain_repo)


if __name__ == "__main__":
    unittest.main()
