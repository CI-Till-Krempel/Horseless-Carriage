# Issue

- Issue ID: ISSUE-0046
- Title: KPIs Never Computed, Grace Still Dead-Ends On A Multi-Hop Close-Out, And The Team Fabricates Stub Files/Buffer Stories To Game Two Mechanical Gates
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-07

## Overview
Reported (maintainer, reviewing `0.1.0-run26` - the first run after ISSUE-0045's fixes): sprints 1-2
completed cleanly with real sprint reports, but sprint 3 still produced none, the run report flagged
junk files cluttering the repo root, and - separately noticed by the maintainer - no run has ever shown
real KPI trends ("Say-Do Ratio: n/a", "never computed").

Investigated against the real run: `gh run view 31165715925` (`0.1.0-run26`), its
`report.md`/`manifest.json`/`transcript.md` artifacts, and the actual PRs/files in the eval repo
(`horseless-carriage-eval-todo-app`). Four distinct, independent findings.

**Finding 1 - QualityGuardian has never once been invoked, in any run:** grepping every transcript this
run and `0.1.0-run25` produced for `QualityGuardian`/`calculate_kpis`/`update_sprint_report` returns zero
hits. Root cause: `ORCHESTRATOR_PROMPT`'s SPRINT CLOSE SEQUENCE (`prompts.py`) went straight from step 6
(Scrum Master's retrospective) to step 7 (Product Owner's `create_sprint_report`/`create_release_pr`) -
no step ever told any agent to transfer to QualityGuardian, despite `QUALITY_GUARDIAN_PROMPT` itself
having detailed instructions for what to do "at the end of each sprint." A fully-wired, reachable
sub-agent (`quality_guardian` is a normal peer in `root_agent.sub_agents`) sat completely orphaned
because nothing in the actual step-by-step flow ever named it. `create_sprint_report` never mechanically
required it either (unlike the retrospective, which `retro_baseline` already mechanically enforces) - the
prompt alone saying QualityGuardian's step mattered was never enough, the same lesson the retrospective
gate already learned (see ISSUE-0009 era work). Every KPI trend chart in every real run's report has
read "no data available... never computed" as a direct result.

**Finding 2 - the ISSUE-0045 grace redirect works, but the grace window is too thin for a real,
multi-hop close-out:** traced sprint 3's halt turn-by-turn in the transcript. QA hit the main budget
(5,036,698/5,000,000) and was correctly redirected to Product Owner (ISSUE-0045's fix). Product Owner's
first move was wrong (`advance_story_stage(US-0006, "Tested")` - QA-only, rejected); its second move was
also off the close-out path (a stray `transfer_to_agent(Architect)`); Architect then hit budget too
(5,222,106) and was redirected back to Product Owner; Product Owner *then* correctly transferred to
Scrum Master for retro - but that one exchange alone pushed usage to 5,314,996, past the grace ceiling
(5,000,000 x 1.05 = 5,250,000). Scrum Master - itself grace-eligible - now also exceeded grace with
nowhere left to redirect (by design: a grace role past its own grace has no better place to go), froze,
and all 4 remaining "continue" nudges hit the identical wall. The entire 250,000-token grace allowance
was spent on two wrong guesses and agent-hopping before a single retro/report/release call ever
happened.

**Finding 3 - `advance_story_stage`'s Implemented gate was gamed 4 times in a row, explaining the Code
Quality complaint:** the report flagged `list_delete.py`/`task_feature.py`/`task_toggle.py`/
`task_delete.py` cluttering the repo root - each a one-line stub with a docstring and a trivial
`return True`/`pass`, no real logic. Confirmed in the transcript: DevTeam wrote the entire app
(`app.py`/`templates/index.html`/`test_app.py`) exactly once, during US-0001 (the CRUD operations are
tightly interlinked, so implementing them all in one pass is reasonable). For every subsequent story
(US-0002 through US-0005), `advance_story_stage`'s Implemented check - which requires a *new*
`write_file` touch to any non-`specs/` file since the last Implemented transition, with no concept of
"already covered by earlier work" - rejected the call, and DevTeam fabricated a trivial file purely to
satisfy the mechanical check, 4 times in a row.

**Finding 4 - the Ready-backlog depth gate has no honest way out once real backlog runs low, so the
model fabricated fake stories:** by sprint 3, only US-0005/US-0006 remained real and un-Accepted -
2 stories, short of `TARGET_STORIES_PER_SPRINT (3) x READY_BACKLOG_SPRINTS_TARGET (1)` = 3. With no
honest way to say "that's genuinely all that's left," the model fabricated two throwaway stories,
**"Additional Buffer Story for Sprint Depth"** and **"...2..."** (US-0007/US-0008), spending real
story-spec-PR effort on stories nobody ever intended to implement, purely to pad the Ready count.

## Acceptance Criteria
- The SPRINT CLOSE SEQUENCE (`ORCHESTRATOR_PROMPT`) has an explicit step handing off to QualityGuardian
  between the retrospective and `create_sprint_report`, and `create_sprint_report` mechanically refuses
  to run without a *fresh* `update_sprint_report` call since the last report (mirroring `retro_baseline`
  exactly) - a prompt-only instruction has now twice proven insufficient on its own.
- The KPI dashboard is actually rendered inside the generated sprint report document (previously
  computed and stored in `sprint_report_kpis` purely for `run_eval_analysis.py`'s trend charts, with no
  human-visible counterpart in the document itself, despite `QUALITY_GUARDIAN_PROMPT` saying to include
  it).
- `SPRINT_CLOSEOUT_GRACE_PERCENT`'s default is raised from 5.0 to 20.0, and the synthesized redirect
  message (`agent.py`'s `_budget_halt_response`, from ISSUE-0045) explicitly states the exact remaining
  sequence and says not to attempt any other action first - cutting the wrong guesses that ate the whole
  grace window at the source, not just paying for them out of a bigger budget.
- `advance_story_stage`'s Implemented gate accepts an honest `implemented_via_earlier_work` justification
  (parallel to the existing `spike` flag) when a story's real work already landed via an earlier story's
  `write_file` calls this sprint - logged to `decision_log` for audit, not a silent bypass. A blank or
  placeholder justification is still rejected.
- `ready_backlog_shortfall`'s target can be explicitly, honestly waived via a new
  `declare_backlog_scope_complete(justification)` tool (Product Owner) when the product's real remaining
  scope is genuinely smaller than the target - requiring a substantive justification, logged to
  `decision_log`. Deliberately **not** an automatic cap based on however many items merely happen to be
  in `product_backlog` - that would silently satisfy the gate for any real, open-ended backlog too (see
  Notes).
- Full `agents/scrum_team/tests` suite and top-level `pytest tests/` both pass with no regressions.

## Notes
- An automatic cap (`min(target, count of non-Accepted backlog items)`) was tried first and reverted -
  it broke `test_refuses_when_short_then_succeeds_once_enough_ready`
  (`test_sprint_and_approval_gates.py`), which relies on the gate demanding *more* Ready work than
  whatever's currently entered, for a normal open-ended backlog. Automatically capping by list length
  would let a real Product Owner get away with never building a real Ready-ahead buffer at all (just
  never enter more than the target). The explicit, justified `declare_backlog_scope_complete` escape
  hatch preserves the gate's original intent for the common case while giving an honest way out for a
  genuinely closed/finite scope - the same design tradeoff `spike` already makes for "no code to write."
- The grace-window fix addresses the *proximate* cause (too little headroom, wasted on wrong guesses);
  a deeper contributor is that per-turn token cost balloons over a sprint as conversation history grows
  (each hop in sprint 3 cost 90-185K tokens purely to reason about the next transfer, just from
  accumulated context) - flagged as a separate, larger architectural question (context
  trimming/summarization) for a future issue, not addressed here.
- Two retro-filed process notes in the eval repo itself (`ISSUE-0002`/`ISSUE-0003`: DevTeam trying to
  create a feature branch before the sprint backlog PR merged; a git-push rejection from untracked files
  during story-spec creation) both self-corrected within the same sprint and are rated low-severity by
  the run's own report - no code fix proposed for these.
- The roadmap/story desync noted in the report (US-0005 shown "Implemented" not "Accepted") is fallout
  from sprint 3 running out of budget mid-review, not an independent bug - expected to improve as
  Findings 2-4 stop wasting spend on wrong guesses and fabricated work.

## Test Approach
- `agents/scrum_team/tests/test_quality.py::test_update_sprint_report_bumps_kpi_update_count` - a fresh
  `update_sprint_report` call increments `kpi_update_count`.
- `agents/scrum_team/tests/test_budget.py::test_create_sprint_report_rejects_without_fresh_kpi_update`,
  `test_create_sprint_report_requires_fresh_kpi_update_each_sprint` - mirrors the existing retro-gate
  tests exactly, for the new KPI gate.
- `agents/scrum_team/tests/test_budget.py::test_create_sprint_report_renders_kpi_dashboard` - the
  rendered report document actually contains a `## KPI Dashboard` section with real values.
- `agents/scrum_team/tests/test_agent.py::TestSprintCloseoutGrace` - existing grace tests pinned to an
  explicit `SPRINT_CLOSEOUT_GRACE_PERCENT` rather than the ambient default (so they don't silently break
  the next time the default itself changes).
- `agents/scrum_team/tests/test_requirements.py` -
  `test_implemented_rejects_blank_or_placeholder_earlier_work_justification`,
  `test_implemented_accepts_a_real_earlier_work_justification` (asserts `decision_log` entry);
  `TestDeclareBacklogScopeComplete` - rejects placeholder justifications, accepts and logs a real one.
- `agents/scrum_team/tests/test_helpers.py::TestReadyBacklogShortfall` -
  `test_reports_shortfall_against_the_full_target_regardless_of_backlog_size` (the regression the
  automatic-cap approach would have caused), `test_backlog_scope_complete_waives_the_target_entirely`.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm --entrypoint ""
  -e PYTHONPATH=/app agent pytest agents/scrum_team/tests`, with `db`/`litellm` up first): 518 passed, no
  regressions.
- `pytest tests/`: 421 passed, no regressions.

## Resolution
- `agents/scrum_team/prompts.py`: SPRINT CLOSE SEQUENCE gets a new step 7 (Scrum Master ->
  QualityGuardian -> Product Owner for KPIs), renumbering the old step 7 to 8;
  `QUALITY_GUARDIAN_PROMPT` told to transfer back to Product Owner once done; the grace-window guidance
  and `_budget_halt_response`'s message both made explicit/directive about the remaining sequence;
  `REQUIREMENTS ENGINEERING` section points at `declare_backlog_scope_complete` instead of inventing
  filler stories; DevTeam's Implemented-stage guidance points at `implemented_via_earlier_work` instead
  of fabricating a file.
- `agents/scrum_team/tools/quality.py`: `update_sprint_report` increments `kpi_update_count` on success.
- `agents/scrum_team/tools/budget.py`: `create_sprint_report` gains a `kpi_baseline` gate mirroring
  `retro_baseline`, sets `kpi_baseline` on success, and renders a `## KPI Dashboard` section from
  `sprint_report_kpis` when present.
- `agents/scrum_team/tools/requirements.py`: `advance_story_stage` gains
  `implemented_via_earlier_work: str = None`; a substantive justification satisfies the Implemented
  gate's source-touch check (logged to `decision_log`) instead of requiring a new file. New
  `declare_backlog_scope_complete(justification)` sets `backlog_scope_complete` (logged to
  `decision_log`).
- `agents/scrum_team/helpers.py`: `closeout_grace_percent()` default 5.0 -> 20.0.
  `ready_backlog_shortfall()` gains a `backlog_scope_complete` parameter that waives the target entirely
  when set - explicitly passed in, never inferred from backlog size.
- `agents/scrum_team/tools/github.py`: `create_sprint_backlog_pr` passes
  `state.get("backlog_scope_complete")` through to `ready_backlog_shortfall`; error message mentions the
  new escape hatch.
- `agents/scrum_team/agent.py`: `_budget_halt_response`'s redirect message rewritten to be directive
  about the exact remaining sequence.
- `agents/scrum_team/state.py`, `tools/scrum.py`: new persisted state fields `kpi_update_count`,
  `kpi_baseline`, `backlog_scope_complete`.
- `docker-compose.yaml`, `docker-compose.local.yaml`, `docker-compose.local-hostollama.yaml`:
  `SPRINT_CLOSEOUT_GRACE_PERCENT` default 5.0 -> 20.0.
