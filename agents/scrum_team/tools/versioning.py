# agents/scrum_team/tools/versioning.py
"""
GH-501: enforces Semantic Versioning (https://semver.org/) discipline for the
*target product* the scrum team manages (distinct from Horseless Carriage's
own meta-release, documented in RELEASE.md and tracked via the VERSION
file/`hc_version` - see base.py's `_hc_version`). Before this, the target
product had no SemVer scheme of its own at all - `generate_changelog_entry`
(changelog.py) versions its entries by sprint number precisely because
nothing else existed yet (see that function's own docstring, which points
here).

Two things this module enforces, both driven by `ScrumState.sprint_backlog`
(the same source changelog entries are already derived from):

1. **Correct increment**: a proposed version bump must be the right SemVer
   increment (major/minor/patch) for the *type* of change actually being
   released - major for a breaking change, minor for a new backward-
   compatible feature, patch for a fix - relative to the last released
   version. Change type is derived per backlog item (`classify_item_change_
   type`) from an explicit `change_type`/`breaking_change` field if the role
   proposing the story set one, else a reasonable default by item `type`
   (see that function's own docstring) - and the release as a whole takes
   the *highest-severity* type among everything actually Done this cycle
   (`required_change_type`), the same way a single breaking change forces a
   major bump even if everything else shipped alongside it was "just" a fix.

2. **Snapshot suffix discipline**: a version proposed mid-development (before
   it's actually released) must carry a `-snapshot` pre-release suffix (e.g.
   `1.3.0-snapshot`) - so an in-development build is never confused for a
   tagged release - and that suffix must be stripped (finalized) only at
   actual release time, never before.

Enforcement posture (two different mechanisms, deliberately):
- `propose_version_bump` - the tool a role (Product Owner/Architect) calls to
  set the next version - HARD BLOCKS (refuses to record the new version,
  `status: "error"`) on either violation. This is the cheapest point to
  correct a mistake (nothing downstream has committed to the bad version
  yet), and the check is fully mechanical/deterministic (no judgment call
  about unrelated changes, unlike e.g. the stray-file edge case in US-0011),
  so there's no good reason to let a wrong version past it - same posture as
  create_release_pr's own human-approval gate (also a hard, deterministic
  refusal).
- `release_version_advisory` (consulted by `create_release_pr`, tools/
  github.py) - ADVISORY ONLY (a flag added to the result, never a refusal to
  open the PR) if the version already in play doesn't match what the
  backlog's change types now call for. The backlog can keep changing between
  a mid-sprint `propose_version_bump` call and the actual release (more
  stories reaching Done, one of them turning out to be breaking) - re-
  litigating that as a hard block at the one moment release is trying to go
  out risks exactly the "team fabricates/reclassifies just to satisfy a
  mechanical gate" failure mode `sprint_capacity_advisory` (budget.py) and
  `ready_backlog_shortfall` (helpers.py) already document reasons to avoid;
  a visible flag in the release PR gives a human reviewer the same
  information without that risk.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Tuple

from ..helpers import is_story_done

# The pre-release identifier this project standardizes on for an
# in-development, not-yet-released build (GH-501). Chosen over "-dev"/"-rc"
# because it's the exact word the issue itself names, and reads
# unambiguously as "not a release" to a human seeing it in a version string.
SNAPSHOT_SUFFIX = "snapshot"

# Ordered least -> most severe, matching SemVer's own patch < minor < major.
CHANGE_TYPES: Tuple[str, ...] = ("fix", "feature", "breaking")
_CHANGE_TYPE_RANK = {change_type: rank for rank, change_type in enumerate(CHANGE_TYPES)}
_BUMP_FOR_CHANGE_TYPE = {"breaking": "major", "feature": "minor", "fix": "patch"}

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?$"
)


def parse_version(version: str) -> Tuple[int, int, int, Optional[str]]:
    """
    Parses a `MAJOR.MINOR.PATCH[-prerelease]` string. Raises ValueError (not
    a tool-result dict - this is an internal building block every other
    function in this module composes, not an agent-facing tool itself) if
    `version` isn't a syntactically valid SemVer core, optionally followed by
    a `-` and a non-empty pre-release identifier.
    """
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"Version must be a non-empty string, got {version!r}.")
    match = _SEMVER_RE.match(version.strip())
    if not match:
        raise ValueError(
            f"'{version}' is not a valid semantic version - expected MAJOR.MINOR.PATCH "
            "(e.g. '1.2.3'), optionally with a '-prerelease' suffix (e.g. '1.3.0-snapshot')."
        )
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), prerelease


def version_core(version: str) -> str:
    """The bare `MAJOR.MINOR.PATCH` string, discarding any pre-release suffix."""
    major, minor, patch, _ = parse_version(version)
    return f"{major}.{minor}.{patch}"


def is_snapshot(version: str) -> bool:
    """True if `version` carries this project's `-snapshot` pre-release suffix."""
    try:
        _, _, _, prerelease = parse_version(version)
    except ValueError:
        return False
    return (prerelease or "").lower() == SNAPSHOT_SUFFIX


