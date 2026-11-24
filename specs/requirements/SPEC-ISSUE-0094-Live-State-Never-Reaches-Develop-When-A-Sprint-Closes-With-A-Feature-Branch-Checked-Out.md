# Issue

- Issue ID: SPEC-ISSUE-0094
- Title: Live State Never Reaches Develop When A Sprint Closes With A Feature Branch Checked Out - Stage Progress, Sprint Close And Budget Reset All Silently Lost
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-24

## Overview
A real eval run (0.2.1-run95, HC commit `b6bc4938`) delivered 1 of 12 stories in 3 sprints and stopped
early with `budget_critical_halt`. The judge scored Team Efficiency 1/5. Its Top Problem #3 says
US-0002 was "trapped in Review state ... unable to advance due to phase-boundary enforcement," and the
team's own sprint-3 retro says the same thing (`manifest.json` sprint 3 `sprint_report`, "Impediments
Logged So Far"). **That diagnosis is wrong.** The team lost its own state between sprints. The run
shows three symptoms of that loss:

1. **A story's progress went backwards.** US-0002's `stages_completed` was
   `['Draft','Ready','Implemented']` in the sprint-2 manifest snapshot. In sprint 3 it was back to
   `['Draft','Ready']`. In sprint 3, Architect's `advance_story_stage(US-0002, "Reviewed")` was refused
   with `"Cannot mark 'US-0002' as Reviewed - it hasn't completed ['Implemented'] yet."`
   (`transcript.md:9672`). The story file the same agents had just read said
   `- Status: Implemented` (`transcript.md:9544`, read while the feature branch was checked out).
   DevTeam had to re-mark it Implemented (`transcript.md:9781`). The sprint ran out of budget before
   US-0002 could reach Tested.
2. **The sprint number never advanced.** `team_sprint_number` is 1 in all three manifest entries
   (`team_sprint_is_continuation: true` for 2 and 3). At both later sprint starts, `start_sprint` was
   refused with `"start_sprint does not belong in the current phase ('Development')"`
   (`transcript.md:7082-7083` sprint 2, and the equivalent Event 14 in sprint 3).
3. **The budget was never reset.** `start_sprint` is the only place `sprint_number` and the per-sprint
   token budget reset (`sprint_budget_reset_state_delta`). Because it never ran, sprints 2 and 3
   inherited sprint 1's nearly spent 5M budget. Each got only about 1.3M tokens before
   `🚫 [TOKEN BUDGET EXCEEDED]`. Sprint 2's own report says "Tokens used: 5,695,473 / 5,000,000"
   after one partial story.

All three come from one mechanism: the session's live state is never written to develop when a sprint
closes.

**Confirmed mechanism (git-level evidence from the eval repo
`CI-Till-Krempel/horseless-carriage-eval-todo-app`):**

- **develop's `.hc/state.json` stopped changing 6 minutes into a 15-minute run.**
  `gh api repos/.../commits?sha=eval/0.2.1-run95/develop&path=.hc/state.json` shows the last commit
  touching it is `a7566072` at 10:34:01. That is the moment US-0001's feature PR #911 merged. Every
  later `checkpoint: save_state_to_repo` commit, from 10:38:12 to 10:42:13 (the rest of sprint 2,
  its close-out, and all of sprint 3), landed on
  `eval-0.2.1-run95/feature/US-0002-data-model-implementation`. That is US-0002's feature branch, and
  its PR #912 is still **open, never merged**.
- **The two branches disagree.** `.hc/state.json` at `a7566072` (develop) has US-0002 at
  `['Draft','Ready']`, `sprint_report: ""` and `token_usage.total: 4,375,611`. At `f514c46a`
  (feature branch, 10:38:33) it has US-0002 at `[..., 'Implemented']` **and** sprint 2's authored
  sprint report. Sprint 1's live state actually ended at 6,172,370 tokens with a fallback report
  (manifest sprint 1). None of that reached develop.
- **Both close-out paths shipped develop's stale copy.** Sprint 1's close-out PR #913 (10:36) and
  sprint 2's release commit `a3201e3f` (10:39:03) both contain freshly re-rendered
  `specs/reports/*.md`, plus `CHANGELOG.md` in sprint 2's case. Neither updates `.hc/state.json`,
  `specs/ROADMAP.md`, or `specs/stories/US-0002-*.md`.
