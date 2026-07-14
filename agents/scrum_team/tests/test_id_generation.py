import unittest
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock
import sys

# Add current dir to path
sys.path.append(os.getcwd())

from agents.scrum_team.tools.requirements import upsert_story, upsert_epic
from agents.scrum_team.tools.docs import upsert_adr
from agents.scrum_team.state import ScrumState

class TestIDGeneration(unittest.TestCase):
    def setUp(self):
        self.test_repo = Path("test_repo_ids")
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        self.test_repo.mkdir(exist_ok=True)
        os.environ["STATE_REPO_PATH"] = str(self.test_repo.absolute())
        
        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()
        self.tool_context.agent_name = "TestAgent"

    def tearDown(self):
        if self.test_repo.exists():
            shutil.rmtree(self.test_repo)
        if "STATE_REPO_PATH" in os.environ:
            del os.environ["STATE_REPO_PATH"]

    def test_upsert_story_id_generation(self):
        # First story
        res = upsert_story({"title": "First Story"}, tool_context=self.tool_context)
        self.assertEqual(res["item"]["id"], "US-0001")
        
        # Second story
        res = upsert_story({"title": "Second Story"}, tool_context=self.tool_context)
        self.assertEqual(res["item"]["id"], "US-0002")
        
        # Story with placeholder
        res = upsert_story({"id": "US-XXXX", "title": "Third Story"}, tool_context=self.tool_context)
        self.assertEqual(res["item"]["id"], "US-0003")

    def test_upsert_epic_id_generation(self):
        # First epic
        res = upsert_epic({"title": "First Epic"}, tool_context=self.tool_context)
        self.assertEqual(res["item"]["id"], "EP-0001")
        
        # Second epic
        res = upsert_epic({"title": "Second Epic"}, tool_context=self.tool_context)
        self.assertEqual(res["item"]["id"], "EP-0002")

    def test_upsert_adr_id_generation(self):
        # Ensure architecture dir exists
        arch_dir = self.test_repo / "specs" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        
        # First ADR
        res = upsert_adr(
            title="First Decision",
            context="The problem",
            decision="The fix",
            consequences="The result",
            tool_context=self.tool_context
        )
        self.assertEqual(res["status"], "ok")
        self.assertIn("ADR-0001", res["path"])
        
        # Second ADR
        res = upsert_adr(
            title="Second Decision",
            context="Another problem",
            decision="Another fix",
            consequences="Another result",
            tool_context=self.tool_context
        )
        self.assertEqual(res["status"], "ok")
        self.assertIn("ADR-0002", res["path"])

if __name__ == "__main__":
    unittest.main()