def with_snapshot_suffix(core: str) -> str:
    """
    Appends the `-snapshot` suffix to a bare `MAJOR.MINOR.PATCH` string.
    Raises ValueError if `core` isn't a valid version core, or already
    carries a pre-release suffix of its own (callers should finalize/strip
    first rather than stacking suffixes).
    """
    major, minor, patch, prerelease = parse_version(core)
    if prerelease:
        raise ValueError(
            f"'{core}' already carries a pre-release suffix ('-{prerelease}') - "
            "pass a bare MAJOR.MINOR.PATCH core instead."
        )
    return f"{major}.{minor}.{patch}-{SNAPSHOT_SUFFIX}"


def strip_snapshot_suffix(version: str) -> str:
    """Finalizes a version for release: drops any pre-release suffix, keeping just the core."""
    return version_core(version)


def classify_item_change_type(item: Dict[str, Any]) -> str:
    """
    Classifies one sprint_backlog item as 'breaking' | 'feature' | 'fix'.

    - An explicit `change_type` field on the item (set by whichever role
      proposed it - `upsert_story`/`upsert_epic`/`upsert_issue` all accept
      arbitrary extra fields today) wins if it's one of CHANGE_TYPES.
    - Else `breaking_change: True` promotes it to 'breaking' regardless of
      item type - the one flag a role sets deliberately when a change breaks
      an existing contract, since nothing about an item's bare `type` field
      (Epic/User Story/Issue) can tell that apart from an ordinary feature.
    - Else: an Issue (a reported gap/bug - see upsert_issue's own docstring)
      defaults to 'fix'; an Epic or User Story defaults to 'feature' (new,
      backward-compatible capability) - the common case for most delivered
      work, matching RELEASE.md's own MINOR definition ("new capability,
      backward-compatible").
    """
    if not isinstance(item, dict):
        return "feature"
    explicit = str(item.get("change_type") or "").strip().lower()
    if explicit in CHANGE_TYPES:
        return explicit
    if item.get("breaking_change") is True:
        return "breaking"
    if item.get("type") == "Issue":
        return "fix"
    return "feature"


def required_change_type(sprint_backlog: List[Dict[str, Any]]) -> Optional[str]:
    """
    The highest-severity change type among every sprint_backlog item that has
    actually reached a Done/Accepted status (`is_story_done` - the same gate
    `derive_changelog_entries` already uses, so "what's in this release" stays
    one consistent definition across changelog and version enforcement).
    Returns None if nothing has reached Done yet - there's no change to
    classify a required increment against (see `validate_version_increment`'s
    own handling of that case).
    """
    done_items = [
        item for item in (sprint_backlog or [])
        if isinstance(item, dict) and is_story_done(item.get("status"))
    ]
    if not done_items:
        return None
    change_types = [classify_item_change_type(item) for item in done_items]
    return max(change_types, key=lambda change_type: _CHANGE_TYPE_RANK[change_type])


def expected_bump(change_type: str) -> str:
    """Maps a change type to the SemVer increment it requires ('major'/'minor'/'patch')."""
    if change_type not in _BUMP_FOR_CHANGE_TYPE:
        raise ValueError(f"Unknown change_type '{change_type}' - expected one of {CHANGE_TYPES}.")
    return _BUMP_FOR_CHANGE_TYPE[change_type]


