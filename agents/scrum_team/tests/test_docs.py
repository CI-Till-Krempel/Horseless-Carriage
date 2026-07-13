# agents/scrum_team/tests/test_docs.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.docs import (
    read_doc,
    upsert_prd,
    upsert_srs,
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


if __name__ == "__main__":
    unittest.main()