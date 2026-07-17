# agents/scrum_team/tools/quality.py
import re
from ..state import ScrumState
from typing import Dict, Any
from .base import _configured_repo_root, _run

_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", re.MULTILINE)
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_ERROR_RE = re.compile(r"(\d+)\s+error")
_NO_TESTS_RE = re.compile(r"no tests ran")


def _execute_test_suite_coverage(tool_context=None) -> Dict[str, Any]:
    """
    Runs pytest with coverage against the configured target repo and parses
    the real coverage percentage and pass/fail counts from its output,
    rather than fabricating a number.
    """
    repo_root = _configured_repo_root(tool_context)
    result = _run(
        ["pytest", "--cov", "--cov-report=term", "-q", "--no-header"],
        cwd=str(repo_root),
        tool_context=tool_context,
    )

    if result.get("status") == "error" and "returncode" not in result:
        # The subprocess itself couldn't be started at all (e.g. pytest not
        # installed in the target repo) - distinct from pytest running and
        # exiting non-zero because tests failed (that path still has stdout
        # to parse below).
        return {
            "available": False,
            "test_coverage": None,
            "tests_run": 0,
            "tests_failed": 0,
            "note": f"pytest could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""

    if _NO_TESTS_RE.search(stdout):
        return {
            "available": True,
            "test_coverage": 0.0,
            "tests_run": 0,
            "tests_failed": 0,
            "note": "no tests collected",
        }

    coverage_match = _COVERAGE_TOTAL_RE.search(stdout)
    test_coverage = int(coverage_match.group(1)) / 100.0 if coverage_match else None

    passed = int(m.group(1)) if (m := _PASSED_RE.search(stdout)) else 0
    failed = int(m.group(1)) if (m := _FAILED_RE.search(stdout)) else 0
    errored = int(m.group(1)) if (m := _ERROR_RE.search(stdout)) else 0

    return {
        "available": test_coverage is not None,
        "test_coverage": test_coverage,
        "tests_run": passed + failed + errored,
        "tests_failed": failed + errored,
        "note": None if test_coverage is not None else "coverage summary not found in pytest output",
    }


def calculate_kpis(tool_context=None) -> Dict[str, Any]:
    """
    Calculates and returns a dictionary of quality KPIs.

    test_coverage/tests_run/tests_failed are derived from actually executing
    the target repo's test suite (US-0005). code_complexity and
    vulnerability_scan_results are still placeholders pending US-0006/US-0007.
    """
    coverage_result = _execute_test_suite_coverage(tool_context)

    maintainability = {
        "code_complexity": 10,
        "test_coverage": coverage_result["test_coverage"],
        "test_coverage_available": coverage_result["available"],
        "tests_run": coverage_result["tests_run"],
        "tests_failed": coverage_result["tests_failed"],
    }
    if coverage_result["note"]:
        maintainability["test_coverage_note"] = coverage_result["note"]

    return {
        "team_effectiveness": {
            "say_do_ratio": 0.8,
            "commitment_reliability": 1.0,
        },
        "result_quality": {
            "defect_escape_rate": 0.05,
            "customer_satisfaction": 4.5,
        },
        "maintainability": maintainability,
        "security": {
            "vulnerability_scan_results": {
                "critical": 0,
                "high": 1,
                "medium": 3,
                "low": 5,
            }
        },
    }

def update_sprint_report(kpis: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Adds the KPI dashboard to the sprint report.
    """
    # In a real implementation, this would format the KPIs into a nice
    # dashboard and append it to the sprint report.
    # For now, we'll just store the KPIs in the state.
    if tool_context and hasattr(tool_context, "state"):
        tool_context.state["sprint_report_kpis"] = kpis
    return {"status": "ok", "kpis": kpis}
