# agents/scrum_team/tests/test_docs.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.docs import (
    read_doc,
    upsert_prd,
    upsert_srs,
)
from ..state import ScrumState


class TestDocsTools(unittest.TestCase):
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data="This is a test document.")
    def test_read_doc(self, mock_open):
        """
        Acceptance Criteria:
        - A document is read from the file system.
        """
        state = ScrumState()
        content = read_doc("test.md", state)
        self.assertEqual(content, "This is a test document.")

    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_upsert_prd(self, mock_open):
        """
        Acceptance Criteria:
        - A PRD is created or updated.
        """
        state = ScrumState()
        upsert_prd("This is a PRD.", state)
        mock_open().write.assert_called_with("This is a PRD.")

    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_upsert_srs(self, mock_open):
        """
        Acceptance Criteria:
        - An SRS is created or updated.
        """
        state = ScrumState()
        upsert_srs("This is an SRS.", state)
        mock_open().write.assert_called_with("This is an SRS.")


if __name__ == "__main__":
    unittest.main()