def compute_expected_version_core(previous_version: str, change_type: str) -> str:
    """The exact MAJOR.MINOR.PATCH this change_type requires, relative to previous_version."""
    major, minor, patch, _ = parse_version(previous_version)
    bump = expected_bump(change_type)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def validate_version_increment(
    previous_version: str, proposed_version: str, change_type: Optional[str]
) -> Dict[str, Any]:
    """
    Checks ONLY the MAJOR.MINOR.PATCH increment (ignores any pre-release
    suffix on `proposed_version` entirely) - see `validate_snapshot_suffix`
    for the separate suffix-discipline check, and `validate_version_bump`
    for both composed together. Kept separate so `release_version_advisory`
    (tools/github.py's create_release_pr) can re-check increment-correctness
    alone, without also re-flagging the suffix question finalize_release_
    version is about to resolve mechanically anyway.

    Returns {"valid", "errors", "expected_increment", "expected_version_core",
    "proposed_version_core"}.
    """
    try:
        previous_core = version_core(previous_version)
    except ValueError as e:
        return {"valid": False, "errors": [f"Previous version is invalid: {e}"],
                "expected_increment": None, "expected_version_core": None, "proposed_version_core": None}
    try:
        proposed_core = version_core(proposed_version)
    except ValueError as e:
        return {"valid": False, "errors": [f"Proposed version is invalid: {e}"],
                "expected_increment": None, "expected_version_core": None, "proposed_version_core": None}

    errors: List[str] = []
    expected_increment: Optional[str] = None
    expected_core: Optional[str] = None

    if change_type is not None:
        expected_increment = expected_bump(change_type)
        expected_core = compute_expected_version_core(previous_core, change_type)
        if proposed_core != expected_core:
            errors.append(
                f"'{proposed_core}' is not the correct SemVer increment for a '{change_type}' change "
                f"since the last released version '{previous_core}' - expected a {expected_increment} "
                f"bump to '{expected_core}'."
            )
    else:
        # Nothing has reached Done yet to classify a required increment
        # against - the only invariant left to enforce is ordering (a
        # proposed version must still be newer than the last release), not
        # which specific increment it is.
        previous_tuple = parse_version(previous_core)[:3]
        proposed_tuple = parse_version(proposed_core)[:3]
        if proposed_tuple <= previous_tuple:
            errors.append(
                f"'{proposed_core}' must be newer than the last released version '{previous_core}'."
            )

    return {
        "valid": not errors,
        "errors": errors,
        "expected_increment": expected_increment,
        "expected_version_core": expected_core,
        "proposed_version_core": proposed_core,
    }


def validate_snapshot_suffix(version: str, expect_snapshot: bool) -> Optional[str]:
    """
    Returns an error string if `version`'s pre-release suffix doesn't match
    what's expected for this point in the release cycle, else None.
    - expect_snapshot=True (mid-development): must carry the '-snapshot' suffix.
    - expect_snapshot=False (actual release/tag time): must carry no suffix at all.
    """
    try:
        _, _, _, prerelease = parse_version(version)
    except ValueError as e:
        return str(e)
    if expect_snapshot and (prerelease or "").lower() != SNAPSHOT_SUFFIX:
        return (
            f"A version proposed mid-development must carry the '-{SNAPSHOT_SUFFIX}' suffix "
            f"(got '{version}') - see RELEASE.md/GH-501, e.g. propose "
            f"'{version_core(version)}-{SNAPSHOT_SUFFIX}' instead."
        )
    if not expect_snapshot and prerelease:
        return (
            f"A release version must not carry a pre-release suffix (got '{version}') - "
            "strip it (finalize_release_version does this automatically at release time) "
            "before tagging."
        )
    return None


def validate_version_bump(
    previous_version: str,
    proposed_version: str,
    change_type: Optional[str],
    expect_snapshot: bool,
) -> Dict[str, Any]:
    """Composes `validate_version_increment` and `validate_snapshot_suffix` into one result."""
    result = validate_version_increment(previous_version, proposed_version, change_type)
    errors = list(result["errors"])
    suffix_error = validate_snapshot_suffix(proposed_version, expect_snapshot)
    if suffix_error:
        errors.append(suffix_error)
    result["errors"] = errors
    result["valid"] = not errors
    return result


