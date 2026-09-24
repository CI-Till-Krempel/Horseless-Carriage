# Issue

- Issue ID: ISSUE-0050
- Title: `_checkout_develop_or_recover`'s Reset-To-Origin Checkout Silently Discarded Local-Only Story Progress
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-09-24

## Overview
Reported (maintainer, reviewing `0.1.0-run33`): Sprint 2 blew its token budget by 20% (6,032,161 /
5,000,000) and `EVAL-REPORT.md` flagged "strict sequential gating caused total pipeline lockup" and
"Stories US-0003 and US-0004 were implemented in code and tested, but blocked from advancing through the
mandatory pipeline stages due to strict sequential stage gating on US-0002" as a top problem. This issue
was originally filed (Ready, not yet implemented) documenting the effect and proposing fix shapes without a
confirmed git-level root cause. **A second real run (`0.1.0-run34`) reproduced the identical failure shape**
(`manifest.json`'s Sprint 3->4 boundary: US-0001 regressed `Accepted` -> `Implemented`, US-0002 regressed
`Accepted` -> `Ready` with `stages_completed` gone entirely), which is what prompted pinning down the exact
mechanism below and landing a real fix rather than further proposals.

**Root cause, now confirmed via a live git repro (not just artifact archaeology):** `advance_story_stage`
(`agents/scrum_team/tools/requirements.py`) enforces, by design (see `docs/ARCHITECTURE.md`), that
development (Implemented onward) happens one story at a time, top to bottom, in `product_backlog` priority
order - the immediately-preceding story must already be `Accepted`. This rule is not the bug; the state it
reasons over was.

