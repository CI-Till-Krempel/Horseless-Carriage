# agents/scrum_team/tests/test_workbench_templates.py
"""
Tests for US-0021's template-resolution mechanism
(tools/workbench_templates.py): given a Development Workbench template
name, the correct toolchain-script/MCP-config/allowlist-hosts bundle is
loaded from config/development-workbench-templates/<name>/.

Most tests build their own throwaway template directories under a tmp_path
and patch _templates_root to point at them, rather than relying solely on
the real config/development-workbench-templates/fixture-echo/ fixture
shipped in this repo - lets edge cases (missing manifest, missing
mcp-config, multiple templates, invalid names) be exercised without
needing a matching real template directory for each one. A couple of
tests do exercise the real on-disk fixture-echo/ directly, so the actual
packaged file layout is proven to parse end to end at least once.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.scrum_team.tools.workbench_templates import (
    get_workbench_template,
    list_workbench_templates,
    resolve_workbench_template,
    check_workbench_tooling,
    detect_recommended_workbench_template,
    _WORKBENCH_SCRIPT_PLACEHOLDER,
    WORKBENCH_SCRIPT_RELATIVE_PATH,
)


def _write_template(root: Path, name: str, *, script="#!/bin/sh\necho hi\n", manifest=None, mcp_config=None):
    template_dir = root / name
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "workbench-setup.sh").write_text(script, encoding="utf-8")
    if manifest is not None:
        (template_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if mcp_config is not None:
        (template_dir / "mcp-config.json").write_text(json.dumps(mcp_config), encoding="utf-8")
    return template_dir


class TestResolveWorkbenchTemplateAgainstTmpTemplates(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.templates_root = Path(self._tmp.name) / "development-workbench-templates"
        self.templates_root.mkdir(parents=True, exist_ok=True)
        self._patcher = patch(
            "agents.scrum_team.tools.workbench_templates._templates_root",
            return_value=self.templates_root,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_resolves_full_bundle_for_a_known_template(self):
        _write_template(
            self.templates_root, "python-flask",
            script="#!/bin/sh\npip install flask\n",
            manifest={"description": "Flask stack", "additional_allowlist_hosts": ["pypi.org", "files.pythonhosted.org"]},
            mcp_config={"mcpServers": {"flask-docs": {"command": "flask-mcp"}}},
        )
        result = resolve_workbench_template("python-flask")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["name"], "python-flask")
        self.assertEqual(result["script"], "#!/bin/sh\npip install flask\n")
        self.assertEqual(result["description"], "Flask stack")
        self.assertEqual(result["additional_allowlist_hosts"], ["pypi.org", "files.pythonhosted.org"])
        self.assertEqual(result["mcp_config"], {"mcpServers": {"flask-docs": {"command": "flask-mcp"}}})

    def test_manifest_and_mcp_config_are_optional(self):
        _write_template(self.templates_root, "bare-bones")
        result = resolve_workbench_template("bare-bones")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mcp_config"], {})
        self.assertEqual(result["additional_allowlist_hosts"], [])
        self.assertEqual(result["description"], "")

    def test_unreadable_manifest_json_does_not_crash_resolution(self):
        template_dir = _write_template(self.templates_root, "broken-manifest")
        (template_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
        result = resolve_workbench_template("broken-manifest")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["additional_allowlist_hosts"], [])

    def test_unknown_template_name_returns_error_listing_available_names(self):
        _write_template(self.templates_root, "kotlin-multiplatform")
        result = resolve_workbench_template("does-not-exist")
        self.assertEqual(result["status"], "error")
        self.assertIn("Unknown", result["message"])
        self.assertIn("kotlin-multiplatform", result["message"])

    def test_template_directory_missing_required_script_is_an_error(self):
        (self.templates_root / "incomplete").mkdir(parents=True, exist_ok=True)
        result = resolve_workbench_template("incomplete")
        self.assertEqual(result["status"], "error")
        self.assertIn("workbench-setup.sh", result["message"])

    def test_rejects_path_traversal_template_names(self):
        for bad_name in ("../../etc/passwd", "..", "foo/bar", "/etc/passwd", ""):
            result = resolve_workbench_template(bad_name)
            self.assertEqual(result["status"], "error", msg=f"{bad_name!r} should have been refused")

    def test_rejects_uppercase_or_space_containing_names(self):
        _write_template(self.templates_root, "python-flask")
        for bad_name in ("Python-Flask", "python flask", "python_flask"):
            result = resolve_workbench_template(bad_name)
            self.assertEqual(result["status"], "error")

    def test_list_workbench_templates_skips_a_directory_missing_its_script(self):
        _write_template(self.templates_root, "good-one")
        (self.templates_root / "half-written").mkdir(parents=True, exist_ok=True)
        result = list_workbench_templates()
        names = [t["name"] for t in result["templates"]]
        self.assertIn("good-one", names)
        self.assertNotIn("half-written", names)

    def test_list_workbench_templates_is_sorted_and_includes_metadata(self):
        _write_template(self.templates_root, "zeta", manifest={"description": "Z stack", "additional_allowlist_hosts": ["z.example.com"]})
        _write_template(self.templates_root, "alpha", manifest={"description": "A stack"})
        result = list_workbench_templates()
        names = [t["name"] for t in result["templates"]]
        self.assertEqual(names, ["alpha", "zeta"])
        zeta = next(t for t in result["templates"] if t["name"] == "zeta")
        self.assertEqual(zeta["description"], "Z stack")
        self.assertEqual(zeta["additional_allowlist_hosts"], ["z.example.com"])

    def test_list_workbench_templates_empty_when_directory_absent(self):
        empty_root = self.templates_root.parent / "does-not-exist-yet"
        with patch("agents.scrum_team.tools.workbench_templates._templates_root", return_value=empty_root):
            result = list_workbench_templates()
        self.assertEqual(result, {"status": "ok", "templates": []})

    def test_get_workbench_template_tool_matches_resolve_workbench_template(self):
        _write_template(self.templates_root, "react-vite-firebase", script="#!/bin/sh\nnpm install\n")
        via_tool = get_workbench_template("react-vite-firebase", tool_context=None)
        via_resolver = resolve_workbench_template("react-vite-firebase")
        self.assertEqual(via_tool, via_resolver)


class TestRealFixtureEchoTemplateOnDisk(unittest.TestCase):
    """Exercises the real, shipped config/development-workbench-templates/
    fixture-echo/ directory (no monkeypatching) - proves the on-disk
    contract described in workbench_templates.py's own docstring actually
    parses correctly end to end, not just against synthetic tmp fixtures."""

    def test_fixture_echo_resolves_with_its_real_content(self):
        result = resolve_workbench_template("fixture-echo")
        self.assertEqual(result["status"], "ok")
        self.assertIn("fixture-echo workbench", result["script"])
        self.assertIn("fixture.example.test", result["additional_allowlist_hosts"])
        self.assertIn("fixture-tool", result["mcp_config"].get("mcpServers", {}))
        self.assertTrue(result["description"])

    def test_fixture_echo_appears_in_the_real_template_listing(self):
        result = list_workbench_templates()
        names = [t["name"] for t in result["templates"]]
        self.assertIn("fixture-echo", names)


class TestDetectRecommendedWorkbenchTemplate(unittest.TestCase):
    """GH-456: the manifest-sniffing heuristics behind check_workbench_tooling's
    `detected_tech_stack` - one per shipped reference template (US-0022)."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)

    def test_no_recognizable_manifest_returns_none(self):
        self.assertIsNone(detect_recommended_workbench_template(self.repo_root))

    def test_requirements_txt_mentioning_flask_is_python_flask(self):
        (self.repo_root / "requirements.txt").write_text("Flask==3.0.0\npytest\n", encoding="utf-8")
        self.assertEqual(detect_recommended_workbench_template(self.repo_root), "python-flask")

    def test_requirements_txt_without_flask_is_not_python_flask(self):
        (self.repo_root / "requirements.txt").write_text("django==5.0\n", encoding="utf-8")
        self.assertIsNone(detect_recommended_workbench_template(self.repo_root))

    def test_gradle_kts_mentioning_multiplatform_is_kotlin_multiplatform(self):
        (self.repo_root / "build.gradle.kts").write_text(
            'plugins {\n    kotlin("multiplatform")\n}\n', encoding="utf-8",
        )
        self.assertEqual(detect_recommended_workbench_template(self.repo_root), "kotlin-multiplatform")

    def test_commonmain_source_set_layout_is_kotlin_multiplatform_even_without_the_keyword(self):
        (self.repo_root / "shared" / "src" / "commonMain" / "kotlin").mkdir(parents=True)
        self.assertEqual(detect_recommended_workbench_template(self.repo_root), "kotlin-multiplatform")

    def test_plain_gradle_project_without_multiplatform_signal_is_not_kotlin_multiplatform(self):
        (self.repo_root / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
        self.assertIsNone(detect_recommended_workbench_template(self.repo_root))

    def test_package_json_with_vite_and_react_is_react_vite_firebase(self):
        (self.repo_root / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}),
            encoding="utf-8",
        )
        self.assertEqual(detect_recommended_workbench_template(self.repo_root), "react-vite-firebase")

    def test_package_json_with_vite_and_firebase_but_no_react_is_still_react_vite_firebase(self):
        (self.repo_root / "package.json").write_text(
            json.dumps({"dependencies": {"firebase": "^10.0.0"}, "devDependencies": {"vite": "^5.0.0"}}),
            encoding="utf-8",
        )
        self.assertEqual(detect_recommended_workbench_template(self.repo_root), "react-vite-firebase")

    def test_package_json_with_vite_alone_is_not_react_vite_firebase(self):
        """Vite alone (no React, no Firebase) is a real but different stack
        (e.g. vanilla-JS Vite, Vue+Vite) - not this specific template."""
        (self.repo_root / "package.json").write_text(
            json.dumps({"devDependencies": {"vite": "^5.0.0"}}), encoding="utf-8",
        )
        self.assertIsNone(detect_recommended_workbench_template(self.repo_root))


