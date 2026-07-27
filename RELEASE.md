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
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install host-script test dependencies
        run: pip install pytest pyyaml
      - name: Run tests
        run: python3 run_tests.py
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

## Story workflow

Real eval runs repeatedly showed the same failure pattern: stories left with placeholder/empty
content but marked "Done" anyway, and `specs/ROADMAP.md` never reflecting completed work - because
the only enforcement was prompt text asking the agents nicely to follow a checklist, and a cheap
model under budget pressure just... didn't, reliably. See docs/ARCHITECTURE.md "Story workflow" for the full
human-facing writeup (the stage table, the checklist mapping in `spec-templates/DOD.md`/`DOR.md`);
this section is the operational summary the agent prompts themselves point back to.

Every story passes through exactly 5 stages, in this exact order, no skipping: **Ready**
(Product Owner, supported by Architect) → **Implemented** (Dev Team) → **Reviewed** (Architect) →
**Tested** (QA) → **Accepted** (Product Owner). `STORY_STAGES`/`STAGE_OWNERS`
(`agents/scrum_team/helpers.py`) are the source of truth for the stage list and ownership.

`advance_story_stage(title_or_id, stage)` (`agents/scrum_team/tools/requirements.py`) is the only
way a stage is marked complete, and it enforces, in code:
- **Order**: rejects the call if the stages before `stage` aren't complete yet.
- **Ownership**: rejects the call if `tool_context.agent_name` isn't that stage's owner.
- **One story at a time**: `product_backlog` list order is priority order; a story can't advance
  past Ready until the immediately-preceding story (`_preceding_story`, skipping Epics) has reached
  Accepted.
- **Content quality**: for Ready and Accepted/legacy-Done, `_story_readiness_issues` refuses the
  write if title/user story/acceptance criteria are missing or still placeholder/blank text - the
  same check `create_from_template`/`upsert_adr` already apply to templates themselves (see
  `_strip_agent_safeguard_comments` in `agents/scrum_team/tools/docs.py`), now applied to content
  quality, not just leftover template markup.
- **No bypass**: `upsert_story`/`upsert_epic`/`plan_sprint_backlog_item` refuse to set `status`
  directly to any of the 5 stage names *or* a legacy done-synonym ("Done"/"completed"/"closed" -
  `_story_stages_completed`'s read-side backward compat treats any of those as every stage complete,
  so setting one directly is an equally complete bypass, just spelled differently) - see
  `blocks_direct_status_set` in `agents/scrum_team/helpers.py`. Only `advance_story_stage` can set
  status this way, since it's the only path that actually enforces the above.

`advance_story_stage` re-renders `specs/ROADMAP.md`'s per-stage checkboxes for that story via
`_sync_roadmap_for_story`/`update_roadmap` in the same call that marks a stage complete - and its
own top-level `status` reflects whether that sync (and the story markdown rewrite) actually
succeeded, not just whether the in-state stage change did. Reporting "ok" while the roadmap update
silently failed would be exactly the kind of gap this mechanism exists to close. Beyond that,
there's no longer a separate "now go tell the roadmap" step for a story once it's moving through
stages at all.

### Sprint retrospective enforcement

A real eval run (0.1.0-run8) completed all 5 sprints - every story was implemented, reviewed,
tested, and accepted correctly - yet the Scrum Master role was never invoked even once across the
whole run: every `transfer_to_agent` call targeted ProductOwner, DevTeam, Architect, or QA. The
Orchestrator's prompt said the retrospective was mandatory and listed it as the last step of the
sprint-close sequence, but nothing actually stopped the sprint from "closing" without it - it was
simply the easiest step to skip, so it always got skipped, in 5 out of 5 sprints.

`create_sprint_report` (`agents/scrum_team/tools/budget.py`) now mechanically refuses to write a
report unless a *new* retro action or impediment has been logged since the last successful report:
`len(retro_actions) + len(impediment_log)` is compared against a `retro_baseline` snapshot taken
the last time the check passed, so a stale entry from three sprints ago can't trivially satisfy
this sprint's requirement forever - `add_retro_action`/`add_impediment` must produce something new,
every sprint. On success the baseline is updated to the new total. The rejection message tells the
caller (Product Owner) exactly what's missing and to transfer to Scrum Master first - turning a
skippable prompt instruction into a hand-off the tooling itself forces.

