# User Story

- Story ID: US-0025
- Title: Enforce Semantic Versioning and Snapshot Suffix
- Status: Accepted
- Priority: Should
- Owner: Product Owner
- Last Updated: 2026-11-12

## As a Product Owner, I want the target product's version string mechanically checked against SemVer rules and a development "snapshot" suffix, so a version bump can't skip the right major/minor/patch increment and an in-development build is never confused for a tagged release.

## Acceptance Criteria
- Given a proposed version and the change types (breaking/feature/fix) of everything that reached Done
  this sprint, when `propose_version_bump(new_version)` is called, then it hard-blocks (`status:
  "error"`) unless the proposed `MAJOR.MINOR.PATCH` is the correct increment for the highest-severity
  change type present (major for any breaking change, minor for a feature, patch for a fix-only
  release) relative to the last released version.
- Given a version proposed mid-development, when `propose_version_bump` validates it, then it also
  hard-blocks unless the version carries a `-snapshot` pre-release suffix (e.g. `0.3.0-snapshot`).
- Given a version was proposed this cycle, when `create_release_pr` runs, then it automatically
  strips the `-snapshot` suffix (finalizes the release version) and records the finalized version in
  the release PR body - a role never calls a separate "finalize" tool itself.
- Given the backlog's change types shifted after a version was proposed (e.g. a breaking change
  landed after a minor bump was already proposed), when `create_release_pr` runs, then it surfaces a
  non-blocking `version_warning` rather than refusing to open the release PR (see Design notes below
  for why this one check is advisory, not a hard gate, unlike the propose-time check above).
- Given no version was ever proposed this cycle, when `create_release_pr` runs, then it behaves
  exactly as before this story (no version line in the PR body, no warning) - the mechanism is
  entirely opt-in and must not break any existing release flow that doesn't use it.

## Notes
- Implements GH-501 (milestone v0.3.0).
- New module: `agents/scrum_team/tools/versioning.py` - SemVer parsing (`parse_version`,
  `version_core`), the snapshot-suffix convention (`is_snapshot`, `with_snapshot_suffix`,
  `strip_snapshot_suffix`), change-type classification (`classify_item_change_type`,
  `required_change_type` - derived from `sprint_backlog`'s `change_type`/`breaking_change`/`type`
  fields, the same `is_story_done`-gated source `derive_changelog_entries` (changelog.py) already
  uses), and the validation/enforcement functions (`validate_version_increment`,
  `validate_snapshot_suffix`, `validate_version_bump`) plus the three callable entry points
  (`propose_version_bump`, `finalize_release_version`, `release_version_advisory`).
- New `ScrumState` fields (persisted via `REPO_STATE_KEYS`, `tools/scrum.py`): `product_version`
  (current, possibly `-snapshot`-suffixed, string), `product_released_version` (last finalized
  baseline), `product_version_change_type` (recorded at propose time, for traceability).
- Design/technical notes - two different enforcement postures, deliberately: `propose_version_bump`
  hard-blocks (same posture as `create_release_pr`'s existing human-approval gate - deterministic,
  cheapest point to correct a mistake), while `release_version_advisory` (consulted inside
  `create_release_pr`) is advisory-only, matching this codebase's existing pattern for checks whose
  input (the backlog) can keep changing right up to release time (see `sprint_capacity_advisory`,
  `tools/budget.py`, and US-0010/US-0011's "warn/fail" release-diff check) - re-litigating a stale
  classification as a release-time refusal risks the same "team reclassifies/fabricates just to
  satisfy a mechanical gate" failure mode those checks already exist to avoid.
- Distinct from Horseless Carriage's own meta-release versioning (`RELEASE.md`, the committed
  `VERSION` file, `hc_version`) - this is a separate SemVer scheme for the *target* product the scrum
  team manages, which had none before (see `generate_changelog_entry`'s own docstring, which
  versioned entries by sprint number for exactly this reason and pointed at GH-501/this story).
- `propose_version_bump` is a Product Owner tool, alongside `gh_release_create`/`create_release_pr`
  (the existing release-flow tools) - not Architect's, even though Architect is best placed to judge
  whether a given story is a breaking change; Architect records that judgment directly on the story
  itself (`change_type`/`breaking_change`), which `required_change_type` then reads.

## Test Approach
- Unit tests (`agents/scrum_team/tests/test_versioning.py`): `parse_version`/`version_core`
  (valid/invalid shapes), `is_snapshot`/`with_snapshot_suffix`/`strip_snapshot_suffix`,
  `classify_item_change_type` (explicit `change_type`, `breaking_change` flag, type-based default),
  `required_change_type` (highest-severity-wins across multiple Done items, excludes not-yet-Done
  items, None when nothing is Done), `validate_version_increment` (correct/incorrect increment for
  each of breaking/feature/fix, and the no-prior-change-type ordering-only case),
  `validate_snapshot_suffix` (both directions), `propose_version_bump` (accepts a correct proposal,
  rejects a wrong increment, rejects a missing snapshot suffix), `finalize_release_version` (strips
  the suffix, advances the baseline, no-ops when nothing was proposed), `release_version_advisory`
  (None when matching or unset, a message when the backlog's change types outgrew the proposed
  version).
- Integration tests (`agents/scrum_team/tests/test_github.py`): `create_release_pr` finalizes and
  includes the version in the PR body when one was proposed; surfaces `version_warning` without
  blocking when the backlog's change type shifted; behaves identically to pre-US-0025 when no
  version was ever proposed.
