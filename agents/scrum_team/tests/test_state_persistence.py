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
    get_corrupted_state_raw_content,
    save_repaired_state,
    reset_state_from_git,
    clear_corrupted_state,
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

    def test_transcript_is_never_persisted_to_the_state_repo(self):
        """
        Acceptance Criteria (GH issue #127): the raw, unbounded multi-agent
        transcript has no place in a git-committed, human-reviewable state
        file - it stays in-memory-only session state, used to render the
        sprint report excerpt and the human-readable Markdown transcript
        (specs/reports/TRANSCRIPT-*.md, see _write_conversation_transcript
        in tools/budget.py) and made durable separately via the per-run
        log at /app/sessions/transcript-<session-id>.log.
        """
        self.assertNotIn("transcript", REPO_STATE_KEYS)
        self.tool_context.state["transcript"] = [
            {"agent_name": "DevTeam", "role": "model", "content": "Implemented the feature."},
        ]

        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertNotIn("transcript", saved)

    def test_messages_is_still_in_the_persisted_allowlist(self):
        """
        Unlike transcript, `messages` (the flat ScrumOrchestrator-only
        history used to resume a session on a fresh checkout of the state
        repo) serves a distinct functional purpose, not just a debug
        record, and must keep surviving a restart.
        """
        self.assertIn("messages", REPO_STATE_KEYS)

    def test_budget_reset_flag_is_in_the_persisted_allowlist(self):
        """
        Acceptance Criteria (GH issue #110): budget_reset_since_last_sprint_start
        must survive a state reload (a session restart mid-sprint) - if it
        defaulted back to True on every reload instead, start_sprint's new
        "was the budget actually reset since the last sprint?" check would
        silently stop enforcing anything right after a restart.
        """
        self.assertIn("budget_reset_since_last_sprint_start", REPO_STATE_KEYS)

    def test_load_state_from_repo_does_not_restore_transcript_after_restart(self):
        """
        Deliberate behavior change (GH issue #127): since transcript is no
        longer written to .hc/state.json at all, it can no longer be
        restored from it either - a restart starts a fresh (empty)
        transcript. Continuity across a restart is instead provided by the
        per-run log (stable filename as long as SESSION_ID is unchanged)
        and by whatever Markdown transcript snapshots already exist under
        specs/reports/ from prior sprint reports.
        """
        self.tool_context.state["transcript"] = [
            {"agent_name": "ProductOwner", "role": "model", "content": "Prioritized backlog."}
        ]
        save_state_to_repo(tool_context=self.tool_context)

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()

        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(fresh_context.state["transcript"], [])

    def test_hc_version_is_in_the_persisted_allowlist(self):
        self.assertIn("hc_version", REPO_STATE_KEYS)

    def test_save_state_to_repo_persists_hc_version(self):
        self.tool_context.state["hc_version"] = "0.1.0"

        result = save_state_to_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["hc_version"], "0.1.0")

    def test_sprint_files_touched_is_in_the_persisted_allowlist(self):
        """
        Acceptance Criteria (GH issue #119): sprint_files_touched must
        persist alongside dev_touch_baseline (the count it's compared
        against for the Implemented-stage "real source file written" gate)
        - previously only the baseline was persisted, so a state reload
        mid-sprint reset the running list to empty while the baseline
        stayed at its old value, falsely blocking that gate until enough
        NEW writes accumulated again.
        """
        self.assertIn("sprint_files_touched", REPO_STATE_KEYS)

    def test_save_state_to_repo_persists_sprint_files_touched(self):
        self.tool_context.state["sprint_files_touched"] = ["app/main.py", "app/utils.py"]

        result = save_state_to_repo(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["sprint_files_touched"], ["app/main.py", "app/utils.py"])

    def test_load_state_from_repo_restores_sprint_files_touched_after_restart(self):
        self.tool_context.state["sprint_files_touched"] = ["app/main.py"]
        self.tool_context.state["dev_touch_baseline"] = 1
        save_state_to_repo(tool_context=self.tool_context)

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        self.assertEqual(fresh_context.state["sprint_files_touched"], [])

        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(fresh_context.state["sprint_files_touched"], ["app/main.py"])
        self.assertEqual(fresh_context.state["dev_touch_baseline"], 1)

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

    def test_recovers_an_earlier_commit_if_the_latest_one_is_also_corrupted(self):
        """
        Acceptance Criteria (GH issue #85): recovery must not stop at HEAD -
        if the *latest* checkpoint commit's own snapshot is itself
        corrupted (e.g. a torn write got committed before anyone noticed),
        an earlier commit may still be perfectly recoverable.
        """
        self.tool_context.state["sprint_goal"] = "Good state, checkpoint 1"
        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        state_file.write_text("{not valid json - checkpoint 2", encoding="utf-8")
        subprocess.run(["git", "add", ".hc/state.json"], cwd=self.test_repo, check=True)
        subprocess.run(["git", "commit", "-m", "corrupted checkpoint"], cwd=self.test_repo, check=True)

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        result = load_state_from_repo(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result.get("recovered_from_git"))
        self.assertEqual(fresh_context.state["sprint_goal"], "Good state, checkpoint 1")


