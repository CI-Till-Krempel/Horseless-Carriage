# agents/scrum_team/tests/test_quality.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.quality import (
    calculate_kpis,
    update_sprint_report,
)
from agents.scrum_team.state import ScrumState


class TestQualityTools(unittest.TestCase):
    def test_calculate_kpis(self):
        """
        Acceptance Criteria:
        - KPIs are calculated and returned as a dictionary.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        kpis = calculate_kpis()
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
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        kpis = {
            "team_effectiveness": {
                "say_do_ratio": 0.8,
                "commitment_reliability": 1.0,
            }
        }
        update_sprint_report(kpis=kpis, tool_context=tool_context)
        self.assertEqual(tool_context.state["sprint_report_kpis"], kpis)


if __name__ == "__main__":
    unittest.main()