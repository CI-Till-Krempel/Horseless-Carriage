# agents/scrum_team/tests/test_quality.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.quality import (
    calculate_kpis,
    update_sprint_report,
)
from ..state import ScrumState


class TestQualityTools(unittest.TestCase):
    def test_calculate_kpis(self):
        """
        Acceptance Criteria:
        - KPIs are calculated and returned as a dictionary.
        """
        state = ScrumState()
        kpis = calculate_kpis(state)
        self.assertIsInstance(kpis, dict)
        self.assertIn("team_effectiveness", kpis)
        self.assertIn("result_quality", kpis)
        self.assertIn("maintainability", kpis)
        self.assertIn("security", kpis)

    def test_update_sprint_report(self):
        """
        Acceptance Criteria:
        - The sprint report is updated with the KPI dashboard.
        """
        state = ScrumState()
        kpis = {
            "team_effectiveness": {
                "say_do_ratio": 0.8,
                "commitment_reliability": 1.0,
            }
        }
        state = update_sprint_report(kpis, state)
        self.assertEqual(state.sprint_report_kpis, kpis)


if __name__ == "__main__":
    unittest.main()