# agents/scrum_team/tools/quality.py
import json
import os
import re
from ..state import ScrumState
from typing import Dict, Any
from .base import _configured_repo_root, _run

_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", re.MULTILINE)
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_ERROR_RE = re.compile(r"(\d+)\s+error")
_NO_TESTS_RE = re.compile(r"no tests ran")
_AVG_COMPLEXITY_RE = re.compile(r"Average complexity:\s+[A-F]\s+\(([\d.]+)\)")
_LANGUAGE_SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__"}


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
    stderr = result.get("stderr", "") or ""

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

    note = None
    if test_coverage is None:
        # A real eval run hit this repeatedly - QA and DevTeam bounced a
        # story back and forth 9 times, always rejected by the exact same
        # opaque "coverage summary not found" note, with no way to tell
        # whether pytest crashed outright, a dependency was missing, or
        # something else entirely - so every retry guessed blindly at
        # unrelated tooling changes (CI config, requirements.txt, moving
        # test files) instead of whatever the real cause actually was.
        # Surface enough of pytest's own output for that to be diagnosable
        # instead of a black box.
        tail = (stdout or stderr)[-800:].strip()
        note = (
            f"coverage summary not found in pytest output (exit code {result.get('returncode')})"
            + (f" - last output:\n{tail}" if tail else " - no output was captured at all.")
        )

    return {
        "available": test_coverage is not None,
        "test_coverage": test_coverage,
        "tests_run": passed + failed + errored,
        "tests_failed": failed + errored,
        "note": note,
    }


def _detect_primary_language(repo_root) -> str:
    """
    Best-effort detection of the target repo's primary language, used to
    pick an appropriate static analysis tool. Only Python (via radon) is
    supported today; anything else falls back to "unknown" so callers can
    report "not available" instead of fabricating a number.
    """
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _LANGUAGE_SKIP_DIRS and not d.startswith(".")]
        if any(f.endswith(".py") for f in filenames):
            return "python"
    return "unknown"


def _compute_code_complexity(tool_context=None) -> Dict[str, Any]:
    """
    Computes the average cyclomatic complexity of the target repo via radon
    (Python only for now), rather than a fixed constant.
    """
    repo_root = _configured_repo_root(tool_context)
    language = _detect_primary_language(repo_root)

    if language != "python":
        return {
            "available": False,
            "code_complexity": None,
            "note": f"static analysis not available for language: {language}",
        }

    result = _run(
        ["radon", "cc", str(repo_root), "--total-average"],
        cwd=str(repo_root),
        tool_context=tool_context,
    )

    if result.get("status") == "error" and "returncode" not in result:
        # radon itself couldn't be started (e.g. not installed).
        return {
            "available": False,
            "code_complexity": None,
            "note": f"radon could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""
    match = _AVG_COMPLEXITY_RE.search(stdout)
    if not match:
        return {
            "available": False,
            "code_complexity": None,
            "note": "no average complexity reported by radon",
        }

    return {
        "available": True,
        "code_complexity": float(match.group(1)),
        "note": None,
    }


def _scan_security_vulnerabilities(tool_context=None) -> Dict[str, Any]:
    """
    Scans the target repo for security issues via bandit (Python only for
    now) and reports its real severity counts, rather than fabricating
    findings.
    """
    repo_root = _configured_repo_root(tool_context)
    language = _detect_primary_language(repo_root)

    if language != "python":
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": f"security scan not available for language: {language}",
        }

    result = _run(
        ["bandit", "-r", str(repo_root), "-f", "json", "-q"],
        cwd=str(repo_root),
        tool_context=tool_context,
    )

    if result.get("status") == "error" and "returncode" not in result:
        # bandit itself couldn't be started (e.g. not installed). Note this
        # is distinct from bandit running and exiting non-zero because it
        # found issues - that path still has JSON on stdout to parse below.
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": f"bandit could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""
    try:
        report = json.loads(stdout) if stdout else None
    except ValueError:
        report = None

    if not isinstance(report, dict):
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": "could not parse bandit output",
        }

    totals = report.get("metrics", {}).get("_totals", {})

    return {
        "available": True,
        "vulnerability_scan_results": {
            # bandit has no CRITICAL severity tier - real absence, not a
            # fabricated default.
            "critical": 0,
            "high": int(totals.get("SEVERITY.HIGH", 0)),
            "medium": int(totals.get("SEVERITY.MEDIUM", 0)),
            "low": int(totals.get("SEVERITY.LOW", 0)),
        },
        "note": None,
    }


