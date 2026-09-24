# Issue

- Issue ID: ISSUE-0050
- Title: Cross-Sprint State-Persistence Gap Regressed US-0001 To "Implemented", Deadlocking Sprint 2's One-Story-At-A-Time Gate
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-09-24

## Overview
Reported (maintainer, reviewing `0.1.0-run33`): Sprint 2 blew its token budget by 20% (6,032,161 /
5,000,000) and `EVAL-REPORT.md` flagged "strict sequential gating caused total pipeline lockup" and
"Stories US-0003 and US-0004 were implemented in code and tested, but blocked from advancing through the
mandatory pipeline stages due to strict sequential stage gating on US-0002" as a top problem. Separately,
`EVAL-REPORT.md`'s Requirements Quality section flagged story states as "inconsistently updated" versus
actual code delivery.

Investigated directly against the real run (`gh run view 35974931711`, its `manifest.json`/`transcript.md`
artifacts, and the eval repo's actual git history at `horseless-carriage-eval-todo-app`,
`eval/0.1.0-run33/develop`/`main`).

**Root cause, confirmed via git history + parsed state:** `advance_story_stage`
(`agents/scrum_team/tools/requirements.py`) enforces, by design (see `docs/ARCHITECTURE.md`, "a story can't
advance past READY until the story immediately above it has reached ACCEPTED"), that development
(Implemented onward) happens one story at a time, top to bottom, in `product_backlog` priority order - the
immediately-preceding story must already be `Accepted`.

Sprint 1's own final in-session state had US-0001 fully `Accepted` (all six `STORY_STAGES` complete,
confirmed in `manifest.json`'s sprint-1 `sprint_backlog`). But `.hc/state.json`'s **last commit that ever
reached the shared remote** (`eval/0.1.0-run33/develop`, commit `1e73d50d`, "chore: update roadmap and
story stage for US-0001", pushed ~08:29:24, ~1 minute into an ~7-minute run) recorded US-0001 only as far as
`Implemented`:
```
$ gh api "repos/.../contents/specs/stories/US-0001-Create-To-Do-List.md?ref=1e73d50d"
- Status: Implemented
```
No later commit ever updated this file or `.hc/state.json` on the shared branch again - confirmed via
`gh api .../commits?path=.hc/state.json&sha=eval/0.1.0-run33/develop`, which shows exactly two checkpoint
commits, both within the same minute, both at the very start of Sprint 1. `save_state_to_repo`
(`agents/scrum_team/tools/scrum.py`) commits `.hc/state.json` locally on every single story-stage
transition via `_checkpoint_state_commit` - but that function's own docstring is explicit: "**Never
pushes** ... this is a purely local safety net." Nothing else in the eval harness's flow reliably pushes
`.hc/state.json` forward after a story's real progress (Reviewed/Tested/Accepted, sprint-report/release
activity) - by contrast, real/interactive usage keeps one long-lived local clone across a whole engagement,
so a purely-local commit is far less likely to matter; the eval harness's own `run_eval.py` additionally
re-syncs the local clone against the *remote* branch between sprints
(`_sync_local_clone_to_branch`: `git fetch` + `git checkout` + `git pull --ff-only`), which is exactly the
kind of operation that can leave locally-committed-but-never-pushed progress stranded or overwritten.

At the start of Sprint 2, `init_scrum_state()` re-syncs `product_backlog`/`sprint_backlog` from whatever's
actually on disk in the (freshly re-synced) local clone (`load_state_from_repo` + `sync_stories_from_markdown`,
both in `agents/scrum_team/tools/scrum.py` / `requirements.py`) - which, per the above, still only knew
US-0001 as `Implemented`. `advance_story_stage`'s one-at-a-time gate then correctly (per its own rule) but
wrongly (per the team's real, already-shipped work) refused to let US-0002 - and everything behind it in
priority order, including US-0003/US-0004 - advance past `Ready`, since the "immediately-preceding story"
(US-0001) had, as far as any persisted state on the shared branch could show, never reached `Accepted`.
DevTeam/Architect/QA had already done real implementation and test work on US-0003/US-0004 before hitting
this wall (confirmed in `manifest.json`'s sprint-2 backlog: both show real `tasks`/`code_files`/`test_approach`
content), so the team spent the rest of Sprint 2's budget on blocked retries and cross-agent transfers
trying to route around a gate that, from its own state's point of view, was correctly refusing an
out-of-order advance.

This is very likely also implicated in Sprint 3's near-instant re-exhaustion (ISSUE-0049's own transcript
evidence): several agents' `token_usage.agents` entries moved in ways inconsistent with simple
accumulation between Sprint 2 and Sprint 3 (two agents' recorded totals *decreased*, which a monotonic
per-call counter cannot do on its own) - consistent with `init_scrum_state()`'s `load_state_from_repo` step
reloading a stale, non-final snapshot of shared state at a sprint boundary, the same mechanism responsible
for the US-0001 regression above. This part is not yet conclusively traced to a single git operation (see
Notes) and is called out here as a strong lead for the fix's own verification step, not a separately
re-litigated root cause.

## Acceptance Criteria
- A story's real, persisted stage/status can no longer regress between sprints in an eval run: after a
  sprint that advanced a story all the way to `Accepted`, the very next sprint's `init_scrum_state()` must
  observe that story as `Accepted` too - not an earlier stage.
- Concretely, one (or a documented combination) of:
  - The eval harness explicitly pushes the local clone's current branch (or at minimum `.hc/state.json`
    and any touched `specs/` files) before `_sync_local_clone_to_branch` re-syncs against the remote at a
    sprint boundary, so no local-only progress is ever at risk of being stranded or overwritten; or
  - `_sync_local_clone_to_branch`'s `git pull --ff-only` fails loudly (surfaced in the manifest/transcript,
    not just a silently-swallowed non-zero exit) whenever local and remote have diverged, so this failure
    mode is visible in the run's own artifacts instead of only discoverable via manual git archaeology; or
  - `save_state_to_repo` (or a harness-only wrapper around it) pushes in the eval harness's specific
    execution model, where - unlike real/interactive usage - the harness itself is already responsible for
    the local clone's entire lifecycle and no human is present to notice or push on its own.
