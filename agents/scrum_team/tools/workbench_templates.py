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
from typing import Any, Dict, List

from .base import _project_root

_TEMPLATES_DIR_NAME = "development-workbench-templates"
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
