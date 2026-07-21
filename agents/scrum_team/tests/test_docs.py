# agents/scrum_team/tests/test_docs.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.docs import (
    read_doc,
    write_file,
    upsert_prd,
    upsert_srs,
    upsert_adr,
    create_from_template,
)
from agents.scrum_team.state import ScrumState


class TestDocsTools(unittest.TestCase):
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_read_doc(self, mock_read_text, mock_exists):
        """
        Acceptance Criteria:
        - A document is read from the file system.
        """
        mock_exists.return_value = True
        mock_read_text.return_value = "This is a test document."
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        content = read_doc("spec-templates/test.md", tool_context=tool_context)
        self.assertEqual(content["content"], "This is a test document.")

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_upsert_prd(self, mock_write_file):
        """
        Acceptance Criteria:
        - A PRD is created or updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        upsert_prd("This is a PRD.", "test.md", tool_context=tool_context)
        mock_write_file.assert_called_with("specs/requirements/PRD-test.md", "This is a PRD.", overwrite=True, tool_context=tool_context)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_upsert_srs(self, mock_write_file):
        """
        Acceptance Criteria:
        - An SRS is created or updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        upsert_srs("This is an SRS.", "test.md", tool_context=tool_context)
        mock_write_file.assert_called_with("specs/requirements/SRS-test.md", "This is an SRS.", overwrite=True, tool_context=tool_context)


class TestSprintFilesTouched(unittest.TestCase):
    """
    Acceptance Criteria (US-0009):
    - Every write path in tools/docs.py (upsert_prd, upsert_srs, upsert_adr,
      create_from_template - all of which funnel through write_file)
      records the repo-relative path in ScrumState.sprint_files_touched.
    """

    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp())
        patcher = patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=self.repo_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()
        self.tool_context.agent_name = "Architect"

    def test_write_file_records_touched_path(self):
        write_file("specs/requirements/PRD-test.md", "content", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["sprint_files_touched"], ["specs/requirements/PRD-test.md"])

    def test_write_file_does_not_duplicate_repeated_writes(self):
        write_file("specs/requirements/PRD-test.md", "v1", tool_context=self.tool_context)
        write_file("specs/requirements/PRD-test.md", "v2", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["sprint_files_touched"], ["specs/requirements/PRD-test.md"])

    def test_upsert_prd_records_touched_path(self):
        upsert_prd("This is a PRD.", "test.md", tool_context=self.tool_context)
        self.assertIn("specs/requirements/PRD-test.md", self.tool_context.state["sprint_files_touched"])

    def test_upsert_srs_records_touched_path(self):
        upsert_srs("This is an SRS.", "test.md", tool_context=self.tool_context)
        self.assertIn("specs/requirements/SRS-test.md", self.tool_context.state["sprint_files_touched"])

    def test_upsert_adr_records_touched_path(self):
        result = upsert_adr(
            title="Test Decision",
            context="ctx",
            decision="dec",
            consequences="cons",
            adr_id="ADR-0099",
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("specs/architecture/ADR-0099-Test-Decision.md", self.tool_context.state["sprint_files_touched"])

    def test_create_from_template_records_touched_path(self):
        result = create_from_template(
            template_path="spec-templates/stories/TEMPLATE-USER-STORY.md",
            destination_path="specs/stories/US-TEST.md",
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("specs/stories/US-TEST.md", self.tool_context.state["sprint_files_touched"])

    def test_no_writes_yet_touched_list_is_empty(self):
        """
        Acceptance Criteria (US-0009):
        - A sprint with no writes has sprint_files_touched as an empty
          list, not missing/undefined.
        """
        self.assertEqual(ScrumState().model_dump()["sprint_files_touched"], [])


if __name__ == "__main__":
    unittest.main()