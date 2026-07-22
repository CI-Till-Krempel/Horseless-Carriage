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

## Team performance evaluation

Separate from releasing the *code*, `.github/workflows/eval.yml` automatically
evaluates how well the agent team itself performs, against a fixed scenario, so
regressions or improvements in team behavior surface release over release instead
of only being noticed anecdotally.

- **Fixed scenario**: `eval/scenario/PRODUCT-VISION.md` — a deliberately narrow
  to-do-list-web-app product vision, byte-identical across runs so results are
  comparable across versions. Don't edit it to make a run look better; if the
  scenario genuinely needs to change, that's its own deliberate, explained commit.
- **Isolated public state repo**: `CI-Till-Krempel/horseless-carriage-eval-todo-app`.
  Every run creates a fresh branch (`eval/<version>-run<N>`) rather than touching
  `main`, so runs never contaminate each other or the real target repo you'd use
  for actual work.
- **Driver**: `agents/scrum_team/scripts/run_eval.py` runs the team through 5
  sprints headlessly (no human in the loop) via ADK's `Runner` API directly,
  using the cheap `scrum-eval-cheap` model alias (see `litellm.yaml`) and a
  budget sized for a full run (`--token-budget`/`--usd-budget`, reusing the
  same guardrails from ["Budget Management"](README.md#budget-management)) -
  defaulted per sprint (currently 2,600,000 tokens/$3 per sprint, calibrated
  against a real run that hit 2,070,364 tokens in a single sprint) rather than
  a flat total, since the token check is cumulative for the whole session and
  never resets: one sprint blowing a flat total silently starves every later
  sprint of any further LLM calls.
  On top of that, `--max-duration-minutes` (default 40) is an independent
  wall-clock safety net: if the token/USD guardrails somehow don't stop things
  (a bug, an unexpected model behavior), the run still stops gracefully - it
  writes out whatever's been gathered so far as a real report rather than
  running until the CI job's own hard `timeout-minutes` kills the process with
  no output at all. Verified directly: forcing the deadline to 0 stops the run
  before sprint 1 with `stopped_early: true` and a valid, if empty, report.
  Because there's no human to approve PRs, it auto-merges any PR that opens
  against the eval branch once each sprint's invocation finishes — a deliberate,
  documented simplification of the real "Human Review is mandatory" flow, not a
  silent one. Every branch/PR the team creates during an eval run is tagged
  with the run id (`eval-<run-id>/<branch>`, `[eval-<run-id>]` PR title prefix
  - see `_with_eval_branch_prefix`/`_with_eval_title_prefix` in
  `agents/scrum_team/tools/base.py`), set via `EVAL_RUN_ID` and never present
  in real usage, so branches/PRs from different runs sharing the eval repo
  stay distinguishable and the auto-merge only ever matches PRs actually
  targeting *this* run's branch.
- **Analysis**: `agents/scrum_team/scripts/run_eval_analysis.py` sends the final
  code/specs/sprint-reports to a judge LLM call against a fixed rubric (code
  quality, requirements quality, team efficiency) and writes a report with the
  top problems and suggested fixes, ranked by severity. The report is opened as
  its own small, run-id-tagged PR against the eval branch and self-merged (the
  harness's own concluding action, after the run itself is already done) rather
  than pushed directly, so it goes through the same PR mechanism as everything
  else and shows up as the run's final PR - and is also uploaded as a CI
  artifact.
- **Triggers**: automatically on every `v*.*.*` tag (alongside the real release),
  and manually via `workflow_dispatch` for any branch — useful for checking a
  feature branch's effect on team behavior before merging it.
- **Requires maintainer approval before it runs.** Each run is real LLM spend, so
  the `evaluate` job targets the `eval-approval` GitHub Environment, which has a
  required-reviewer protection rule — regardless of trigger (tag push or manual
  dispatch), the job pauses and a maintainer must explicitly approve it in the
  Actions UI before anything actually executes. Manage reviewers under repo
  Settings → Environments → `eval-approval`.

### Required secrets (you must configure these — I can't provision repo secrets)

`eval.yml` needs, as GitHub Actions repository secrets:
- `GOOGLE_API_KEY`, `LITELLM_MASTER_KEY` — same as local `.env`, for the LiteLLM
  proxy the eval run stands up.
- `EVAL_GITHUB_APP_ID`, `EVAL_GITHUB_APP_PRIVATE_KEY`, `EVAL_GITHUB_APP_INSTALLATION_ID`
  — a GitHub App installed on the eval repo (**not** necessarily the same App used
  for real target repos) with `Contents` + `Pull requests: Read & write`. The
  installation must specifically include
  `CI-Till-Krempel/horseless-carriage-eval-todo-app` under "Repository access" —
  a GitHub App's own repo-scoped permissions don't extend to new repos
  automatically (this bit me during development: the eval repo returned a real
  403 until I added it to the installation by hand).

### Known limitations (found via real testing, not fixed here — out of scope)

- `litellm.yaml`'s production model aliases (`scrum-po`, `scrum-dev`, etc.) all
  point at `gemini-1.5-pro`, which 404s as a retired model against a live Gemini
  API key today. `scrum-eval-cheap` was deliberately pointed at a model confirmed
  working (`gemini-flash-lite-latest`) instead of reusing a production alias —
  the production aliases need their own follow-up fix.
- `create_litellm_virtual_key()` doesn't handle "key alias already exists"
  gracefully (a real LiteLLM 400 if the same agent name's key was already
  created in that LiteLLM database) — harmless for a fresh eval run's fresh `db`/
  `litellm` containers, but a real gap if a long-lived LiteLLM database is reused
  across many setup attempts.
- The cheap model is not fully reliable at autonomous multi-step execution — it
  sometimes announces a next action ("Next actions: transfer to X") without a
  tool call actually doing it in the same turn. `run_eval.py` sends a bounded
  number of "continue" nudges to recover from this, but a run can still end
  without a sprint report if the model doesn't recover within that budget. This
  is itself a legitimate signal about team reliability, not just a harness bug.

## Non-goals

- No Docker image publishing / container registry.
- No deployment automation (per "no deployments").
- No changes to the target-product release flow (`create_release_pr`,
  `gh_release_create`) beyond what EP-0004 already plans.
- No branch protection rules automation — set up manually in GitHub repo settings if
  desired, not part of this doc.
