# agents/scrum_team/tests/test_reference_workbench_templates.py
"""
US-0022 (EP-0010): tests for the three shipped reference Development
Workbench templates (python-flask, kotlin-multiplatform,
react-vite-firebase) under config/development-workbench-templates/ - as
distinct from test_workbench_templates.py, which tests US-0021's generic
template-resolution mechanism itself against synthetic tmp fixtures (plus
one pass over the placeholder fixture-echo/ directory).

Every test here exercises the real, shipped template directories via
resolve_workbench_template - no monkeypatching - so a broken/missing file
in config/development-workbench-templates/<name>/ fails exactly the way it
would for a real Architect calling propose_framework_change(template_name=
"python-flask", ...).

Test Approach (per US-0022's story file):
  - Unit tests asserting each template's declared MCP config and allowlist
    hosts are well-formed and non-empty - see
    TestReferenceTemplatesResolveWithWellFormedBundles below.
  - Explicit test/doc check that kotlin-multiplatform's iOS limitation is
    surfaced (not silently dropped) - see
    TestKotlinMultiplatformIosLimitationIsSurfaced below.
  - Per-template smoke test that scaffolds a minimal app from the template
    and runs its test suite inside the resulting workbench image: each
    template ships its own config/development-workbench-templates/<name>/
    smoke-test.sh for exactly this. It is NOT invoked by this file (or by
    `python3 run_tests.py`/the Docker Compose `agent` test service at all) -
    that service runs on agent-net, which net-proxy's allowlist restricts
    to github.com/api.github.com (see docker-compose.yaml), so it has no
    route to pypi.org/registry.npmjs.org/dl.google.com, which installing
    any of these toolchains for real requires. TestSmokeTestScriptsExist
    below only checks that each smoke-test.sh exists, is executable, and
    documents (in its own header comment) why it isn't run here and where
    it should be run instead (a normal CI runner or developer machine with
    real internet access) - see each script's own header comment for the
    full explanation.
"""
import re
import unittest

from agents.scrum_team.tools.workbench_templates import (
    _templates_root,
    resolve_workbench_template,
)

REFERENCE_TEMPLATE_NAMES = ["python-flask", "kotlin-multiplatform", "react-vite-firebase"]