- **The harness then loads that stale copy.** `_sync_local_clone_to_branch`
  (`agents/scrum_team/scripts/run_eval.py:176-187`) checks out develop and pulls between sprints. The
  next sprint's first `load_state_from_repo` (`transcript.md:6733`, `8737`) reads the stale develop
  copy. `current_sprint_phase` (`agents/scrum_team/helpers.py:1048-1077`) then sees a published Sprint
  Backlog PR (`sprint_backlog_pr_sprint: 1`), no `sprint_report_pending_release`, and
  4,375,611 < 5,000,000 tokens. It returns `"Development"`, so `start_sprint` is refused.

**Why the code produces this.** Three things combine:

1. `save_state_to_repo` (`agents/scrum_team/tools/scrum.py:529`) writes `.hc/state.json` and makes a
   **local-only** checkpoint commit on *whatever branch is checked out*
   (`_checkpoint_state_commit`, `scrum.py:468-482`: "Never pushes").
2. The only thing that routinely *pushes* state mid-sprint is `advance_story_stage`'s GH #383
   `state_push` (`agents/scrum_team/tools/requirements.py:2466-2510`). For
   `Implemented`/`Reviewed`/`Tested` it deliberately checks out and pushes to the story's **feature
   branch** (`requirements.py:2497-2498`). It assumes "QA's merge_story_pr, right after Tested,
   carries this same push's commit into develop." That assumption fails whenever a sprint ends with a
   story mid-pipeline: no budget is left, so the merge never happens. The working tree is also left
   on the feature branch. In this run DevTeam's sprint-2 `advance_story_stage(US-0002, "Implemented")`
   returned `"state_push": {"branch": "eval-0.2.1-run95/feature/US-0002-data-model-implementation"}`
   (`transcript.md:7776-7779`). The very next call was already refused as out-of-phase
   (`transcript.md:7870`).
3. Both close-out paths switch to develop and land re-rendered reports, but never write live state
   back to disk after the switch:
   - `_ensure_sprint_report_on_final_halt` (`agents/scrum_team/agent.py:592`, checkout at `:659`) and
     `_sync_and_commit_roadmap_on_exhaustion` (`agent.py:534`, checkout at `:567`)
   - `create_release_pr`'s land step (`agents/scrum_team/tools/github.py:2060-2105`)

   All of them run `git checkout -B develop origin/develop`. That silently replaces the
   committed-and-clean `.hc/state.json` in the working tree with develop's stale version. Next,
   `render_fallback_sprint_report`/`_write_conversation_transcript` regenerate the report files from
   **in-memory** state, which is why the reports are correct. Nothing does the same for
   `.hc/state.json`, `specs/ROADMAP.md`, or the story files. `integrate_open_changes`
   (`github.py:87`) only commits what is *dirty*. The stale state.json is clean, so the release lands
   without it. `create_release_pr` does call `save_state_to_repo` at its very end
   (`github.py:2285`), but that is again only a local, never-pushed checkpoint. Sprint 3's phase still
   read `Development` (that is, `sprint_backlog_pr_sprint` was still 1), so it plainly was not what the
   next sprint loaded. The run's artifacts can't show exactly where that local commit went.

This is the same class of bug as SPEC-ISSUE-0050/0052 and GH #383/#397, where progress disappears
between sprints. It takes a different path, though. GH #383 fixed the case where the stage commit was
never pushed at all. Here the commit *is* pushed, but to a branch that never merges. Every later
close-out step then builds on develop's stale snapshot.

