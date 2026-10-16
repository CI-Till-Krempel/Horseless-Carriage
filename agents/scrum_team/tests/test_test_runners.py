# agents/scrum_team/tests/test_test_runners.py
"""
SPEC-ISSUE-0064: tests for the pluggable test-runner registry (Gradle/Maven/
pytest) that replaced quality.py's old hardcoded `pytest` invocation.

Gradle/Maven tests write real JUnit XML / JaCoCo XML fixtures to a tmp_path
and only mock the `_run` subprocess boundary - the XML-glob-and-parse logic
itself runs for real against the stable, standard report formats both
build tools emit, rather than mocking that logic away too.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.scrum_team.tools.test_runners import (
    _GradleRunner,
    _MavenRunner,
    _detect_unsupported_ecosystem,
    new_issue_url,
    run_test_suite,
)

_OK_RUN = {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}


def _write_junit_xml(path: Path, tests: int, failures: int = 0, errors: int = 0, skipped: int = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<testsuite name="x" tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}"></testsuite>'
    )


def _write_jacoco_xml(path: Path, covered: int, missed: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<report name="x">'
        f'<counter type="INSTRUCTION" covered="1" missed="1"/>'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        "</report>"
    )


class TestGradleRunner(unittest.TestCase):
    def test_detect_via_build_gradle_kts(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "build.gradle.kts").write_text("")
            self.assertTrue(_GradleRunner().detect(repo_root))

    def test_detect_false_for_unrelated_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(_GradleRunner().detect(Path(d)))

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_uses_wrapper_when_present(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            wrapper = repo_root / "gradlew"
            wrapper.write_text("#!/bin/sh\n")
            wrapper.chmod(0o755)
            _write_junit_xml(repo_root / "build/test-results/test/TEST-Foo.xml", tests=1)
            _GradleRunner().run(repo_root)
        self.assertEqual(mock_run.call_args.args[0][0], str(wrapper))

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_falls_back_to_system_gradle_without_wrapper(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            _write_junit_xml(repo_root / "build/test-results/test/TEST-Foo.xml", tests=1)
            result = _GradleRunner().run(repo_root)
        self.assertEqual(mock_run.call_args.args[0][0], "gradle")
        self.assertIn("no ./gradlew wrapper", result["tool"])

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_aggregates_junit_xml_across_modules(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            _write_junit_xml(repo_root / "module-a/build/test-results/test/TEST-A.xml", tests=5, failures=1)
            _write_junit_xml(repo_root / "module-b/build/test-results/test/TEST-B.xml", tests=3, errors=1, skipped=1)
            result = _GradleRunner().run(repo_root)

        self.assertTrue(result["tests_available"])
        # 5 + (3 - 1 skipped) = 7 run; 1 failure + 1 error = 2 failed
        self.assertEqual(result["tests_run"], 7)
        self.assertEqual(result["tests_failed"], 2)

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_coverage_unavailable_without_jacoco_report(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            _write_junit_xml(repo_root / "build/test-results/test/TEST-A.xml", tests=2)
            result = _GradleRunner().run(repo_root)

        self.assertTrue(result["tests_available"])
        self.assertFalse(result["coverage_available"])
        self.assertIsNone(result["test_coverage"])
        self.assertIn("jacoco", result["note"].lower())

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_coverage_available_with_jacoco_report(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            _write_junit_xml(repo_root / "build/test-results/test/TEST-A.xml", tests=4)
            _write_jacoco_xml(repo_root / "build/reports/jacoco/test/jacocoTestReport.xml", covered=90, missed=10)
            result = _GradleRunner().run(repo_root)

        self.assertTrue(result["coverage_available"])
        self.assertEqual(result["test_coverage"], 0.9)

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_no_junit_reports_reports_tests_unavailable(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            result = _GradleRunner().run(Path(d))
        self.assertFalse(result["tests_available"])
        self.assertIn("no JUnit XML", result["note"])


class TestMavenRunner(unittest.TestCase):
    def test_detect_via_pom_xml(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "pom.xml").write_text("")
            self.assertTrue(_MavenRunner().detect(repo_root))

    @patch("agents.scrum_team.tools.test_runners._run")
    def test_surefire_reports_parsed(self, mock_run):
        mock_run.return_value = _OK_RUN
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            _write_junit_xml(repo_root / "target/surefire-reports/TEST-A.xml", tests=10, failures=2)
            result = _MavenRunner().run(repo_root)
        self.assertTrue(result["tests_available"])
        self.assertEqual(result["tests_run"], 10)
        self.assertEqual(result["tests_failed"], 2)
        self.assertFalse(result["coverage_available"])


class TestUnsupportedEcosystemDetection(unittest.TestCase):
    def test_go_mod_detected_and_not_silently_run_as_pytest(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "go.mod").write_text("module example.com/foo\n")
            self.assertEqual(_detect_unsupported_ecosystem(repo_root), "Go")

            result = run_test_suite(repo_root)
        self.assertFalse(result["tests_available"])
        self.assertIsNone(result["tool"])
        self.assertIn("Go project detected", result["note"])
        self.assertIn("github.com/CI-Till-Krempel/Horseless-Carriage/issues/new", result["note"])

    def test_rust_cargo_toml_detected(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "Cargo.toml").write_text("[package]\n")
            self.assertEqual(_detect_unsupported_ecosystem(repo_root), "Rust")

    def test_package_json_alone_is_not_treated_as_unsupported(self):
        # Deliberately NOT in the unsupported list - too common alongside an
        # unrelated primary Python test suite (frontend tooling, lint
        # configs) to treat its mere presence as "this repo needs its own
        # adapter" the way go.mod/Cargo.toml/pom.xml safely can.
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "package.json").write_text("{}")
            self.assertIsNone(_detect_unsupported_ecosystem(repo_root))

    def test_new_issue_url_is_well_formed(self):
        url = new_issue_url("Go", Path("/some/repo"))
        self.assertTrue(url.startswith("https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new?"))
        self.assertIn("title=", url)
        self.assertIn("body=", url)


class TestRegistryFallback(unittest.TestCase):
    @patch("agents.scrum_team.tools.test_runners._run")
    def test_falls_through_to_pytest_when_nothing_else_matches(self, mock_run):
        mock_run.return_value = {"status": "ok", "returncode": 0, "stdout": "1 passed in 0.1s", "stderr": ""}
        with tempfile.TemporaryDirectory() as d:
            result = run_test_suite(Path(d))
        self.assertEqual(result["tool"], "pytest")
        self.assertTrue(result["tests_available"])
