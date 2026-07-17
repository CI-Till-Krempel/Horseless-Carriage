# agents/scrum_team/tests/test_quality.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.quality import (
    calculate_kpis,
    update_sprint_report,
    _execute_test_suite_coverage,
)
from agents.scrum_team.state import ScrumState


class TestQualityTools(unittest.TestCase):
    @patch("agents.scrum_team.tools.quality._run")
    def test_calculate_kpis(self, mock_run):
        """
        Acceptance Criteria:
        - KPIs are calculated and returned as a dictionary.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": "TOTAL 100 10 90%\n5 passed in 1.23s",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        kpis = calculate_kpis(tool_context=tool_context)
        self.assertIsInstance(kpis, dict)
        self.assertIn("team_effectiveness", kpis)
        self.assertIn("result_quality", kpis)
        self.assertIn("maintainability", kpis)
        self.assertIn("security", kpis)
        self.assertEqual(kpis["maintainability"]["test_coverage"], 0.9)
        self.assertEqual(kpis["maintainability"]["tests_run"], 5)
        self.assertEqual(kpis["maintainability"]["tests_failed"], 0)

    @patch("agents.scrum_team.tools.quality._run")
    def test_execute_test_suite_coverage_parses_real_output(self, mock_run):
        """
        Acceptance Criteria (US-0005):
        - pytest --cov is executed and its real coverage percentage parsed.
        - Failure counts are surfaced alongside coverage, not swallowed.
        """
        mock_run.return_value = {
            "status": "error",  # pytest exits non-zero when tests fail
            "returncode": 1,
            "stdout": (
                "Name       Stmts   Miss  Cover\n"
                "--------------------------------\n"
                "mod.py        20      4    80%\n"
                "--------------------------------\n"
                "TOTAL         20      4    80%\n"
                "3 passed, 2 failed in 0.42s"
            ),
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _execute_test_suite_coverage(tool_context=tool_context)

        self.assertTrue(result["available"])
        self.assertEqual(result["test_coverage"], 0.8)
        self.assertEqual(result["tests_run"], 5)
        self.assertEqual(result["tests_failed"], 2)

    @patch("agents.scrum_team.tools.quality._run")
    def test_execute_test_suite_coverage_zero_tests(self, mock_run):
        """
        Acceptance Criteria (US-0005 edge case):
        - Zero tests reports 0%/no-tests explicitly, not a stale/default number.
        """
        mock_run.return_value = {
            "status": "error",
            "returncode": 5,
            "stdout": "no tests ran in 0.01s",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _execute_test_suite_coverage(tool_context=tool_context)

        self.assertTrue(result["available"])
        self.assertEqual(result["test_coverage"], 0.0)
        self.assertEqual(result["tests_run"], 0)
        self.assertEqual(result["note"], "no tests collected")

    @patch("agents.scrum_team.tools.quality._run")
    def test_execute_test_suite_coverage_tool_unavailable(self, mock_run):
        """
        pytest itself couldn't be executed (e.g. not installed) - must
        report unavailable rather than crash or fabricate a number.
        """
        mock_run.return_value = {
            "status": "error",
            "message": "[Errno 2] No such file or directory: 'pytest'",
            "cmd": ["pytest", "--cov"],
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _execute_test_suite_coverage(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["test_coverage"])
        self.assertIn("could not be executed", result["note"])

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