## Acceptance Criteria
- At every point where a sprint's work is published to develop, develop's `.hc/state.json` matches
  the live session state at that moment. This covers `create_release_pr`'s land step,
  `_ensure_sprint_report_on_final_halt`, and `_sync_and_commit_roadmap_on_exhaustion`. Concretely,
  each of these paths calls `save_state_to_repo` (or an equivalent serializer) **after** its
  `checkout develop` and **before** `integrate_open_changes`/`_git_push_impl`. The pushed commit then
  carries live state, not the checked-out file. `create_release_pr` must persist its own
  release-success mutations (`sprint_report_pending_release = False`, `sprint_backlog_pr_sprint = 0`,
  `github.py:2252-2268`) **before** the land push, or push a second time after them. A trailing
  local-only checkpoint (today's `github.py:2285`) is not enough.
- The same applies to `specs/ROADMAP.md` and every story file whose stage is newer in live state than
  on develop. They are re-synced from in-memory state after the checkout, the way the reports already
  are (`sync_all_active_stories_to_roadmap` and `_update_story_markdown`,
  `agents/scrum_team/tools/requirements.py:1761` and `:1418`). A story's `Implemented`/`Reviewed` stage
  reached on an unmerged feature branch therefore survives into the next sprint even though its code
  is still unmerged. This matches what `render_fallback_sprint_report`'s own docstring already
  promises: "unfinished feature branches are left exactly as they are ... pick up again next sprint."
- In an eval run where a sprint ends with a story at `Implemented` on an unmerged feature branch, the
  next sprint's `load_state_from_repo` shows that story with `Implemented` still in `stages_completed`.
  The next sprint's `start_sprint` also succeeds on its first call (the phase is
  `Plan & Start Sprint`, not `Development`). In the manifest, `team_sprint_number` increments across
  harness sprints instead of reading `1 (continued)`.
- Tests that fail today and pass once fixed:
  - Set up a git repo with develop and a feature branch. Use `advance_story_stage(..., "Implemented")`
    to push the stage to the feature branch, which leaves the tree there. Set `sprint_report` in live
    state, then call `create_release_pr`. Assert that `git show origin/develop:.hc/state.json` contains
    the story's `Implemented` stage, the sprint report, `sprint_report_pending_release: false` and
    `sprint_backlog_pr_sprint: 0`. Today it contains develop's older snapshot.
  - The same setup driving `_ensure_sprint_report_on_final_halt`. Assert develop's pushed
    `.hc/state.json` carries live `token_usage` and `sprint_report`. Then assert that
    `current_sprint_phase(<that file's contents>)` is not `"Development"`.

## Notes
- **Run artifacts**: `eval-report-0.2.1-run95` (GitHub Actions run 45374553012, workflow run #95).
  Eval-repo PRs: #911 (US-0001, merged), #912 (US-0002 feature PR, still open), #913 (sprint 1
  close-out, merged 10:36:12), #914 (sprint 2 release, merged 10:39:12), #916 (sprint 3 release,
  merged 10:42:16). develop's `.hc/state.json` history and the `a7566072`/`f514c46a`/`857f41a2`
  snapshots above were read with `gh api repos/CI-Till-Krempel/horseless-carriage-eval-todo-app/...`.
- **Secondary, misleading output**: the team's sprint-3 retro and impediment entries, and the judge's
  Top Problem #3, blame "phase boundary enforcement" for US-0002 not reaching Tested. The phase gate
  did refuse Architect's sprint-2 `Reviewed` call (`transcript.md:8554`, Event 76) because the sprint
  had already entered `Review, Retro & Release`. That gate was working as designed, and its own
  message offers an immediate-retry override. The *sprint-3* failure, though, was the
  lost-`Implemented` rejection above, not the phase gate. Don't loosen the phase gate in response to
  this run.
- **Related but distinct**: SPEC-ISSUE-0092 (live human override for a budget halt) covers a human
  unsticking a sprint stuck at the ceiling. Here the halt was mechanically correct given the stale
  state; the bug is that the budget should have been reset by `start_sprint` and wasn't. SPEC-ISSUE-0070
  (`create_release_pr` resetting `sprint_backlog_pr_sprint`) made the right in-memory change, but this
  run shows that change never reaching develop. SPEC-ISSUE-0080 (event-cap continuation) uses the same
  `team_sprint_is_continuation` reporting, but its cause is the harness event cap, not lost state.
- **Also observed, not root-caused here**: the product backlog shrank 19 → 18 → 17 items across the
  three snapshots (`SPEC-ISSUE-0003`, then `SPEC-ISSUE-0002`, both auto-filed sprint-1 impediments,
  disappeared). These could be legitimate pruning of resolved impediments or another symptom of the
  same stale reload. Worth re-checking once this fix lands.
- Not a guardrail/budget-limit change under AGENTS.md section 12. The fix makes the existing budget
  reset actually happen; it doesn't loosen any limit.

