# agents/scrum_team/tools/test_runners.py
"""
Pluggable test-runner detection and execution for the Tested gate
(advance_story_stage) and calculate_kpis - SPEC-ISSUE-0064.

_execute_test_suite_coverage_impl (quality.py) used to shell out to a
hardcoded `pytest` regardless of the target repo's actual language/build
tool. That's fine for the many Python projects Horseless Carriage manages,
but for a JVM project (Gradle/Maven) it produces either a confusing
collection error (pytest recursing into an unrelated directory, e.g. a
nested horseless-carriage/ submodule checkout) or a false "no tests
collected" / 0% coverage result despite a real, passing native test suite -
QA and DevTeam have no way to tell "the test suite is genuinely empty" apart
from "the harness used the wrong tool entirely" from that output alone.

Each entry below is a self-contained adapter: `detect(repo_root)` decides
whether it applies, `run(repo_root, tool_context)` actually executes the
real test suite and parses its real output - never fabricating a number.
REGISTRY is tried in order, first match wins (the same "pick one primary
ecosystem" simplification _detect_primary_language already makes elsewhere
in quality.py; a repo mixing multiple languages/build tools isn't supported
by any one adapter picking up everything).

Every adapter returns the same shape:
    {"tool": str | None, "tests_available": bool, "tests_run": int,
     "tests_failed": int, "coverage_available": bool,
     "test_coverage": float | None, "note": str | None}

tests_available/tests_run/tests_failed is the load-bearing signal the
Tested gate hard-blocks on (language-agnostic - every supported ecosystem
has a real, no-extra-config way to get a pass/fail count). coverage_* is
informational only, like code_complexity/vulnerability_scan_results
elsewhere in quality.py - some ecosystems need extra project-level
configuration (e.g. JaCoCo for JVM) to measure it at all, and that's never
treated as equivalent to "the test suite itself is broken".
"""
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import _run

_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", re.MULTILINE)
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_ERROR_RE = re.compile(r"(\d+)\s+error")
_NO_TESTS_RE = re.compile(r"no tests ran")

# A cold Gradle/Maven run can download its own build tool distribution plus
# every declared dependency from scratch - the default _run timeout (120s,
# sized for pytest) isn't enough even on a fast connection. Subsequent runs
# are cached (~/.gradle, ~/.m2) and finish in a fraction of this.
_JVM_BUILD_TIMEOUT_SECONDS = 600

# SPEC-ISSUE-0064: markers for ecosystems that have a real, recognizable project
# manifest but no adapter below yet - surfaced as a specific, actionable
# "not supported yet" note (with a pre-filled GitHub issue link) rather than
# silently falling through to pytest and producing a nonsense result for a
# project pytest was never going to find anything in.
_KNOWN_UNSUPPORTED_ECOSYSTEMS = (
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("*.csproj", ".NET"),
    ("*.sln", ".NET"),
)
# Deliberately excludes package.json/Node - too common alongside an
# unrelated primary Python test suite (frontend tooling, lint configs,
# build scripts) to treat its mere presence as "this project is Node and
# needs its own adapter" the way a go.mod/Cargo.toml/pom.xml safely can.
# Node/npm support itself is still deferred (unchanged from pre-SPEC-ISSUE-0064
# behavior: falls through to the pytest adapter either way).
_ISSUE_TRACKER_NEW_ISSUE_URL = "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new"


def _find_upward(repo_root: Path, *names: str) -> bool:
    """True if any of `names` exists directly at repo_root (not recursive -
    these are all files a build tool expects at its project root, same
    assumption check_build already makes for requirements.txt/package.json)."""
    return any((repo_root / name).exists() for name in names)


def _sum_junit_xml_reports(report_dir_glob: Path, pattern: str) -> Optional[Dict[str, int]]:
    """
    Sums tests/failures/errors across every JUnit-format XML report found by
    report_dir_glob.glob(pattern) (recursive - multi-module Gradle/Maven
    projects emit one report set per module). Returns None if no report
    files were found at all (the build may not have run, or ran with no
    test task), {"run": 0, "failed": 0} with no files is never fabricated
    into "0 tests ran" vs. "an unknown number of tests" - the caller
    distinguishes those.

    This format (JUnit XML, <testsuite tests=".." failures=".." errors="..">)
    is emitted by both Gradle's `test` task and Maven Surefire by default,
    no extra plugin required - unlike coverage, which needs JaCoCo.
    """
    files = sorted(report_dir_glob.glob(pattern))
    if not files:
        return None
    total_run = 0
    total_failed = 0
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        for suite in suites:
            tests = int(suite.get("tests", 0))
            failures = int(suite.get("failures", 0))
            errors = int(suite.get("errors", 0))
            skipped = int(suite.get("skipped", 0))
            total_run += tests - skipped
            total_failed += failures + errors
    return {"run": total_run, "failed": total_failed}


