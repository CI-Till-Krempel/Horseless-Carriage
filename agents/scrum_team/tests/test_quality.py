# agents/scrum_team/tests/test_quality.py
import json
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.quality import (
    calculate_kpis,
    update_sprint_report,
    check_build,
    _execute_test_suite_coverage,
    _compute_code_complexity,
    _scan_security_vulnerabilities,
)
from agents.scrum_team.tools.budget import create_sprint_report
from agents.scrum_team.state import ScrumState

_TOOL_NOT_INSTALLED = {
    "status": "error",
    "message": "[Errno 2] No such file or directory",
}


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

    @patch("agents.scrum_team.tools.quality._run")
    def test_calculate_kpis_all_tools_unavailable(self, mock_run):
        """
        Acceptance Criteria (US-0008):
        - With pytest, radon, and bandit all unavailable, calculate_kpis()
          flags each metric as unavailable independently rather than
          substituting a default/dummy value, and does not raise.
        """
        mock_run.side_effect = [_TOOL_NOT_INSTALLED, _TOOL_NOT_INSTALLED, _TOOL_NOT_INSTALLED]
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            kpis = calculate_kpis(tool_context=tool_context)

        maintainability = kpis["maintainability"]
        self.assertFalse(maintainability["test_coverage_available"])
        self.assertIsNone(maintainability["test_coverage"])
        self.assertFalse(maintainability["code_complexity_available"])
        self.assertIsNone(maintainability["code_complexity"])
        self.assertFalse(kpis["security"]["vulnerability_scan_available"])
        self.assertIsNone(kpis["security"]["vulnerability_scan_results"])
        # Each unavailable metric carries its own distinct explanation.
        self.assertIn("pytest", maintainability["test_coverage_note"])
        self.assertIn("radon", maintainability["code_complexity_note"])
        self.assertIn("bandit", kpis["security"]["vulnerability_scan_note"])

    @patch("agents.scrum_team.tools.quality._run")
    def test_calculate_kpis_partial_tooling_flags_independently(self, mock_run):
        """
        Acceptance Criteria (US-0008):
        - With pytest present but radon/bandit unavailable, the available
          metric is reported normally and the unavailable ones are flagged
          independently rather than dragging the whole result down.
        """
        mock_run.side_effect = [
            {
                "status": "ok",
                "returncode": 0,
                "stdout": "TOTAL 100 10 90%\n5 passed in 1.23s",
                "stderr": "",
            },
            _TOOL_NOT_INSTALLED,
            _TOOL_NOT_INSTALLED,
        ]
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            kpis = calculate_kpis(tool_context=tool_context)

        maintainability = kpis["maintainability"]
        self.assertTrue(maintainability["test_coverage_available"])
        self.assertEqual(maintainability["test_coverage"], 0.9)
        self.assertNotIn("test_coverage_note", maintainability)
        self.assertFalse(maintainability["code_complexity_available"])
        self.assertIsNone(maintainability["code_complexity"])
        self.assertFalse(kpis["security"]["vulnerability_scan_available"])
        self.assertIsNone(kpis["security"]["vulnerability_scan_results"])

    @patch("agents.scrum_team.tools.docs.write_file")
    @patch("agents.scrum_team.tools.quality._run")
    def test_sprint_report_generation_survives_total_tooling_failure(self, mock_run, mock_write_file):
        """
        Acceptance Criteria (US-0008 edge case):
        - Total tooling failure degrades gracefully: calculate_kpis() ->
          update_sprint_report() -> create_sprint_report() completes
          without raising, even though every KPI tool is unavailable.
        """
        mock_run.side_effect = [_TOOL_NOT_INSTALLED, _TOOL_NOT_INSTALLED, _TOOL_NOT_INSTALLED]
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]

        with patch("agents.scrum_team.tools.quality._detect_primary_language", return_value="python"):
            kpis = calculate_kpis(tool_context=tool_context)
        update_sprint_report(kpis=kpis, tool_context=tool_context)
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(report["status"], "ok")

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

    def test_update_sprint_report_bumps_kpi_update_count(self):
        """
        Acceptance Criteria (ISSUE-0046): create_sprint_report's kpi_baseline
        gate (tools/budget.py) needs a real signal that QualityGuardian
        actually ran this sprint, mirroring retro_baseline's
        len(retro_actions)+len(impediment_log) pattern - across every real
        eval run before this existed, nothing ever incremented, so nothing
        ever forced the hand-off, so the KPI trends came back "never
        computed" every single time.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        self.assertEqual(tool_context.state["kpi_update_count"], 0)
        update_sprint_report(kpis={"team_effectiveness": {"say_do_ratio": 0.8}}, tool_context=tool_context)
        self.assertEqual(tool_context.state["kpi_update_count"], 1)
        update_sprint_report(kpis={"team_effectiveness": {"say_do_ratio": 0.9}}, tool_context=tool_context)
        self.assertEqual(tool_context.state["kpi_update_count"], 2)

    def test_update_sprint_report_rejects_non_dict_kpis(self):
        """
        Acceptance Criteria: a genuinely unrecoverable kpis shape (not the
        "calculate_kpis" tool-name alias, not a JSON or Python-repr
        stringified dict) must still be rejected before it ever reaches
        state - a bad sprint_report_kpis shape there crashes the *next*
        turn's before_model_callback (get_scrum_state re-validates the
        whole state via ScrumState(**data)) with a pydantic ValidationError
        nowhere near this tool.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = update_sprint_report(kpis="not a dict at all", tool_context=tool_context)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tool_context.state["sprint_report_kpis"], {})

    @patch("agents.scrum_team.tools.quality.calculate_kpis")
    def test_update_sprint_report_self_heals_calculate_kpis_tool_name_string(self, mock_calculate_kpis):
        """
        Acceptance Criteria: a real eval run had QualityGuardian call this
        with kpis="calculate_kpis" (and, in other turns, "calculate_kpis()")
        - the other tool's name, as a plain string - apparently expecting
        update_sprint_report to call it, then retried the same wrong shape
        over a dozen times in a row before burning through the eval's whole
        LLM-call budget. Recognize that shape and actually call
        calculate_kpis() here instead of erroring, so the call succeeds and
        the loop never starts.
        """
        real_kpis = {"team_effectiveness": {"say_do_ratio": 0.8, "commitment_reliability": 1.0}}
        mock_calculate_kpis.return_value = real_kpis
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        for alias in ("calculate_kpis", "calculate_kpis()", "  calculate_kpis  "):
            with self.subTest(alias=alias):
                tool_context.state["sprint_report_kpis"] = {}
                result = update_sprint_report(kpis=alias, tool_context=tool_context)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["kpis"], real_kpis)
                self.assertEqual(tool_context.state["sprint_report_kpis"], real_kpis)

    def test_update_sprint_report_recovers_json_stringified_kpis(self):
        """
        Acceptance Criteria: a JSON-encoded string (double-quoted) is
        transparently parsed back into the real dict, same recovery
        upsert_story/upsert_issue already get via _coerce_dict_arg.
        """
        real_kpis = {"team_effectiveness": {"say_do_ratio": 0.8, "commitment_reliability": 1.0}}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = update_sprint_report(kpis=json.dumps(real_kpis), tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["sprint_report_kpis"], real_kpis)

    def test_update_sprint_report_recovers_python_repr_stringified_kpis(self):
        """
        Acceptance Criteria: a real eval run had QualityGuardian call this
        with kpis=str(some_dict) - Python repr, single-quoted, with a bare
        None - which json.loads can't parse at all. ast.literal_eval (via
        _coerce_dict_arg) recovers it instead of erroring.
        """
        real_kpis = {
            "team_effectiveness": {"say_do_ratio": 0.8, "commitment_reliability": 1.0},
            "maintainability": {"code_complexity": None, "test_coverage": 0.0},
        }
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = update_sprint_report(kpis=str(real_kpis), tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["sprint_report_kpis"], real_kpis)

    @patch("agents.scrum_team.tools.quality._configured_repo_root")
    @patch("agents.scrum_team.tools.quality._run")
    def test_check_build_persists_result_for_the_tested_gate(self, mock_run, mock_repo_root):
        """
        Acceptance Criteria (ISSUE-0004): check_build's result is persisted
        into state so advance_story_stage's Tested gate can verify it
        actually ran and passed, instead of trusting QA's own say-so.
        """
        from pathlib import Path
        import tempfile

        mock_run.return_value = {"status": "ok", "stdout": "", "stderr": ""}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "requirements.txt").write_text("pytest\n")
            mock_repo_root.return_value = Path(tmp_dir)
            check_build(tool_context=tool_context)

        self.assertEqual(tool_context.state["last_check_build"], {"checked": "requirements.txt", "passing": True})

    @patch("agents.scrum_team.tools.quality._configured_repo_root")
    def test_check_build_persists_not_checked_result(self, mock_repo_root):
        from pathlib import Path
        import tempfile

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_repo_root.return_value = Path(tmp_dir)
            check_build(tool_context=tool_context)

        self.assertEqual(tool_context.state["last_check_build"], {"checked": None, "passing": None})


if __name__ == "__main__":
    unittest.main()