def check_build(tool_context=None) -> Dict[str, Any]:
    """
    Attempts to actually install the project's declared dependencies -
    the mechanical Definition-of-Done check (see spec-templates/DOD.md)
    for "the build runs", which QA must run for every story before it's
    accepted as Done. Catches the exact class of failure a real eval run
    hit (requirements.txt pinning SQLAlchemy==3.1.1, a version that
    doesn't exist - the app would never even install, let alone run) that
    code review alone missed.

    Supports Python (requirements.txt) and Node (package.json) projects
    today; anything else is reported as "not checked" rather than a false
    pass or a hard block on stacks this can't verify.
    """
    repo_root = _configured_repo_root(tool_context)

    if (repo_root / "requirements.txt").exists():
        checked = "requirements.txt"
        cmd = ["pip", "install", "--dry-run", "-r", "requirements.txt"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context)
        if result.get("status") == "error" and "--dry-run" in (result.get("stderr") or ""):
            # Older pip without --dry-run support - fall back to a real
            # install rather than reporting a false failure. Safe here:
            # this runs inside the agent's own disposable container, not a
            # developer's host machine.
            cmd = ["pip", "install", "-r", "requirements.txt"]
            result = _run(cmd, cwd=str(repo_root), tool_context=tool_context)
    elif (repo_root / "package.json").exists():
        checked = "package.json"
        cmd = ["npm", "install", "--dry-run"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context)
    else:
        result = {
            "status": "ok",
            "checked": None,
            "passing": None,
            "message": "No requirements.txt or package.json found - no recognized dependency manifest to check.",
        }
        if tool_context and getattr(tool_context, "state", None):
            tool_context.state["last_check_build"] = {"checked": None, "passing": None}
        return result

    passing = result.get("status") == "ok"
    # Persisted so advance_story_stage's "Tested" gate (ISSUE-0004) can
    # verify check_build actually ran and passed, instead of trusting QA's
    # own say-so that it did.
    if tool_context and getattr(tool_context, "state", None):
        tool_context.state["last_check_build"] = {"checked": checked, "passing": passing}
    return {
        "status": "ok" if passing else "error",
        "checked": checked,
        "command": " ".join(cmd),
        "passing": passing,
        "output": ((result.get("stdout") or "") + (result.get("stderr") or ""))[-4000:],
    }


def calculate_kpis(tool_context=None) -> Dict[str, Any]:
    """
    Calculates and returns a dictionary of quality KPIs.

    test_coverage/tests_run/tests_failed are derived from actually executing
    the target repo's test suite (US-0005). code_complexity is derived from
    a real static analysis tool (US-0006). vulnerability_scan_results is
    derived from a real security scan (US-0007).
    """
    coverage_result = _execute_test_suite_coverage(tool_context)
    complexity_result = _compute_code_complexity(tool_context)
    security_result = _scan_security_vulnerabilities(tool_context)

    maintainability = {
        "code_complexity": complexity_result["code_complexity"],
        "code_complexity_available": complexity_result["available"],
        "test_coverage": coverage_result["test_coverage"],
        "test_coverage_available": coverage_result["available"],
        "tests_run": coverage_result["tests_run"],
        "tests_failed": coverage_result["tests_failed"],
    }
    if complexity_result["note"]:
        maintainability["code_complexity_note"] = complexity_result["note"]
    if coverage_result["note"]:
        maintainability["test_coverage_note"] = coverage_result["note"]

    security = {
        "vulnerability_scan_available": security_result["available"],
        "vulnerability_scan_results": security_result["vulnerability_scan_results"],
    }
    if security_result["note"]:
        security["vulnerability_scan_note"] = security_result["note"]

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
        "security": security,
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
