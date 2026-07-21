# agents/scrum_team/tools/migrations.py
from typing import Any, Callable, Dict, List, Tuple


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(p) for p in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


def _version_lt(a: str, b: str) -> bool:
    return _version_tuple(a) < _version_tuple(b)


# Each entry: the version whose *shape* the migration fixes up TO. Applied in
# order for any persisted state whose recorded hc_version predates it. Empty
# today - no breaking ScrumState change exists yet, so this is a no-op hook
# point rather than a migration solving a problem that doesn't exist. See
# RELEASE.md "Migration scaffold".
MIGRATIONS: List[Tuple[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = [
    # ("0.2.0", _migrate_to_0_2_0),
]


def migrate_state(state: Dict[str, Any], from_version: str) -> Dict[str, Any]:
    """
    Applies any migrations registered for a version newer than from_version,
    in order, so a .hc/state.json written by an older Horseless Carriage
    version loads cleanly under a newer one.
    """
    for target_version, migrate in MIGRATIONS:
        if _version_lt(from_version, target_version):
            state = migrate(state)
    return state
