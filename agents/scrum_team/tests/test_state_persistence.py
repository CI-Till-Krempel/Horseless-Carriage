# agents/scrum_team/tests/test_state_persistence.py
import unittest
import os
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from agents.scrum_team.tools.scrum import (
    save_state_to_repo,
    load_state_from_repo,
    REPO_STATE_KEYS,
)
from agents.scrum_team.state import ScrumState


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

    def test_save_state_to_repo_excludes_keys_outside_allowlist(self):
        # Confirm REPO_STATE_KEYS is a deliberate allowlist, not a full dump —
        # e.g. github_token (sensitive) must never be written to the state repo.
        self.tool_context.state["github_token"] = "should-not-be-persisted"

        save_state_to_repo(tool_context=self.tool_context)

        state_file = self.test_repo / ".hc" / "state.json"
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertNotIn("github_token", saved)


if __name__ == "__main__":
    unittest.main()