- A regression test (or a documented, deliberately-scripted local repro using `--dev-mode`) demonstrates a
  story reaching `Accepted` in one sprint, a sprint-boundary re-sync, and `init_scrum_state()` still
  observing `Accepted` (not a stale earlier stage) at the start of the next sprint.

## Notes
- The exact git operation that stranded US-0001's later transitions was not pinned down to a single command
  in this investigation - `_checkpoint_state_commit`'s "never push" design explains the underlying gap
  unambiguously, but confirming precisely how `_sync_local_clone_to_branch`'s `fetch`/`checkout`/
  `pull --ff-only` sequence interacts with locally-committed-but-unpushed progress (silently no-ops on
  local-ahead, refuses loudly on genuine divergence, or something else) needs a live, instrumented local
  repro (`--dev-mode`, inspecting `git log`/`git status` in the local clone at each sprint boundary) before
  landing a fix - this issue documents the observed effect and proposes fix shapes, not a verified-to-the-line
  git diagnosis.
- The one-story-at-a-time sequential gate itself (`docs/ARCHITECTURE.md`) is a deliberate design choice, not
  the bug - it is working exactly as written. This issue's fix target is the state it was reasoning over
  being wrong, not the rule itself. A secondary, more defensive option worth considering separately: give
  `advance_story_stage` a self-healing check when a story's own commit/PR history (e.g. a merged
  `story-spec` or release PR referencing it) contradicts its freshly-reloaded `stages_completed` - similar
  in spirit to the existing `state_json_corrupted` recovery path for a different failure shape - so an
  unattended run has some chance of noticing and recovering from this class of contradiction without a
  human in the loop. Not proposed as this issue's Acceptance Criteria since it treats a symptom (a
  contradiction, once already present) rather than the underlying persistence gap.
- Two of Sprint 3's agents (`ScrumMaster`, `QualityGuardian`) show *lower* `token_usage.agents` totals than
  Sprint 2's end, despite neither agent taking a single turn in Sprint 3 (confirmed: all 26 real events in
  `transcript.md`'s Sprint 3 log are authored by `ProductOwner`) - impossible under simple accumulation, and
  consistent with `init_scrum_state()` reloading a stale, pre-Sprint-2-end snapshot of `token_usage` from
  the same stranded/overwritten state described above, not a fresh, correctly-reset `{}`. Flagged as a lead
  for verifying this issue's fix, not claimed as independently proven.

## Test Approach
- A scripted local repro (`--dev-mode`, 2-sprint run against a scratch eval-repo fork) that advances a
  story to `Accepted` in sprint 1, then asserts sprint 2's `init_scrum_state()` doc_sync/backlog reflects
  `Accepted`, not an earlier stage.
- Unit coverage on whichever concrete mechanism the fix lands on (e.g. a harness-side push step, or a
  loud-failure assertion on `_sync_local_clone_to_branch`'s pull step) mirroring the existing
  `agents/scrum_team/tests/test_run_eval.py` mocking patterns (`_FakeSession`/`_FakeSessionService`-style
  fakes, no real git/network calls).
- Full `agents/scrum_team/tests` suite, no regressions, once a fix lands.

## Resolution
Not yet implemented - proposed fix shapes are listed under Acceptance Criteria above, pending a decision
on which (or which combination) to land, informed by the live local repro called out in Notes.
