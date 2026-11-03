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

import lib_env

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


def sync_from_env(hc_path: Path) -> None:
    """Re-registers hc_path using whatever its OWN .env currently says,
    rather than whatever install-time snapshot a caller happens to have in
    hand - the single place "is this nested, and what's its real name/
    target_repo right now" gets decided, so new_project.py (first install)
    and setup_llm.py (every later reconfiguration) can't drift apart on the
    answer.

    A no-op for a standalone checkout (hc_path not nested inside another
    repo - project_registry only tracks new_project.py-installed,
    multi-project setups, same gate new_project.py's own
    _install_if_nested already used) or one with no STATE_REPO_PATH set
    yet (nothing meaningful to register).

    GH issue tracker: a real incident - a project's own .env STATE_REPO_PATH
    had drifted from project_registry.json's install-time snapshot (e.g. an
    .env copied between two projects' submodule checkouts to reuse API
    keys), and nothing ever re-synced the registry afterward - dashboard.py
    silently showed one project's sprint goal under another's name. Calling
    this after every STATE_REPO_PATH write (not just the first one, at
    install time) keeps the registry from ever drifting that far again."""
    parent = hc_path.parent
    if not (parent / ".git").exists():
        return
    state_repo_path = lib_env.read_env_var(hc_path / ".env", "STATE_REPO_PATH")
    if not state_repo_path:
        return
    target_repo = Path(state_repo_path)
    if not target_repo.is_absolute():
        # Docker Compose (and dashboard.py) resolve a relative
        # STATE_REPO_PATH against the compose file's own directory - hc_path
        # - not wherever this function happens to be called from.
        target_repo = (hc_path / target_repo).resolve()
    register(target_repo.name, hc_path, target_repo)


def sync_all() -> None:
    """Called once by dashboard.py at startup - re-syncs every registered
    project from its own current .env, and drops any entry whose hc_path
    isn't absolute.

    GH issue tracker ("old state" report): every call site that legitimately
    registers a project (new_project.py, setup_project.py, setup_llm.py)
    always resolves hc_path to an absolute path first - the one bug that
    ever produced a non-absolute entry was setup_llm.py calling
    sync_from_env(Path(".").parent) (fixed in 933ac89), which wrote a literal
    {"hc_path": "."} entry. That entry's hc_path trivially ".is_dir()"s true
    no matter where dashboard.py happens to be launched from (it IS the
    launch cwd), so project_snapshot's missing-hc_path check never caught
    it - the dashboard read whatever unrelated repo happened to be running
    dashboard.py's own .env/state.json and rendered it as a row with stale,
    unrelated data. Entries created before that fix are still sitting in
    ~/.horseless-carriage/projects.json on any machine that hit it; this is
    what actually cleans them up, since the fix itself only stopped new ones
    from being created."""
    projects = load()
    valid = [entry for entry in projects if Path(entry.get("hc_path", "")).is_absolute()]
    if len(valid) != len(projects):
        save(valid)
    for entry in valid:
        hc_path = Path(entry["hc_path"])
        if hc_path.is_dir():
            sync_from_env(hc_path)
