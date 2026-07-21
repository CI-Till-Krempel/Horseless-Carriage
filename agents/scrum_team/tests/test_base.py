# agents/scrum_team/tests/test_base.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.scrum_team.tools.base import _hc_version


class TestHcVersion(unittest.TestCase):
    """
    Acceptance Criteria (release process, see RELEASE.md):
    - The running Horseless Carriage version is read from the committed
      VERSION file at the project root, not fabricated.
    """

    def test_hc_version_reads_real_version_file(self):
        version = _hc_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_hc_version_reports_unknown_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.scrum_team.tools.base._project_root", return_value=Path(tmp_dir)):
                self.assertEqual(_hc_version(), "unknown")


if __name__ == "__main__":
    unittest.main()
