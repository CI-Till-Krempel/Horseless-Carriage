# agents/scrum_team/tests/test_quality.py
import json
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.quality import (
    calculate_kpis,
    update_sprint_report,
    _execute_test_suite_coverage,
    _compute_code_complexity,
    _scan_security_vulnerabilities,
)
from agents.scrum_team.state import ScrumState


class TestQualityTools(unittest.TestCase):
    @patch("agents.scrum_team.tools.quality._run")
    def test_calculate_kpis(self, mock_run):
        """
        Acceptance Criteria:
        - KPIs are calculated and returned as a dictionary.
        """
        # _run is called for pytest coverage, then radon, then bandit.
        mock_run.side_effect = [
            {
                "status": "ok",
                "returncode": 0,
                "stdout": "TOTAL 100 10 90%\n5 passed in 1.23s",
                "stderr": "",
            },
            {
                "status": "ok",
                "returncode": 0,
                "stdout": "Average complexity: A (3.5)",
                "stderr": "",
            },
            {
                "status": "error",  # bandit exits non-zero when it finds issues
                "returncode": 1,
                "stdout": json.dumps({"metrics": {"_totals": {"SEVERITY.HIGH": 1, "SEVERITY.MEDIUM": 3, "SEVERITY.LOW": 5}}}),
                "stderr": "",
            },
        ]
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            kpis = calculate_kpis(tool_context=tool_context)
        self.assertIsInstance(kpis, dict)
        self.assertIn("team_effectiveness", kpis)
        self.assertIn("result_quality", kpis)
        self.assertIn("maintainability", kpis)
        self.assertIn("security", kpis)
        self.assertEqual(kpis["maintainability"]["test_coverage"], 0.9)
        self.assertEqual(kpis["maintainability"]["tests_run"], 5)
        self.assertEqual(kpis["maintainability"]["tests_failed"], 0)
        self.assertEqual(kpis["maintainability"]["code_complexity"], 3.5)
        self.assertTrue(kpis["maintainability"]["code_complexity_available"])
        self.assertTrue(kpis["security"]["vulnerability_scan_available"])
        self.assertEqual(
            kpis["security"]["vulnerability_scan_results"],
            {"critical": 0, "high": 1, "medium": 3, "low": 5},
        )

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

    @patch("agents.scrum_team.tools.quality._run")
    def test_compute_code_complexity_parses_real_output(self, mock_run):
        """
        Acceptance Criteria (US-0006):
        - Complexity is computed via real static analysis (radon) and the
          parsed value flows through unchanged.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": (
                "mod.py\n"
                "    F 1:0 foo - A (2)\n\n"
                "Average complexity: A (4.25)"
            ),
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _compute_code_complexity(tool_context=tool_context)

        self.assertTrue(result["available"])
        self.assertEqual(result["code_complexity"], 4.25)

    def test_compute_code_complexity_unsupported_language(self):
        """
        Acceptance Criteria (US-0006 edge case):
        - An unsupported language/toolchain reports "not available" rather
          than a fabricated number.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="javascript"):
            result = _compute_code_complexity(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["code_complexity"])
        self.assertIn("javascript", result["note"])

    @patch("agents.scrum_team.tools.quality._run")
    def test_compute_code_complexity_tool_unavailable(self, mock_run):
        """
        radon itself couldn't be executed (e.g. not installed) - must
        report unavailable rather than crash or fabricate a number.
        """
        mock_run.return_value = {
            "status": "error",
            "message": "[Errno 2] No such file or directory: 'radon'",
            "cmd": ["radon", "cc"],
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _compute_code_complexity(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["code_complexity"])
        self.assertIn("could not be executed", result["note"])

    @patch("agents.scrum_team.tools.quality._run")
    def test_compute_code_complexity_no_average_reported(self, mock_run):
        """
        radon ran but produced no "Average complexity" line (e.g. no
        functions found) - must report unavailable, not a stale number.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _compute_code_complexity(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["code_complexity"])
        self.assertEqual(result["note"], "no average complexity reported by radon")

    @patch("agents.scrum_team.tools.quality._run")
    def test_scan_security_vulnerabilities_parses_real_output(self, mock_run):
        """
        Acceptance Criteria (US-0007):
        - Vulnerabilities are gathered from an actual scanner (bandit) run,
          and severity counts flow through into the KPI structure unchanged.
        """
        mock_run.return_value = {
            "status": "error",  # bandit exits non-zero when it finds issues
            "returncode": 1,
            "stdout": json.dumps({
                "metrics": {
                    "_totals": {
                        "SEVERITY.HIGH": 2,
                        "SEVERITY.MEDIUM": 1,
                        "SEVERITY.LOW": 4,
                    }
                }
            }),
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _scan_security_vulnerabilities(tool_context=tool_context)

        self.assertTrue(result["available"])
        self.assertEqual(
            result["vulnerability_scan_results"],
            {"critical": 0, "high": 2, "medium": 1, "low": 4},
        )

    def test_scan_security_vulnerabilities_unsupported_language(self):
        """
        Acceptance Criteria (US-0007 edge case):
        - An unsupported language/toolchain reports "scan not performed"
          rather than silently defaulting to zero findings.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="javascript"):
            result = _scan_security_vulnerabilities(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["vulnerability_scan_results"])
        self.assertIn("javascript", result["note"])

    @patch("agents.scrum_team.tools.quality._run")
    def test_scan_security_vulnerabilities_tool_unavailable(self, mock_run):
        """
        bandit itself couldn't be executed (e.g. not installed) - must
        report scan not performed rather than crash or fabricate a result.
        """
        mock_run.return_value = {
            "status": "error",
            "message": "[Errno 2] No such file or directory: 'bandit'",
            "cmd": ["bandit", "-r"],
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _scan_security_vulnerabilities(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["vulnerability_scan_results"])
        self.assertIn("could not be executed", result["note"])

    @patch("agents.scrum_team.tools.quality._run")
    def test_scan_security_vulnerabilities_unparsable_output(self, mock_run):
        """
        bandit ran but produced output that isn't valid JSON - must report
        scan not performed, not zero findings.
        """
        mock_run.return_value = {
            "status": "ok",
            "returncode": 0,
            "stdout": "not json",
            "stderr": "",
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            result = _scan_security_vulnerabilities(tool_context=tool_context)

        self.assertFalse(result["available"])
        self.assertIsNone(result["vulnerability_scan_results"])
        self.assertEqual(result["note"], "could not parse bandit output")

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