class TestStateRepairTools(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #85 - "Offer options to repair or delete
    corrupted state"): get_corrupted_state_raw_content/save_repaired_state
    (LLM-assisted repair), reset_state_from_git (search all of git history,
    not just HEAD), and clear_corrupted_state (delete outright) - each only
    ever acts on a genuinely corrupted state.json, never on one that
    already parses fine.
    """

    def setUp(self):
        self.test_repo = Path("test_repo_state_repair_tools")
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
        self.state_file = self.test_repo / ".hc" / "state.json"

    def tearDown(self):
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        if "STATE_REPO_PATH" in os.environ:
            del os.environ["STATE_REPO_PATH"]
        if self.old_internal_path:
            os.environ["INTERNAL_STATE_REPO_PATH"] = self.old_internal_path

    def _corrupt_state_file(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text("{not valid json", encoding="utf-8")

    # --- get_corrupted_state_raw_content ---

    def test_get_corrupted_state_raw_content_returns_the_raw_text(self):
        self._corrupt_state_file()
        result = get_corrupted_state_raw_content(tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["raw_content"], "{not valid json")

    def test_get_corrupted_state_raw_content_refuses_when_not_corrupted(self):
        save_state_to_repo(tool_context=self.tool_context)
        result = get_corrupted_state_raw_content(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    def test_get_corrupted_state_raw_content_refuses_when_no_file_exists(self):
        result = get_corrupted_state_raw_content(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    # --- save_repaired_state ---

    def test_save_repaired_state_persists_a_valid_repair(self):
        self._corrupt_state_file()
        result = save_repaired_state({"sprint_goal": "Repaired by the LLM"}, tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.tool_context.state["sprint_goal"], "Repaired by the LLM")
        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8"))["sprint_goal"], "Repaired by the LLM")

    def test_save_repaired_state_refuses_when_not_corrupted(self):
        save_state_to_repo(tool_context=self.tool_context)
        result = save_repaired_state({"sprint_goal": "Should not apply"}, tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    def test_save_repaired_state_refuses_a_non_dict(self):
        self._corrupt_state_file()
        result = save_repaired_state("not a dict", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    def test_save_repaired_state_refuses_data_that_does_not_validate(self):
        self._corrupt_state_file()
        # sprint_backlog must be a list of dicts, not a string
        result = save_repaired_state({"sprint_backlog": "not-a-list"}, tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    # --- reset_state_from_git ---

    def test_reset_state_from_git_recovers_the_last_checkpoint(self):
        self.tool_context.state["sprint_goal"] = "Good state, checkpointed"
        save_state_to_repo(tool_context=self.tool_context)
        self._corrupt_state_file()

        fresh_context = MagicMock()
        fresh_context.state = ScrumState().model_dump()
        result = reset_state_from_git(tool_context=fresh_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(fresh_context.state["sprint_goal"], "Good state, checkpointed")
        self.assertFalse(fresh_context.state["state_json_corrupted"])

    def test_reset_state_from_git_refuses_when_not_corrupted(self):
        save_state_to_repo(tool_context=self.tool_context)
        result = reset_state_from_git(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    def test_reset_state_from_git_reports_error_with_no_checkpoint_available(self):
        self._corrupt_state_file()
        result = reset_state_from_git(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")

    # --- clear_corrupted_state ---

    def test_clear_corrupted_state_deletes_the_file(self):
        self._corrupt_state_file()
        result = clear_corrupted_state(tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(self.state_file.exists())
        self.assertFalse(self.tool_context.state["state_json_corrupted"])

    def test_clear_corrupted_state_refuses_when_not_corrupted(self):
        save_state_to_repo(tool_context=self.tool_context)
        result = clear_corrupted_state(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertTrue(self.state_file.exists())

    def test_clear_corrupted_state_refuses_when_no_file_exists(self):
        result = clear_corrupted_state(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