class TestCheckWorkbenchTooling(unittest.TestCase):
    """GH-456: "check regularly (at startup / each sprint) whether the
    tooling (test frameworks) is set up" - check_workbench_tooling is the
    mechanical check itself, wired into init_scrum_state/start_sprint
    (see test_scrum.py) and directly callable by the Architect."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)

        self._repo_patcher = patch(
            "agents.scrum_team.tools.workbench_templates._configured_repo_root",
            return_value=self.repo_root,
        )
        self._repo_patcher.start()
        self.addCleanup(self._repo_patcher.stop)

        # Real shipped templates (python-flask, kotlin-multiplatform,
        # react-vite-firebase, fixture-echo) - exercised against the
        # actual on-disk config/development-workbench-templates/, so a
        # detected name is only ever recommended if it genuinely still
        # exists there (no _templates_root patch in this class).

    def test_unconfigured_workbench_with_recognized_stack_recommends_its_template(self):
        (self.repo_root / "requirements.txt").write_text("Flask==3.0.0\n", encoding="utf-8")
        result = check_workbench_tooling(tool_context=None)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["workbench_configured"])
        self.assertEqual(result["detected_tech_stack"], "python-flask")
        self.assertTrue(result["action_needed"])
        self.assertIn("propose_framework_change", result["recommendation"])
        self.assertIn("template_name='python-flask'", result["recommendation"])

    def test_unconfigured_workbench_with_unrecognized_stack_falls_back_to_custom_script_path(self):
        """An unsupported/unrecognized tech stack must degrade gracefully -
        no crash, and a recommendation that still points at a real path
        forward (the fully-custom workbench_setup_script option from
        US-0021), not a template that doesn't fit."""
        (self.repo_root / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
        result = check_workbench_tooling(tool_context=None)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["workbench_configured"])
        self.assertIsNone(result["detected_tech_stack"])
        self.assertTrue(result["action_needed"])
        self.assertIn("workbench_setup_script", result["recommendation"])
        self.assertNotIn("template_name", result["recommendation"])

    def test_missing_hc_directory_entirely_is_also_unconfigured_not_a_crash(self):
        result = check_workbench_tooling(tool_context=None)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["workbench_configured"])
        self.assertTrue(result["action_needed"])

    def test_already_configured_workbench_is_a_no_op(self):
        hc_dir = self.repo_root / ".hc"
        hc_dir.mkdir(parents=True)
        (hc_dir / "workbench-setup.sh").write_text("#!/bin/sh\napt-get install -y default-jdk\n", encoding="utf-8")
        result = check_workbench_tooling(tool_context=None)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["workbench_configured"])
        self.assertFalse(result["action_needed"])
        self.assertIsNone(result["recommendation"])

    def test_already_configured_workbench_is_a_no_op_even_when_detected_stack_disagrees(self):
        """A real customization is never second-guessed just because a
        cheap manifest sniff would have recommended something else."""
        (self.repo_root / "requirements.txt").write_text("Flask==3.0.0\n", encoding="utf-8")
        hc_dir = self.repo_root / ".hc"
        hc_dir.mkdir(parents=True)
        (hc_dir / "workbench-setup.sh").write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
        result = check_workbench_tooling(tool_context=None)
        self.assertTrue(result["workbench_configured"])
        self.assertFalse(result["action_needed"])

    def test_untouched_placeholder_content_is_treated_as_unconfigured(self):
        hc_dir = self.repo_root / ".hc"
        hc_dir.mkdir(parents=True)
        (hc_dir / "workbench-setup.sh").write_text(_WORKBENCH_SCRIPT_PLACEHOLDER, encoding="utf-8")
        result = check_workbench_tooling(tool_context=None)
        self.assertFalse(result["workbench_configured"])
        self.assertTrue(result["action_needed"])

    def test_stale_detector_match_against_a_removed_template_is_not_recommended(self):
        """detect_recommended_workbench_template's own match is cross-checked
        against what list_workbench_templates() still actually has - a
        template that no longer exists is never recommended, even if its
        detector would otherwise have matched."""
        (self.repo_root / "requirements.txt").write_text("Flask==3.0.0\n", encoding="utf-8")
        with patch(
            "agents.scrum_team.tools.workbench_templates.list_workbench_templates",
            return_value={"status": "ok", "templates": []},
        ):
            result = check_workbench_tooling(tool_context=None)
        self.assertIsNone(result["detected_tech_stack"])
        self.assertIn("workbench_setup_script", result["recommendation"])


