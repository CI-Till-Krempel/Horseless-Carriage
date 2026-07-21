# Release Process

This document describes the release process for Horseless Carriage itself (this
repo/tool). It does **not** change how Horseless Carriage releases the *target*
products it manages for users (`create_release_pr`, `gh_release_create`, the upcoming
EP-0004 changelog work) — those stay as-is. See [Scope](#scope) below.

## Scope

Two distinct things both use the word "release" in this codebase; this document only
covers the first one:

1. **Releasing Horseless Carriage itself** — tagging a version of *this* repo
   (`CI-Till-Krempel/Horseless-Carriage`) so users know what they're running. This is
   what this document sets up.
2. **Releasing a target product** that the Scrum agents manage on a user's behalf —
   already exists via `create_release_pr()` / `gh_release_create()` in
   `agents/scrum_team/tools/github.py`, and will grow a changelog step per
   `specs/implementation-plans/IP-0004-Changelog-Generation.md`. Not touched here.

The one place these connect: we record *which version of Horseless Carriage* was used
to run a sprint inside the target repo's own state, so a sprint report is traceable
back to the tool version that produced it (see
[Tracking the HC version in the state repo](#tracking-the-hc-version-in-the-state-repo)).

## Versioning

[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`):
- **MAJOR** — breaking change to agent tool contracts, `ScrumState` shape, or CLI/env
  interface that requires user action.
- **MINOR** — new capability, backward-compatible (new tool, new story/epic delivered).
- **PATCH** — bug fix, no behavior/contract change.

The roadmap (`specs/ROADMAP.md`) already plans work in `v0.1`, `v0.2`, ... waves. Those
become the actual git tags/releases: **`v0.1.0` is our first release**, cut from `main`
once EP-0001–EP-0003 (all currently Done) are tagged.

Note: pre-1.0, per SemVer, `0.x.y` is explicitly "anything may change at any time" —
MINOR bumps can include breaking changes until we cut `v1.0.0`. Flagging so it's a
conscious choice, not an oversight.

## Branching model — GitFlow

- **`main`** — always reflects the latest released version. Every commit on `main` is
  tagged. Protected: no direct pushes, only merges from `release/*` or `hotfix/*`.
- **`develop`** — integration branch for the next release. Default base branch for new
  work, **starting after `v0.1.0` ships** (see [Rollout](#rollout)) — until then,
  feature branches keep targeting `main` exactly as today.
- **`feature/*`** — as already used (`feature/us-NNNN-<slug>`), branched from `develop`,
  PR'd back into `develop` (once `develop` exists — see Rollout).
- **`release/vX.Y.Z`** — cut from `develop` when preparing a release. Only version-bump
  / release-note fixups happen here, no new features. Merged into `main` (tagged) *and*
  back into `develop`.
- **`hotfix/vX.Y.Z`** — cut from `main` for an urgent fix that can't wait for the next
  `develop` cycle. Merged into `main` (tagged) *and* `develop`.

```
main      ──●────────────●────────────●──   (tags: v0.1.0, v0.1.1, v0.2.0)
             \          / \          /
release/*     ●──●──●──●   ●──●──●──●
             /              \
develop   ──●────●────●────●─●────●────●──
             \    \    \        /
feature/*     ●────●    ●──────●
```

## Release procedure

1. When `develop` has everything intended for the release, cut `release/vX.Y.Z` from
   `develop`.
2. Bump `VERSION` (see [Where the version lives](#where-the-version-lives)), update
   `CHANGELOG.md` if `EP-0004` has landed by then, open a PR `release/vX.Y.Z → main`.
3. On merge to `main`, tag the merge commit `vX.Y.Z` and push the tag.
4. Pushing the tag triggers the GitHub Action below, which publishes the GitHub
   Release. No manual `gh release create` needed.
5. Merge `release/vX.Y.Z` back into `develop` (or fast-forward `develop` to `main` if
   nothing diverged) so the tag's history isn't lost on the next cycle.

## GitHub Action

New workflow, `.github/workflows/release.yml`, minimal since there's no build/deploy
artifact to publish:

```yaml
name: Release

on:
  push:
    tags:
      - "v*.*.*"

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: |
          chmod +x run_tests.sh
          ./run_tests.sh
      - name: Publish GitHub Release
        run: gh release create "${{ github.ref_name }}" --generate-notes
        env:
          GH_TOKEN: ${{ github.token }}
```

- Trigger: any tag matching `v*.*.*` pushed (e.g. from step 3 above).
- Re-runs the test suite before publishing — belt-and-suspenders, since `main` should
  already be green from the `release/*` PR's CI run, but a tag can in principle be
  pushed independent of a PR merge.
- `--generate-notes` uses GitHub's auto-generated release notes (commits/PRs since the
  last tag) — no changelog-authoring effort required. Can be swapped for a real
  `CHANGELOG.md` excerpt once EP-0004 exists.
- No Docker image build/push, no deployment step — matches "no deployments, minimal
  effort".

## Tracking the HC version in the state repo

A new field on `ScrumState` (`agents/scrum_team/state.py`), distinct from the existing
`version` field (which is a *state-schema* version, frozen at `"1.0.0"` since day one
and unrelated to this):

```python
hc_version: str = "unknown"
```

- Added to `REPO_STATE_KEYS` (`agents/scrum_team/tools/scrum.py`) so it's persisted to
  the target repo's `.hc/state.json` alongside everything else.
- Set on every `init_scrum_state()` call to the *currently running* version (not
  `setdefault` — it should always reflect the version that actually ran this session,
  overwriting whatever was previously persisted).
- Surfaced in `create_sprint_report()` (`agents/scrum_team/tools/budget.py`): a line
  under the report title, `**Generated by Horseless Carriage vX.Y.Z**`.

### Where the version lives

**Committed `VERSION` file at repo root**, plain text (`0.1.0`), read at runtime.
Bumped as part of the `release/*` PR (step 2 above). Simple, human-readable, no git
dependency at runtime, works identically in and out of Docker — the container's final
stage doesn't need `.git` copied in, and there's no build-arg plumbing to maintain.

## Migration scaffold

No breaking `ScrumState` changes exist yet, but the hook point is built now so the
*next* one doesn't have to invent this from scratch. New file
`agents/scrum_team/tools/migrations.py`:

```python
# Each entry: the version whose *shape* the migration fixes up TO.
# Applied in order for any state whose recorded hc_version is older.
MIGRATIONS: list[tuple[str, Callable[[dict], dict]]] = [
    # ("0.2.0", _migrate_to_0_2_0),
]

def migrate_state(state: dict, from_version: str) -> dict:
    """Applies any migrations newer than from_version, in order."""
    for target_version, fn in MIGRATIONS:
        if _version_lt(from_version, target_version):
            state = fn(state)
    return state
```

Wired into `load_state_from_repo()` (`agents/scrum_team/tools/scrum.py`): after reading
`.hc/state.json`, compare its recorded `hc_version` against the current one and run
`migrate_state` before merging into `tool_context.state`. `MIGRATIONS` starts empty, so
this is a no-op today — pure scaffolding, deliberately not solving a migration that
doesn't exist yet.

## Rollout

`v0.1.0` ships first, from current `main`, with **no** workflow change — `develop`
doesn't exist yet, so this release is cut directly on `main`. Once `v0.1.0` is tagged:

1. Create `develop` from the `v0.1.0` tag.
2. Future feature-branch PRs target `develop` instead of `main`.
3. `main` becomes merge-only from `release/*`/`hotfix/*` from that point on.

This avoids introducing branch-workflow churn in the same change as the first-ever
release.

## Non-goals

- No Docker image publishing / container registry.
- No deployment automation (per "no deployments").
- No changes to the target-product release flow (`create_release_pr`,
  `gh_release_create`) beyond what EP-0004 already plans.
- No branch protection rules automation — set up manually in GitHub repo settings if
  desired, not part of this doc.