def _jacoco_line_coverage(report_glob: Path, pattern: str) -> Optional[float]:
    """
    Parses a JaCoCo XML report's top-level LINE <counter> into a 0..1
    coverage ratio. JaCoCo is a plugin that must be explicitly applied in
    build.gradle(.kts)/pom.xml - not every JVM project has it configured,
    so returning None here (report not found) is the common, expected case,
    not an error.
    """
    files = sorted(report_glob.glob(pattern))
    if not files:
        return None
    try:
        root = ET.parse(files[0]).getroot()
    except ET.ParseError:
        return None
    for counter in root.findall("counter"):
        if counter.get("type") == "LINE":
            covered = int(counter.get("covered", 0))
            missed = int(counter.get("missed", 0))
            total = covered + missed
            return round(covered / total, 4) if total else None
    return None


def _jvm_result(tool: str, run_result: Dict[str, Any], junit: Optional[Dict[str, int]],
                 coverage: Optional[float], coverage_hint: str) -> Dict[str, Any]:
    if run_result.get("status") == "error" and "returncode" not in run_result:
        return {
            "tool": tool, "tests_available": False, "tests_run": 0, "tests_failed": 0,
            "coverage_available": False, "test_coverage": None,
            "note": f"{tool} could not be executed: {run_result.get('message', 'unknown error')}",
        }
    if junit is None:
        tail = ((run_result.get("stdout") or "") + (run_result.get("stderr") or ""))[-800:].strip()
        return {
            "tool": tool, "tests_available": False, "tests_run": 0, "tests_failed": 0,
            "coverage_available": False, "test_coverage": None,
            "note": (
                f"no JUnit XML test reports found after running {tool} "
                f"(exit code {run_result.get('returncode')})"
                + (f" - last output:\n{tail}" if tail else "")
            ),
        }
    return {
        "tool": tool,
        "tests_available": True,
        "tests_run": junit["run"],
        "tests_failed": junit["failed"],
        "coverage_available": coverage is not None,
        "test_coverage": coverage,
        "note": None if coverage is not None else coverage_hint,
    }


class _GradleRunner:
    name = "gradle"

    def detect(self, repo_root: Path) -> bool:
        return _find_upward(
            repo_root, "gradlew", "build.gradle", "build.gradle.kts",
            "settings.gradle", "settings.gradle.kts",
        )

    def run(self, repo_root: Path, tool_context=None) -> Dict[str, Any]:
        wrapper = repo_root / "gradlew"
        cmd = [str(wrapper), "test", "--console=plain"] if wrapper.exists() else ["gradle", "test", "--console=plain"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context, timeout=_JVM_BUILD_TIMEOUT_SECONDS)
        junit = _sum_junit_xml_reports(repo_root, "**/build/test-results/test/*.xml")
        coverage = _jacoco_line_coverage(repo_root, "**/build/reports/jacoco/test/jacocoTestReport.xml")
        return _jvm_result(
            "gradle" if wrapper.exists() else "gradle (no ./gradlew wrapper committed - used system gradle)",
            result, junit, coverage,
            "no JaCoCo report found - apply the jacoco plugin in build.gradle(.kts) and run "
            "the jacocoTestReport task to get coverage.",
        )


class _MavenRunner:
    name = "maven"

    def detect(self, repo_root: Path) -> bool:
        return _find_upward(repo_root, "mvnw", "pom.xml")

    def run(self, repo_root: Path, tool_context=None) -> Dict[str, Any]:
        wrapper = repo_root / "mvnw"
        cmd = [str(wrapper), "test", "-B"] if wrapper.exists() else ["mvn", "test", "-B"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context, timeout=_JVM_BUILD_TIMEOUT_SECONDS)
        junit = _sum_junit_xml_reports(repo_root, "**/target/surefire-reports/*.xml")
        coverage = _jacoco_line_coverage(repo_root, "**/target/site/jacoco/jacoco.xml")
        return _jvm_result(
            "maven" if wrapper.exists() else "maven (no ./mvnw wrapper committed - used system mvn)",
            result, junit, coverage,
            "no JaCoCo report found - add the jacoco-maven-plugin to pom.xml to get coverage.",
        )