class TestReferenceTemplatesResolveWithWellFormedBundles(unittest.TestCase):
    """Each of the three US-0022 reference templates resolves successfully
    and bundles a non-empty, well-formed script, MCP config, and
    allowlist - the AC every template must satisfy independently."""

    def test_all_three_reference_templates_are_discoverable_by_name(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                result = resolve_workbench_template(name)
                self.assertEqual(result["status"], "ok", result.get("message"))

    def test_each_template_has_a_non_empty_executable_looking_script(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                result = resolve_workbench_template(name)
                script = result["script"]
                self.assertIsInstance(script, str)
                self.assertTrue(script.strip())
                self.assertTrue(script.startswith("#!"), "script should start with a shebang")

    def test_each_template_has_a_non_empty_well_formed_mcp_config(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                result = resolve_workbench_template(name)
                mcp_config = result["mcp_config"]
                self.assertIsInstance(mcp_config, dict)
                self.assertIn("mcpServers", mcp_config)
                servers = mcp_config["mcpServers"]
                self.assertIsInstance(servers, dict)
                self.assertTrue(servers, "mcpServers should declare at least one server")
                for server_name, server_def in servers.items():
                    self.assertIsInstance(server_name, str)
                    self.assertTrue(server_name)
                    self.assertIsInstance(server_def, dict)
                    self.assertIn("command", server_def)
                    self.assertTrue(server_def["command"])

    def test_each_template_has_a_non_empty_allowlist_with_plausible_hostnames(self):
        # A plain hostname (propose_framework_change renders these verbatim
        # into the PR body as `- \`host\``, not a regex) - rough but useful
        # sanity check: at least one dot, no scheme/path/whitespace.
        hostname_re = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                result = resolve_workbench_template(name)
                hosts = result["additional_allowlist_hosts"]
                self.assertIsInstance(hosts, list)
                self.assertTrue(hosts, "additional_allowlist_hosts should be non-empty")
                for host in hosts:
                    self.assertRegex(host, hostname_re, msg=f"{host!r} doesn't look like a plain hostname")

    def test_each_template_has_a_non_empty_description(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                result = resolve_workbench_template(name)
                self.assertTrue(result["description"].strip())

    def test_python_flask_allowlist_covers_pypi_at_minimum(self):
        result = resolve_workbench_template("python-flask")
        hosts = result["additional_allowlist_hosts"]
        self.assertIn("pypi.org", hosts)
        self.assertIn("files.pythonhosted.org", hosts)

    def test_python_flask_script_covers_scaffold_install_test_run_debug_and_exploratory_testing(self):
        script = resolve_workbench_template("python-flask")["script"]
        self.assertIn("flask", script.lower())
        self.assertIn("pytest", script.lower())
        self.assertIn("debugpy", script.lower())
        # exploratory testing: an HTTP client (HTTPie) or `flask shell`
        self.assertTrue("httpie" in script.lower() or "http " in script.lower())
        self.assertIn("shell", script.lower())

    def test_react_vite_firebase_allowlist_covers_npm_and_firebase_hosts(self):
        result = resolve_workbench_template("react-vite-firebase")
        hosts = result["additional_allowlist_hosts"]
        self.assertIn("registry.npmjs.org", hosts)
        self.assertTrue(any("firebase" in h or "googleapis" in h for h in hosts))

    def test_react_vite_firebase_script_covers_scaffold_install_test_run_emulator_and_debug(self):
        script = resolve_workbench_template("react-vite-firebase")["script"].lower()
        self.assertIn("vite", script)
        self.assertIn("firebase", script)
        self.assertIn("npm", script)
        self.assertIn("emulator", script)
        self.assertIn("inspect", script)  # Node debugging

    def test_kotlin_multiplatform_allowlist_covers_gradle_and_maven_hosts(self):
        result = resolve_workbench_template("kotlin-multiplatform")
        hosts = result["additional_allowlist_hosts"]
        self.assertTrue(any("gradle" in h for h in hosts))
        self.assertTrue(any("maven" in h or "google" in h for h in hosts))

    def test_kotlin_multiplatform_script_installs_jdk_and_android_sdk_and_runs_android_tests(self):
        script = resolve_workbench_template("kotlin-multiplatform")["script"].lower()
        self.assertIn("jdk", script)
        self.assertIn("android", script)
        self.assertIn("sdkmanager", script)
        self.assertIn("gradlew", script)
        self.assertIn("testdebugunittest", script)


class TestKotlinMultiplatformIosLimitationIsSurfaced(unittest.TestCase):
    """AC: iOS build/run/debug realistically requires a macOS host with
    Xcode - the template must document this explicitly rather than
    silently promising iOS support works inside the Linux-container
    workbench. Checked everywhere an Architect would encounter this
    template's capabilities: the script itself, and the manifest
    description surfaced by list_workbench_templates/get_workbench_template
    before the Architect even picks it."""

    def setUp(self):
        self.result = resolve_workbench_template("kotlin-multiplatform")

    def test_script_explicitly_documents_the_ios_limitation(self):
        script = self.result["script"]
        self.assertIn("macOS", script)
        self.assertIn("Xcode", script)
        self.assertIn("iOS LIMITATION", script)
        self.assertTrue(
            "cannot" in script.lower() or "does not" in script.lower(),
            "script should explicitly say iOS build/run/debug cannot happen in this container",
        )

    def test_manifest_description_explicitly_documents_the_ios_limitation(self):
        description = self.result["description"]
        self.assertIn("macOS", description)
        self.assertIn("Xcode", description)
        self.assertIn("iOS LIMITATION", description)
        self.assertTrue("not" in description.lower())

    def test_ios_limitation_is_not_silently_dropped_from_either_surface(self):
        # Belt-and-suspenders: both surfaces independently mention the
        # specific missing capabilities (build/run/debug), not just "iOS"
        # in passing.
        for surface_name, text in (("script", self.result["script"]), ("description", self.result["description"])):
            with self.subTest(surface=surface_name):
                lowered = text.lower()
                self.assertTrue(
                    any(word in lowered for word in ("build", "run", "debug")),
                    f"{surface_name} should name the specific iOS capabilities that don't work here",
                )

    def test_android_is_not_similarly_caveated_since_it_genuinely_works_here(self):
        script = self.result["script"]
        self.assertIn("fully usable in this container", script)


class TestSmokeTestScriptsExist(unittest.TestCase):
    """Per US-0022's Test Approach: a smoke test that scaffolds a minimal
    app from the template and runs its test suite inside the resulting
    workbench image. Full container scaffolding needs real internet access
    (PyPI/npm/Android SDK) this repo's own sandboxed test container
    (agent-net, see docker-compose.yaml) deliberately does not have, so
    each template ships its own standalone smoke-test.sh instead of this
    file invoking Docker directly. These checks confirm the smoke test
    exists, is executable, and documents where it should actually be run -
    not that it was skipped silently."""

    def test_each_reference_template_ships_an_executable_smoke_test_script(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                script_path = _templates_root() / name / "smoke-test.sh"
                self.assertTrue(script_path.is_file(), f"{script_path} should exist")
                import os
                self.assertTrue(os.access(script_path, os.X_OK), f"{script_path} should be executable")

    def test_each_smoke_test_documents_where_it_should_actually_run(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                script_path = _templates_root() / name / "smoke-test.sh"
                content = script_path.read_text(encoding="utf-8")
                self.assertIn("NOT run by", content)
                self.assertIn("CI", content)
                self.assertIn("docker build", content)

    def test_each_smoke_test_scaffolds_and_runs_a_test_suite_against_the_template_script(self):
        for name in REFERENCE_TEMPLATE_NAMES:
            with self.subTest(template=name):
                script_path = _templates_root() / name / "smoke-test.sh"
                content = script_path.read_text(encoding="utf-8")
                # Each smoke test builds FROM the template's own
                # workbench-setup.sh, not a hand-rolled substitute.
                self.assertIn("workbench-setup.sh", content)
                self.assertTrue(
                    "pytest" in content or "vitest" in content or "testDebugUnitTest" in content,
                    "smoke test should actually invoke a test runner",
                )


if __name__ == "__main__":
    unittest.main()
