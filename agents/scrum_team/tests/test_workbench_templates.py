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


if __name__ == "__main__":
    unittest.main()