class TestWorkbenchPlaceholderMatchesHostSideConstant(unittest.TestCase):
    """GH-456: _WORKBENCH_SCRIPT_PLACEHOLDER is a deliberate duplicate of
    check_state_repo.py's own identically-named constant (that module is
    host-side only, never copied into the agent image - see
    agent.Dockerfile - so it can't just be imported from in-container code).
    This test is what keeps the two from silently drifting apart when run on
    the host (python3 run_tests.py's own "host-script tests" phase, or a
    bare `pytest agents/scrum_team/tests` from a host checkout) - both of
    which have check_state_repo.py sitting right there at the repo root.
    This project's *official* agents/scrum_team/tests run (run_tests.py's
    own "agent test suite" phase) runs this same file a second time, inside
    the agent container, which deliberately never ships check_state_repo.py
    (see agent.Dockerfile/docker-compose.yaml's agent service - only
    ./agents, ./spec-templates, ./eval, and
    ./config/development-workbench-templates are copied/mounted in) - so
    this skips there instead of failing, the same "degrade gracefully
    rather than assume an environment that isn't guaranteed" shape
    check_workbench_tooling itself follows for an unrecognized tech stack."""

    def test_placeholder_constant_matches_check_state_repo_byte_for_byte(self):
        try:
            import check_state_repo
        except ModuleNotFoundError:
            self.skipTest(
                "check_state_repo.py is host-only (never copied into the agent image/container) - "
                "this cross-check only runs where it's actually importable, e.g. the host-script "
                "test phase of python3 run_tests.py."
            )
        self.assertEqual(_WORKBENCH_SCRIPT_PLACEHOLDER, check_state_repo._WORKBENCH_SCRIPT_PLACEHOLDER)

    def test_relative_path_matches_check_state_repo_convention(self):
        self.assertEqual(WORKBENCH_SCRIPT_RELATIVE_PATH, ".hc/workbench-setup.sh")


if __name__ == "__main__":
    unittest.main()