### Human interaction levels

`record_human_approval`'s two gates (`advance_story_stage(..., "Implemented")` and
`create_release_pr`, see ISSUE-0001 above) originally hard-coded exactly two approval types:
`"sprint"` before implementing, `"release"` before releasing. That's the right shape for a human
embedded as Product Owner or acting as a business stakeholder, but it doesn't fit every real usage:
a CEO-level sponsor who only cares about spend shouldn't have to review every sprint's backlog to
unblock the team, and a fully automated evaluation run (`run_eval.py`) has no human to call
`record_human_approval` at all - which is exactly why `run_eval.py`'s scripted messages had to
socially-engineer the model into calling it based on prompt text alone ("treat the sprint as
pre-approved"), rather than that being mechanically true.

`INTERACTION_LEVEL` (`agents/scrum_team/helpers.py`'s `get_interaction_level()`, see
`docs/INTERACTION-LEVELS.md`) generalizes the two hard-coded gates into a level-driven lookup
(`required_pre_implementation_approval`/`required_pre_release_approval`) instead:

| Level | Pre-implementation | Pre-release |
|---|---|---|
| Product / Stakeholder | `"sprint"` | `"release"` |
| CEO | `"budget"` | none |
| EVAL | none | none |

`record_human_approval` itself gained a third `approval_type`, `"budget"`, for the CEO level - no
new state field was needed: the existing `sprint_approval_baseline`/`release_approval_baseline`
"must be NEW since last time" snapshots (in `create_sprint_report`/`create_release_pr`) now compare
against whichever type the active level actually requires, computed fresh from the environment each
time rather than persisted - a misconfigured or unset `INTERACTION_LEVEL` falls back to `Product`,
the most-supervised level, instead of silently disabling every gate. `run_eval.py` now sets
`INTERACTION_LEVEL=EVAL` explicitly, so the "no human in the loop" property of an eval run is
mechanically guaranteed rather than resting entirely on the model obeying scripted prompt text.

`create_sprint_report` also branches on level via `report_detail_level()`, rather than always
rendering the same unconditional content: `full` (Product, EVAL) keeps everything, `business`
(Stakeholder) drops per-agent token usage and transcript excerpts, `executive` (CEO) renders budget
and headline outcomes only. This is in the report-generation code itself, not left to the PO agent's
prompt-following, so a human at a given level gets a consistently-shaped report regardless of how
that call happened to be phrased - and no data is silently dropped: an omitted section is still named
explicitly under a "Full Process Detail" heading, pointing at `.hc/state.json` where it still lives
in full.

### Tool dispatch resilience (hallucinated/disallowed tool calls)

A real eval run (2026-07-24, GitHub Actions run 30099595847) crashed outright: ProductOwner called
`write_file`, a tool only DevTeam/QualityGuardian actually have (see each `LlmAgent`'s `tools=[...]`
in `agents/scrum_team/agent.py`) - ADK's own dispatch code doesn't route that to the tool function at
all, it raises a bare `ValueError` ("Tool 'write_file' not found...") that propagates all the way up
through `runner.run_async` and kills the whole process, discarding every sprint completed so far in
that run. A single hallucinated tool name from one sub-agent, out of dozens of calls across a
multi-sprint run, should not be fatal to the entire session.

ADK (2.4.0+) provides exactly the hook for this: `LlmAgent(on_tool_error_callback=...)`. When tool
dispatch fails because the name isn't found for the calling agent at all, ADK synthesizes a
placeholder `BaseTool(name=<hallucinated name>, description="Tool not found")` before invoking this
callback (see `google.adk.flows.llm_flows.functions._execute_single_function_call_async`); if the
callback returns a dict instead of `None`, that dict becomes the tool's function-response - the model
sees an ordinary tool-error message and can recover (try a real tool, or `transfer_to_agent`) instead
of the process aborting. `on_tool_error_callback` (`agents/scrum_team/agent.py`) checks for exactly
that `"Tool not found"` placeholder (not string-matching the exception text, which could change
across ADK versions) and returns a message naming the tool, the calling role, and the two ways to
recover. It's registered on every `LlmAgent` - all six specialists via `COMMON_AGENT_CALLBACKS`, and
`root_agent` directly (it defines its own callback lists rather than using the shared dict). A
genuine exception raised *inside* a real tool call (its actual description, not the placeholder) is
left alone and still propagates - this only softens dispatch-time "tool not found" errors, not real
bugs.

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
  for actual work. This branch is pushed to the remote immediately after being
  created, before the team does anything - `gh_pr_create`/`create_release_pr`
  default their PR `base` to it, and `gh pr create --base <branch>` fails
  outright if that branch doesn't exist on the remote yet. Without this,
  0.1.0-run4 produced feature branches but zero PRs for the whole run - silently,
  since `create_release_pr` used to always report `"status": "ok"` regardless of
  whether the underlying push/PR-create actually succeeded (also fixed).
- **Driver**: `agents/scrum_team/scripts/run_eval.py` runs the team through 5
  sprints headlessly (no human in the loop) via ADK's `Runner` API directly,
  using the cheap `scrum-eval-cheap` model alias (see `litellm.yaml`) and a
  budget sized for a full run (`--token-budget`/`--usd-budget`, reusing the
  same guardrails from ["Budget Management"](docs/BUDGET.md)) -
  defaulted per sprint from `EVAL_SPRINT_TOKEN_BUDGET`/`EVAL_SPRINT_USD_BUDGET`
  in `.env` (currently 2,600,000 tokens/$3 per sprint, calibrated against a
  real run that hit 2,070,364 tokens in a single sprint) rather than a flat
  total, since the token check is cumulative for the whole session and never
  resets: one sprint blowing a flat total silently starves every later sprint
  of any further LLM calls.
  On top of that, `--max-duration-minutes` (default 40) is an independent
  wall-clock safety net: if the token/USD guardrails somehow don't stop things
  (a bug, an unexpected model behavior), the run still stops gracefully - it
  writes out whatever's been gathered so far as a real report rather than
  running until the CI job's own hard `timeout-minutes` kills the process with
  no output at all. Verified directly: forcing the deadline to 0 stops the run
  before sprint 1 with `stopped_early: true` and a valid, if empty, report.
  **Local runs only** (`GITHUB_ACTIONS` unset): before spending anything, the
  script checks that the LiteLLM proxy is actually reachable, not just
  configured - the USD guardrail above lives entirely in the proxy (see
  docs/BUDGET.md) and silently does not apply without it. If
  it's not reachable, the script prints a loud warning and refuses to proceed
  unless `--dev-mode` is passed, acknowledging that only the local token-count
  guardrail is protecting the run. `eval.yml`'s CI job always brings the proxy
  up and waits for `/health/readiness` first, so this never triggers there and
  `--dev-mode` is never needed in CI.
  Because there's no human to approve PRs, it auto-merges any PR that opens
  against the eval branch once each sprint's invocation finishes — a deliberate,
  documented simplification of the real "Human Review is mandatory" flow, not a
  silent one. Before merging each sprint's PR(s), it posts that sprint's full
  raw agent activity log (every event's author/text and each tool call's actual
  arguments/response, not just the tool name - a cheap model often makes a
  tool call with little or no accompanying free text, so name-only logging
  looked like "just tool calls, no conversation" when the real substance - PR
  comment bodies, code passed to `write_file`, etc. - was one field over the
  whole time; see `_run_one_sprint`) as a PR comment via
  `_format_sprint_transcript`/`_post_sprint_transcript`, capped at
  `MAX_TRANSCRIPT_CHARS` with a pointer to the full, untruncated version. That
  full version - every sprint's complete transcript, all events, no cap - is
  written to `transcript.md` and uploaded as its own CI artifact (see
  `_format_full_transcript`, eval.yml's "Upload report artifact" step) rather
  than only ever existing inside the run manifest. Every branch/PR
  the team creates during an eval run is tagged with the run id
  (`eval-<run-id>/<branch>`, `[eval-<run-id>]` PR title prefix - see
  `_with_eval_branch_prefix`/`_with_eval_title_prefix` in
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
  artifact. It then opens a second, run-id-tagged PR from the whole eval branch
  (by now containing every sprint's merged work plus that report commit)
  against the eval repo's actual default branch, as a single place to review
  everything the run produced - and deliberately leaves it **open, never
  merged** (title says so explicitly), since merging an eval run into the eval
  repo's real default branch would defeat the point of keeping eval runs
  isolated. See `_open_overview_pr`.
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