def propose_version_bump(new_version: str, tool_context=None) -> Dict[str, Any]:
    """
    Records the target product's next version (mid-development - see
    `finalize_release_version` for actual release time). Call this
    whenever a version number is being proposed or bumped (e.g. Architect
    sizing a breaking change, Product Owner planning the next release).

    Validates, and HARD BLOCKS (refuses to record it, `status: "error"`) if
    either check fails:
    - `new_version`'s MAJOR.MINOR.PATCH must be the correct SemVer increment
      for the highest-severity change type among sprint_backlog items that
      have actually reached Done this cycle (see `required_change_type`),
      relative to the last released version (`ScrumState.product_released_
      version`, defaulting to '0.0.0' if nothing has ever been released).
    - `new_version` must carry the '-snapshot' suffix (mid-development, not
      yet released).

    On success, records `new_version` (with its '-snapshot' suffix intact)
    into `ScrumState.product_version`, and the change type it validated
    against into `product_version_change_type` - both consulted again by
    `create_release_pr` at actual release time.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return {"status": "error", "message": "propose_version_bump requires an active session (tool_context.state)."}
    state = tool_context.state
    previous_version = state.get("product_released_version") or "0.0.0"
    change_type = required_change_type(state.get("sprint_backlog", []) or [])
    validation = validate_version_bump(previous_version, new_version, change_type, expect_snapshot=True)
    if not validation["valid"]:
        return {
            "status": "error",
            "message": "; ".join(validation["errors"]),
            "validation": validation,
        }
    state["product_version"] = new_version
    state["product_version_change_type"] = change_type
    return {
        "status": "ok",
        "version": new_version,
        "change_type": change_type,
        "expected_increment": validation["expected_increment"],
        "previous_version": previous_version,
    }


def finalize_release_version(tool_context=None) -> Dict[str, Any]:
    """
    Strips/finalizes `ScrumState.product_version`'s '-snapshot' suffix at
    actual release time, and advances `product_released_version` (the
    baseline the next cycle's `propose_version_bump`/`release_version_
    advisory` compare against) to the finalized value. Called automatically
    from `create_release_pr` (tools/github.py) - a role never needs to call
    this directly, same as `generate_changelog` isn't called directly either.

    A no-op, `finalized: False`, if no version has ever been proposed this
    cycle (`product_version` unset) - this feature is entirely opt-in; a
    project that never calls `propose_version_bump` sees no versioning
    behavior from `create_release_pr` at all.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return {"status": "ok", "finalized": False, "message": "No active session - nothing to finalize."}
    state = tool_context.state
    current = state.get("product_version")
    if not current:
        return {"status": "ok", "finalized": False, "message": "No version has been proposed this cycle - nothing to finalize."}
    try:
        finalized = strip_snapshot_suffix(current)
    except ValueError as e:
        return {"status": "error", "message": f"Could not finalize '{current}': {e}"}
    was_snapshot = is_snapshot(current)
    state["product_version"] = finalized
    state["product_released_version"] = finalized
    return {"status": "ok", "finalized": True, "version": finalized, "was_snapshot": was_snapshot}


def release_version_advisory(tool_context=None) -> Optional[str]:
    """
    Advisory-only (see this module's own docstring for why this - not
    `propose_version_bump`'s hard block - is the enforcement point for
    this check) re-validation of the increment `ScrumState.product_version`
    represents, against whatever the sprint_backlog's change types call for
    *right now* - consulted by `create_release_pr` just before it finalizes
    the release. Returns None (nothing to flag) if no version is in use, or
    it's already correct.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return None
    state = tool_context.state
    version = state.get("product_version")
    if not version:
        return None
    previous_version = state.get("product_released_version") or "0.0.0"
    change_type = required_change_type(state.get("sprint_backlog", []) or [])
    result = validate_version_increment(previous_version, version, change_type)
    if result["valid"]:
        return None
    return "; ".join(result["errors"])