`save_state_to_repo` (`agents/scrum_team/tools/scrum.py`) commits `.hc/state.json` locally on every single
story-stage transition via `_checkpoint_state_commit` - deliberately local-only ("Never pushes ... this is a
purely local safety net"). Meanwhile, `_checkout_develop_or_recover` (`agents/scrum_team/tools/github.py`) -
the shared `git fetch` + `git checkout -B <develop> origin/<develop>` helper used by
`create_story_spec_pr`/`create_sprint_backlog_pr`/`start_feature_branch`/`create_release_pr`, i.e. called
very frequently, at least once per story spec, once per sprint backlog, every feature branch, and every
release attempt - unconditionally resets the local `develop` branch to match `origin/develop`.
`git checkout -B <branch> <start-point>` is a hard reset when `<branch>` already exists: any commits on the
current local `develop` that aren't reachable from `origin/develop` become unreachable garbage the instant
this runs, with no error, no warning surfaced anywhere in the tool's return value - `git` itself only prints
`Reset branch 'develop'` to stdout, which nothing here ever inspected.

**Live repro** (`$TMPDIR/git-repro`, a bare "remote" + two local clones - full transcript in this issue's
git history/PR):
1. A persistent local clone (standing in for the one the eval harness/a real engagement keeps across a
   whole run) makes three local-only `save_state_to_repo`-style checkpoint commits on `develop` -
   `Implemented` -> `Reviewed` -> `Accepted` - none ever pushed.
2. Meanwhile a *separate* clone simulates `create_story_spec_pr`'s real flow: a story-spec branch, pushed,
   merged into `develop` **server-side** via `gh pr merge` (this never touches the persistent local clone at
   all - GitHub does the merge on its own servers).
3. Now local `develop` and `origin/develop` have genuinely diverged: local has 3 commits origin doesn't;
   origin has 1 commit (the story-spec merge) local doesn't.
4. Running `_sync_local_clone_to_branch`'s exact sequence (`fetch` + `checkout` + `pull --ff-only`) against
   this: the `--ff-only` pull **fails loudly** (exit 128, "Not possible to fast-forward") and - confirmed by
   inspecting the file afterward - **does not touch `state.json` at all**. This function was a red herring;
   it fails safe.
5. Running `_checkout_develop_or_recover`'s actual checkout instead - `git checkout -B develop
   origin/develop` - against the same diverged clone: `git` prints `Reset branch 'develop'` and
   **`state.json` instantly reverts from `"v4 (Accepted)"` to `"v1 (init)"`** - the 3 local-only checkpoint
   commits are gone from any branch tip, unreachable.

This is not eval-harness-specific and not sprint-boundary-specific - `_checkout_develop_or_recover` runs
inside ordinary story/sprint/release tooling used identically in real/interactive engagements. It fires
constantly relative to how often a story actually reaches `Accepted` without an intervening real push, which
is why both `0.1.0-run33` and `0.1.0-run34` hit it, at different points in their respective runs.

## Acceptance Criteria
- `_checkout_develop_or_recover`'s reset-to-origin checkout never silently discards local-only commits on
  `develop`.
- The common case (local strictly ahead of origin, nothing else changed) is a clean push before the
  checkout, making the checkout a true no-op.
- The harder case (local and origin have genuinely diverged, as in the live repro above) is reconciled via a
  merge, then pushed - still a no-op checkout afterward, and still zero data loss, as long as the merge is
  clean (verified in the repro: a story's own state vs. another story's brand-new spec file never actually
  conflict on content).
- The rare case a real merge conflict can't be auto-resolved safely does not regress past the previous
  (silent) behavior - it is now a **logged, recoverable** loss (a pushed rescue branch + a `decision_log`
  entry naming it) instead of a silent one, and the original reset-checkout still proceeds so callers aren't
  newly blocked by an unresolved conflict.
- Regression tests cover: no local branch yet (no-op), local not ahead (no-op), clean fast-forward (pushed),
  genuine divergence with a clean merge (merged + pushed), a real conflict (rescued + logged), and a merge
  that succeeds locally but fails to push (left un-synced, caller's checkout unchanged from before this fix
  - a real network/auth problem still needs a human either way).

## Notes
- The one-story-at-a-time sequential gate itself is unchanged and correct - it was always reasoning
  correctly over whatever state it was given; that state is now trustworthy.
- `_sync_local_clone_to_branch` (`run_eval.py`, eval-harness-only) turned out not to be implicated at all -
  its `pull --ff-only` fails loudly and leaves the working tree untouched on divergence, confirmed directly.
  Left as-is.
- A more defensive, symptom-side option considered and **not** taken: teaching `advance_story_stage` to
  self-heal when a story's own commit/PR history contradicts its freshly-reloaded `stages_completed`. Not
  needed once the actual persistence gap is closed at its source - the state it reloads is no longer wrong in
  the first place.
- `0.1.0-run34`'s Sprint 3->4 regression additionally coincided with `create_release_pr` failing on every
  single attempt that run for an unrelated reason (see ISSUE-0051) - every one of those failed attempts still
  ran `_checkout_develop_or_recover` first, repeatedly re-triggering this exact discard. Fixing ISSUE-0051
  reduces how often this gets triggered in practice, but this issue's fix is what actually prevents the data
  loss itself regardless of how often the caller retries.

## Test Approach
- `agents/scrum_team/tests/test_github.py::TestPreserveLocalOnlyDevelopCommits` - the six cases listed under
  Acceptance Criteria, pure `_run` mocking (no real git calls), mirroring this file's existing style.
- `agents/scrum_team/tests/test_github.py::TestCheckoutDevelopOrRecover` - two new cases confirming the
  wiring: a plain (non-reset) checkout once preservation confirms `in_sync`, and the original reset-checkout
  still runs when preservation couldn't sync (rescued case).
- The three pre-existing `TestCheckoutDevelopOrRecover` cases (self-heal on "would be overwritten", no retry
  on an unrelated failure, give up if nothing to integrate) updated to patch
  `_preserve_local_only_develop_commits` as a no-op `None` - they test a different concern, now-orthogonal to
  this fix.
- Live repro (see Overview) re-run against the fixed code: local's 3 checkpoint commits and origin's story-
  spec merge are both present afterward, `state.json` still reads `"v4 (Accepted)"`, working tree clean,
  `git status` reports "up to date with origin/develop".
- Full `agents/scrum_team/tests` suite (via `docker compose -p horseless-carriage-test --env-file .env.test
  run --rm --no-deps --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`
  - `--no-deps` here only because a live dev stack already held the usual `db`/`litellm` ports on this
  machine; unaffected by that): 643 passed. The one pre-existing failure with `--no-deps`
  (`test_llm_integration.py::test_key_creation_and_usage`) needs the real `litellm` service and fails purely
  from its absence, unrelated to this change.

## Resolution
- `agents/scrum_team/tools/github.py`: new `_preserve_local_only_develop_commits()` - pushes (or
  merges-then-pushes) any local `develop` commits origin doesn't have, called from
  `_checkout_develop_or_recover` right after its `git fetch`, right before the reset-to-origin checkout.
  `_checkout_develop_or_recover` now uses a plain `git checkout <develop>` (not a reset) once preservation
  confirms local and origin are in sync, and falls back to the original reset-checkout otherwise (the rare,
  logged-and-rescued conflict case).
- `agents/scrum_team/tests/test_github.py`: new `TestPreserveLocalOnlyDevelopCommits` (6 tests), 2 new
  `TestCheckoutDevelopOrRecover` cases, 3 existing `TestCheckoutDevelopOrRecover` cases updated to patch the
  new helper as a no-op.