## Test Approach
- Unit (host-independent, `agents/scrum_team/tests/`): build a temporary git repo with a bare
  `origin`, `develop`, and one feature branch, in the style of the existing `create_release_pr` /
  `integrate_open_changes` tests. Cover the two scenarios under Acceptance Criteria by asserting on
  `git show origin/<develop>:.hc/state.json`, never just the working-tree file, since the working
  tree is exactly what masks this bug locally.
- Add a regression assertion that `create_release_pr`'s land commit includes `.hc/state.json` whenever
  live state differs from develop's committed copy, even when the working-tree file was clean at
  checkout time.
- Eval follow-up: the next Team Performance Evaluation run should show `team_sprint_number`
  incrementing per harness sprint, and no story's `stages_completed` shrinking between consecutive
  manifest snapshots (the Step 2 query in `.claude/skills/investigate-eval-run/SKILL.md`).

## Resolution
Fixed in the same PR that filed this issue (2026-11-24).

- **New `_rewrite_live_state_into_working_tree`** (`agents/scrum_team/tools/github.py`) writes the
  following from in-memory state onto whatever is checked out:
  - `.hc/state.json`, via `save_state_to_repo`;
  - `specs/ROADMAP.md`, via `sync_all_active_stories_to_roadmap`;
  - the story file of every non-Epic backlog item past Ready, via `_update_story_markdown`.

  Each artifact is best-effort, so one failure doesn't block the others.
- **New `_push_live_state_to_develop`** checks out develop (`_checkout_develop_or_recover`), runs the
  rewrite and `integrate_open_changes`, then publishes through `_preserve_local_only_develop_commits`
  (push, or merge-then-push if diverged). A call with nothing new to publish creates no empty commit.
- **`create_release_pr`**:
  - The land step now runs the rewrite right after its develop checkout and before
    `integrate_open_changes`.
  - The trailing local-only `save_state_to_repo` is replaced by `_push_live_state_to_develop`. The
    release-success reset (`sprint_report_pending_release`, `sprint_backlog_pr_sprint`) and
    `finalize_release_version`'s state changes now actually reach origin/develop.
  - The response gains `state_push`, plus a `state_push_warning` if publishing failed. The release
    itself still reports `ok`, since the PR already opened.
- **`agent.py`**: `_ensure_sprint_report_on_final_halt` and `_sync_and_commit_roadmap_on_exhaustion`
  run the rewrite right after their own develop checkout. In the latter it replaces the roadmap-only
  sync.
- **Tests**: `agents/scrum_team/tests/test_live_state_reaches_develop.py` uses a real bare `origin`
  and real git, with only `gh` stubbed, and reproduces run95's shape: a stale develop, and live state
  checkpointed only on a pushed feature branch. It asserts on `git show develop:.hc/state.json` in
  origin:
  - `create_release_pr`: the story keeps `Implemented`, the report and live token usage are present,
    `sprint_report_pending_release` is False, `sprint_backlog_pr_sprint` is 0,
    `current_sprint_phase` is not `Development`, and the story file says Implemented.
  - `_push_live_state_to_develop` is a no-op (no new commit) when develop already matches.
  - `_ensure_sprint_report_on_final_halt`: the story keeps `Implemented`, live token usage and the
    report are present, and the phase is `Review, Retro & Release`.

  All three fail against the pre-fix code (origin/develop showed `['Draft', 'Ready']`, exactly as in
  run95) and pass after the fix. Two `test_github.py` assertions and three `test_agent.py` patch
  targets were updated for the intentional extra checkout/integrate and the renamed sync call.
  `python3 run_tests.py`: 863 host + 1486 agent passed.
- **Not changed** (deliberately out of scope): advance_story_stage's GH #383 feature-branch
  `state_push`. Persisting in-flight progress on the feature branch is still correct; the gap was
  only that close-out never re-published live state to develop. The `agent.py` close-out paths also
  still use a raw `checkout -B develop origin/develop` rather than `_checkout_develop_or_recover`.
  Live state is now rewritten after that checkout anyway, so a discarded local-only develop
  checkpoint no longer loses state, but a discarded local-only `specs/` commit could still be lost
  there. That's a pre-existing SPEC-ISSUE-0050-class gap, worth its own follow-up.
