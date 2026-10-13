"""
GH issue #288 / #310: the shared list of Horseless Carriage installations on
this machine - new_project.py appends to it when installing into a new
project, dashboard.py (GH issue #310) reads it to know what to show.

Stored outside any single product repo (~/.horseless-carriage/projects.json)
since a project's own STATE_REPO_PATH is the wrong place for a registry that
spans multiple, unrelated projects - and a machine-home-directory location
works the same way regardless of which project's installation is currently
being configured. Stdlib-only (plain JSON), matching every other host-side
script in this repo.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

REGISTRY_PATH = Path.home() / ".horseless-carriage" / "projects.json"


def load() -> List[Dict[str, Any]]:
    """Never raises - a missing or corrupted registry file just means no
    projects are known yet, not a hard error (this is a convenience
    listing, not a source of truth any single project's own operation
    depends on)."""
    if not REGISTRY_PATH.is_file():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    projects = data.get("projects", [])
    return projects if isinstance(projects, list) else []


def save(projects: List[Dict[str, Any]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps({"projects": projects}, indent=2) + "\n", encoding="utf-8")


def register(name: str, hc_path: Path, target_repo: Path) -> None:
    """Adds an entry, or updates one already registered for the same
    hc_path - idempotent, so re-running new_project.py against an
    already-registered project (e.g. after `git pull`-ing a newer
    Horseless Carriage into it) just refreshes name/target_repo rather
    than duplicating the entry.

    hc_path: the installed horseless-carriage/ submodule directory itself
    (where .env/docker-compose*.yaml live - what dashboard.py needs to
    actually operate that project's stack).
    target_repo: the product repo it's installed into (STATE_REPO_PATH)."""
    hc_path_str = str(hc_path)
    target_repo_str = str(target_repo)
    projects = load()
    for entry in projects:
        if entry.get("hc_path") == hc_path_str:
            entry["name"] = name
            entry["target_repo"] = target_repo_str
            save(projects)
            return
    projects.append({"name": name, "hc_path": hc_path_str, "target_repo": target_repo_str})
    save(projects)
