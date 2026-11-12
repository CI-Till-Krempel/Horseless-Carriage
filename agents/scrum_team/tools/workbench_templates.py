# agents/scrum_team/tools/workbench_templates.py
"""
US-0021 (EP-0010): a library of named, reusable "Development Workbench"
templates - toolchain install script + MCP config fragment + declared
network allowlist hosts, one per tech stack - that the Architect can pick
by name instead of hand-authoring `.hc/workbench-setup.sh` from scratch
every time (see propose_framework_change, tools/workflow.py).

Mirrors `config/model-templates/`'s own convention: plain, named
files/directories under this (Horseless Carriage's own) repo, read at
propose-time via _project_root() - never the product/state repo
_configured_repo_root() points at, and never written to by anything here.
Same axis, different concern: model-templates picks an LLM provider;
these pick a tech-stack toolchain.

Template contract - config/development-workbench-templates/<name>/:
  - workbench-setup.sh (REQUIRED): the toolchain install script content,
    copied verbatim into `.hc/workbench-setup.sh` by propose_framework_change
    unless the caller supplies its own workbench_setup_script to
    customize/replace it.
  - mcp-config.json (optional): an MCP config fragment, shaped exactly like
    this repo's own `.mcp.json` (`{"mcpServers": {...}}`), merged into
    `.hc/workbench-mcp.json`.
  - manifest.json (optional): `{"description": str,
    "additional_allowlist_hosts": [str, ...]}` - a human-readable blurb and
    the package-repository hosts this stack's toolchain needs at runtime.

At this stage (US-0021) no real reference templates ship yet - US-0022
adds python-flask/kotlin-multiplatform/react-vite-firebase. The one
`fixture-echo` template under config/development-workbench-templates/ is a
deliberately minimal placeholder that exists only so this module's
end-to-end "read real files off disk" path has something real to read;
it is not a usable reference workbench and templates/tests should not
treat it as one.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import _project_root, _configured_repo_root

_TEMPLATES_DIR_NAME = "development-workbench-templates"

# GH-456: repo-relative path to the per-project workbench script, in the
# PRODUCT/state repo (_configured_repo_root), never this one
# (_project_root) - the one thing propose_framework_change actually writes
# (via a human-reviewed PR). Kept here (not duplicated as a literal in
# workflow.py) since check_workbench_tooling below needs it too; workflow.py
# imports it from here instead of re-declaring its own copy.
WORKBENCH_SCRIPT_RELATIVE_PATH = ".hc/workbench-setup.sh"

# GH-456: byte-for-byte the same placeholder content check_state_repo.py's
# own ensure_workbench_script_default/is_workbench_script_placeholder
# write/compare against - duplicated here, deliberately, rather than
# imported: check_state_repo.py is a host-side script (imports lib_env) run
# by doctor.py/run_tests.py/run_adk_eval.py on the HOST, before any agent
# container exists, and is never COPY'd into the agent image (see
# agent.Dockerfile's `final` stage - only ./agents, ./spec-templates,
# ./eval, ./config/development-workbench-templates, plus a handful of named
# files, ever land in the real runtime image). Importing it from in-container
# code would either fail outright or silently diverge from what actually
# ships. This is the same "kept independent deliberately" shape
# tools/test_runners.py's own module docstring already documents for a
# near-identical host/container split - test_workbench_templates.py cross-
# checks this constant against check_state_repo.py's own (importable from
# the host-side test suite) so the two can never silently drift apart.
_WORKBENCH_SCRIPT_PLACEHOLDER = (
    "#!/bin/sh\n"
    "# SPEC-ISSUE-0064: per-project \"workbench\" - whatever toolchain THIS\n"
    "# product's tech stack needs (JDK, Node, Go, ...) gets installed here,\n"
    "# e.g. `apt-get update && apt-get install -y default-jdk`.\n"
    "#\n"
    "# Never hand-edit this file directly - it's built into the agent image\n"
    "# at every rebuild, so a direct edit here has no effect until it's\n"
    "# actually committed and the image is rebuilt, and (more importantly)\n"
    "# the Architect's propose_framework_change tool is the only way this\n"
    "# is meant to change: it always opens a human-reviewed Pull Request,\n"
    "# the same way propose_steering_change does for <Role>-identity.md -\n"
    "# see agents/scrum_team/tools/workflow.py.\n"
    "#\n"
    "# No workbench-specific setup yet - this placeholder is a harmless\n"
    "# no-op so the agent image still builds for a project with no\n"
    "# toolchain needs beyond horseless-carriage's own Python base.\n"
)
# Plain lowercase-alnum-and-hyphen names only (e.g. "python-flask") - no
# "/", "..", or absolute paths. template_name is externally supplied (the
# Architect's own choice, ultimately LLM-controlled), so this is a strict
# allowlist, not just a style preference: it's what keeps resolution from
# ever being able to escape config/development-workbench-templates/.
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _templates_root() -> Path:
    return _project_root() / "config" / _TEMPLATES_DIR_NAME


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def list_workbench_templates(tool_context=None) -> Dict[str, Any]:
    """
    Lists the named Development Workbench templates available under
    config/development-workbench-templates/, so the Architect can discover
    what exists before calling propose_framework_change with a
    template_name - the same way config/model-templates/'s named files are
    discovered by setup_llm.py.

    Returns {"status": "ok", "templates": [{"name", "description",
    "additional_allowlist_hosts"}, ...]}, sorted by name. A subdirectory
    missing its required workbench-setup.sh is silently skipped (not an
    error) - lets a contributor stage a new template directory
    incrementally without breaking discovery of every other one. Empty
    list (not an error) if the directory doesn't exist yet at all.
    """
    root = _templates_root()
    templates: List[Dict[str, Any]] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not (child / "workbench-setup.sh").is_file():
                continue
            manifest = _read_json(child / "manifest.json")
            templates.append({
                "name": child.name,
                "description": manifest.get("description", ""),
                "additional_allowlist_hosts": list(manifest.get("additional_allowlist_hosts", []) or []),
            })
    return {"status": "ok", "templates": templates}


def resolve_workbench_template(template_name: str) -> Dict[str, Any]:
    """
    Loads one named Development Workbench template's full bundle - see
    this module's own docstring for the on-disk contract. Used both by
    propose_framework_change (tools/workflow.py) to fold a template into a
    human-reviewed PR, and directly exposed as get_workbench_template so
    the Architect can preview one before deciding what to customize.

    Returns {"status": "ok", "name", "script", "mcp_config",
    "additional_allowlist_hosts", "description"} on success, or
    {"status": "error", "message"} for:
      - an invalid template_name (fails the plain name pattern check),
      - an unknown template_name (no matching directory),
      - a template directory missing its required workbench-setup.sh.

    mcp_config is always a dict (possibly {} if mcp-config.json is absent
    or unreadable); additional_allowlist_hosts is always a list (possibly
    empty).
    """
    if not template_name or not _TEMPLATE_NAME_RE.match(template_name):
        return {
            "status": "error",
            "message": (
                f"Invalid template_name {template_name!r} - must be a plain lowercase "
                "alphanumeric-and-hyphen name (e.g. 'python-flask'), not a path."
            ),
        }

    root = _templates_root().resolve()
    template_dir = (root / template_name).resolve()
    # Defense in depth: the name pattern above already forbids "/"/".." so
    # this can't actually escape root, but a resolved-path check costs
    # nothing and matches the belt-and-suspenders style write_file/
    # delete_file already use for their own repo_root containment check.
    if template_dir.parent != root or not template_dir.is_dir():
        available = [t["name"] for t in list_workbench_templates().get("templates", [])]
        return {
            "status": "error",
            "message": (
                f"Unknown Development Workbench template {template_name!r}. "
                f"Available templates: {available or '(none configured yet)'}"
            ),
        }

    script_path = template_dir / "workbench-setup.sh"
    if not script_path.is_file():
        return {
            "status": "error",
            "message": f"Template {template_name!r} is missing its required workbench-setup.sh.",
        }

    manifest = _read_json(template_dir / "manifest.json")
    mcp_config = _read_json(template_dir / "mcp-config.json")

    return {
        "status": "ok",
        "name": template_name,
        "script": script_path.read_text(encoding="utf-8"),
        "mcp_config": mcp_config,
        "additional_allowlist_hosts": list(manifest.get("additional_allowlist_hosts", []) or []),
        "description": manifest.get("description", ""),
    }


def get_workbench_template(template_name: str, tool_context=None) -> Dict[str, Any]:
    """
    Agent-callable preview of one named Development Workbench template -
    its full toolchain script, MCP config fragment, declared allowlist
    hosts, and description - so the Architect can inspect a template
    (e.g. to decide what to customize) before calling
    propose_framework_change(template_name=...). Read-only: never writes
    anything, proposes nothing. See resolve_workbench_template's docstring
    for the full contract and error cases.
    """
    return resolve_workbench_template(template_name)


# GH-456: "list of supported tech stacks" detection - a best-effort, purely
# read-only sniff of manifest files the project's own repo already has on
# disk, one detector per shipped reference template (US-0022). Order
# matters only in that the first match wins; none of these overlap in
# practice (a project is never simultaneously Flask AND Kotlin
# Multiplatform AND React+Vite+Firebase). Never raises - an unreadable/
# missing file is just "that detector doesn't match", same as
# resolve_workbench_template's own _read_json tolerance for a malformed
# manifest.json.
def _manifest_text(repo_root: Path, *relative_parts: str) -> str:
    try:
        return Path(repo_root, *relative_parts).read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def _looks_like_python_flask(repo_root: Path) -> bool:
    for name in ("requirements.txt", "Pipfile", "pyproject.toml"):
        if "flask" in _manifest_text(repo_root, name):
            return True
    return False


def _looks_like_kotlin_multiplatform(repo_root: Path) -> bool:
    for name in ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "settings.gradle"):
        text = _manifest_text(repo_root, name)
        if "multiplatform" in text:
            return True
    # Typical Kotlin Multiplatform source-set layout (commonMain/...) even
    # when the root manifest text itself doesn't say "multiplatform"
    # verbatim (e.g. a convention plugin applied by name only).
    try:
        return next(repo_root.glob("**/src/commonMain"), None) is not None
    except OSError:
        return False


def _looks_like_react_vite_firebase(repo_root: Path) -> bool:
    text = _manifest_text(repo_root, "package.json")
    return "vite" in text and ("react" in text or "firebase" in text)


# Name must match a real subdirectory under config/development-workbench-templates/
# (cross-checked against list_workbench_templates() at call time below, not
# just trusted here) - these three are US-0022's shipped reference
# templates. Adding a fourth template later (see docs/WORKBENCHES.md "Adding
# a new template") should add its own detector here too, but a template
# with no detector yet is never an error - it's simply never auto-
# recommended, same as any other genuinely new/unrecognized stack.
_TECH_STACK_DETECTORS = (
    ("python-flask", _looks_like_python_flask),
    ("kotlin-multiplatform", _looks_like_kotlin_multiplatform),
    ("react-vite-firebase", _looks_like_react_vite_firebase),
)


def detect_recommended_workbench_template(repo_root: Path) -> Optional[str]:
    """
    Best-effort "what tech stack does this project's repo actually look
    like" sniff, for check_workbench_tooling below - returns the name of
    the first shipped Development Workbench template (see
    _TECH_STACK_DETECTORS) whose detector matches a real manifest file
    under repo_root, or None if nothing recognized (a genuinely new/
    unsupported stack - never an error, see that function's own docstring
    for how this degrades gracefully).
    """
    for name, detector in _TECH_STACK_DETECTORS:
        try:
            if detector(repo_root):
                return name
        except OSError:
            continue
    return None


def check_workbench_tooling(tool_context=None) -> Dict[str, Any]:
    """
    GH-456: "check regularly (at startup / each sprint) whether the tooling
    (test frameworks) is set up" - the agent-facing, in-session counterpart
    of SPEC-ISSUE-0064's host-side doctor.py nudge (which can only warn
    about a *generic* JVM/Gradle gap, and runs on the host before any agent
    container exists - check_state_repo.py, which that nudge lives in, is
    never copied into the agent image itself, see agent.Dockerfile). Wired
    into init_scrum_state's return value (startup - GH #423: available to
    every role, auto-run once per session) and start_sprint's return value
    (each sprint - ScrumMaster's tool) so the team sees a fresh, structured
    nudge at both of the points the issue names, rather than only ever
    discovering a missing JDK/Node/toolchain mid-sprint when the Tested
    gate's real test run fails confusingly. Also directly callable by the
    Architect (its own tool) to check on demand.

    Never writes anything and never calls propose_framework_change itself -
    same "mechanically enforced through tools, but the proposal itself
    always needs a rationale only the calling role can supply" shape
    propose_framework_change's own docstring describes; this only ever
    surfaces a recommendation for the Architect to act on.

    Returns:
      {"status": "ok", "workbench_configured": bool,
       "detected_tech_stack": str | None, "action_needed": bool,
       "recommendation": str | None}

    - workbench_configured: False if `.hc/workbench-setup.sh` is missing
      entirely, or still exactly the auto-created placeholder
      (_WORKBENCH_SCRIPT_PLACEHOLDER above - see its own comment for why
      this is a deliberate duplicate of check_state_repo.py's identical
      constant, cross-checked by a test). True for ANY other content,
      including one that no longer matches detected_tech_stack's own
      template verbatim - once the Architect has genuinely customized a
      workbench, this check never second-guesses that choice just because
      a cheap manifest sniff disagrees with it.
    - detected_tech_stack: the name of a shipped Development Workbench
      template (see list_workbench_templates) whose detector recognized a
      real manifest file in this project, cross-checked against what's
      actually still available (a stale detector match against a
      renamed/removed template is never recommended) - None if nothing
      recognized.
    - action_needed/recommendation are False/None whenever workbench_configured
      is True. Otherwise:
      - detected_tech_stack set: recommends
        `propose_framework_change(template_name=<detected_tech_stack>, ...)`
        - the EP-0010 templated path.
      - detected_tech_stack is None (an unsupported/unrecognized stack):
        degrades gracefully to recommending US-0021's original fully-
        custom-script path - `propose_framework_change(workbench_setup_script=...)`
        with no template_name - rather than crashing or guessing at a
        template that doesn't fit.
    """
    repo_root = _configured_repo_root(tool_context)
    workbench_path = repo_root / WORKBENCH_SCRIPT_RELATIVE_PATH
    try:
        script_text = workbench_path.read_text(encoding="utf-8", errors="replace") if workbench_path.is_file() else None
    except OSError:
        script_text = None
    workbench_configured = script_text is not None and script_text != _WORKBENCH_SCRIPT_PLACEHOLDER

    detected_tech_stack = detect_recommended_workbench_template(repo_root)
    available_names = {t["name"] for t in list_workbench_templates().get("templates", [])}
    if detected_tech_stack not in available_names:
        detected_tech_stack = None

    if workbench_configured:
        return {
            "status": "ok",
            "workbench_configured": True,
            "detected_tech_stack": detected_tech_stack,
            "action_needed": False,
            "recommendation": None,
        }

    if detected_tech_stack:
        recommendation = (
            f"No Development Workbench is set up yet for this project ({WORKBENCH_SCRIPT_RELATIVE_PATH} is "
            f"missing or still the untouched placeholder), and its tech stack looks like {detected_tech_stack!r} "
            f"- call propose_framework_change(template_name={detected_tech_stack!r}, rationale=...) to bootstrap "
            "it (get_workbench_template lets you preview the template first). See docs/WORKBENCHES.md."
        )
    else:
        recommendation = (
            f"No Development Workbench is set up yet for this project ({WORKBENCH_SCRIPT_RELATIVE_PATH} is "
            "missing or still the untouched placeholder), and its tech stack doesn't match any shipped "
            "template (see list_workbench_templates) - call propose_framework_change(workbench_setup_script=..., "
            "rationale=...) with a fully custom script for this stack instead. See docs/WORKBENCHES.md."
        )

    return {
        "status": "ok",
        "workbench_configured": False,
        "detected_tech_stack": detected_tech_stack,
        "action_needed": True,
        "recommendation": recommendation,
    }