class _PytestRunner:
    """
    Today's exact pre-SPEC-ISSUE-0064 logic, unchanged - the default/fallback
    adapter so every currently-working Python project keeps working exactly
    as before. detect() always returns True: this is the last adapter tried
    in REGISTRY, after every more-specific ecosystem has already had its
    turn (see _KNOWN_UNSUPPORTED_ECOSYSTEMS for what runs before falling
    all the way through to this).
    """
    name = "pytest"

    def detect(self, repo_root: Path) -> bool:
        return True

    def run(self, repo_root: Path, tool_context=None) -> Dict[str, Any]:
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        pythonpath = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
        result = _run(
            ["pytest", "--cov", "--cov-report=term", "-q", "--no-header"],
            cwd=str(repo_root),
            tool_context=tool_context,
            env_overrides={"PYTHONPATH": pythonpath},
        )

        if result.get("status") == "error" and "returncode" not in result:
            return {
                "tool": "pytest", "tests_available": False, "tests_run": 0, "tests_failed": 0,
                "coverage_available": False, "test_coverage": None,
                "note": f"pytest could not be executed: {result.get('message', 'unknown error')}",
            }

        stdout = result.get("stdout", "") or ""
        stderr = result.get("stderr", "") or ""

        if _NO_TESTS_RE.search(stdout):
            return {
                "tool": "pytest", "tests_available": True, "tests_run": 0, "tests_failed": 0,
                "coverage_available": True, "test_coverage": 0.0,
                "note": "no tests collected",
            }

        coverage_match = _COVERAGE_TOTAL_RE.search(stdout)
        test_coverage = int(coverage_match.group(1)) / 100.0 if coverage_match else None

        passed = int(m.group(1)) if (m := _PASSED_RE.search(stdout)) else 0
        failed = int(m.group(1)) if (m := _FAILED_RE.search(stdout)) else 0
        errored = int(m.group(1)) if (m := _ERROR_RE.search(stdout)) else 0
        tests_run = passed + failed + errored

        note = None
        if test_coverage is None:
            tail = (stdout or stderr)[-800:].strip()
            note = (
                f"coverage summary not found in pytest output (exit code {result.get('returncode')})"
                + (f" - last output:\n{tail}" if tail else " - no output was captured at all.")
            )

        return {
            "tool": "pytest",
            "tests_available": tests_run > 0,
            "tests_run": tests_run,
            "tests_failed": failed + errored,
            "coverage_available": test_coverage is not None,
            "test_coverage": test_coverage,
            "note": note,
        }


REGISTRY: List[Any] = [_GradleRunner(), _MavenRunner(), _PytestRunner()]


def _detect_unsupported_ecosystem(repo_root: Path) -> Optional[str]:
    """
    SPEC-ISSUE-0064: a repo can have a real, recognizable manifest for a language
    no adapter above supports yet (Go/Rust/Ruby/PHP/.NET/plain Node) - this
    says so explicitly, with a link to request support, rather than letting
    it silently fall through to pytest and get misreported as "no tests
    collected" for a project pytest was never going to find anything in.
    Returns the detected ecosystem's name, or None if nothing recognized.
    """
    for pattern, ecosystem in _KNOWN_UNSUPPORTED_ECOSYSTEMS:
        if "*" in pattern:
            if list(repo_root.glob(pattern)):
                return ecosystem
        elif (repo_root / pattern).exists():
            return ecosystem
    return None


def new_issue_url(ecosystem: str, repo_root: Path) -> str:
    title = f"Add test-runner support for {ecosystem}"
    body = (
        f"Horseless Carriage detected a {ecosystem} project at `{repo_root}` with no "
        "test-runner adapter (see agents/scrum_team/tools/test_runners.py) - the Tested "
        "gate and calculate_kpis can't run or measure this project's real test suite yet."
    )
    from urllib.parse import quote
    return f"{_ISSUE_TRACKER_NEW_ISSUE_URL}?title={quote(title)}&body={quote(body)}"


def run_test_suite(repo_root: Path, tool_context=None) -> Dict[str, Any]:
    """
    Picks the first matching adapter in REGISTRY and runs it. Since
    _PytestRunner.detect() always returns True, this only reaches the
    "truly nothing recognized" case for a repo with zero test infrastructure
    of any kind - see _detect_unsupported_ecosystem for the more useful
    "recognized but not supported yet" case, checked first.
    """
    unsupported = _detect_unsupported_ecosystem(repo_root)
    if unsupported:
        return {
            "tool": None, "tests_available": False, "tests_run": 0, "tests_failed": 0,
            "coverage_available": False, "test_coverage": None,
            "note": (
                f"{unsupported} project detected (via its manifest file) but Horseless Carriage has "
                f"no {unsupported} test-runner adapter yet - request one: {new_issue_url(unsupported, repo_root)}"
            ),
        }
    for runner in REGISTRY:
        if runner.detect(repo_root):
            return runner.run(repo_root, tool_context=tool_context)
    return {
        "tool": None, "tests_available": False, "tests_run": 0, "tests_failed": 0,
        "coverage_available": False, "test_coverage": None,
        "note": "no supported test runner detected for this project layout",
    }
