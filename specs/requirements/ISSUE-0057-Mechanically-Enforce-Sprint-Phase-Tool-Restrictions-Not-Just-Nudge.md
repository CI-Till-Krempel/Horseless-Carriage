# Issue

- Issue ID: ISSUE-0057
- Title: Mechanically Enforce Sprint-Phase Tool Restrictions (Not Just Nudge) - Block Implementation Outside Development, Block New Implementation During Closing
- Status: Ready
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Raised by the maintainer while reviewing ISSUE-0056: `current_sprint_phase` (`agents/scrum_team/
helpers.py:789`) already computes one of `SPRINT_PHASES` ("Conceptual Work", "Plan & Start Sprint",
"Development", "Review, Retro & Release") every turn, and `phase_awareness_injection_callback`
(`agents/scrum_team/agent.py:1653`) already tells every role which one it's in - but that function's
own docstring says it plainly: *"This never refuses a tool call itself... the existing mechanical
gates... are untouched and remain the only real enforcement."* Nothing stops DevTeam from writing new
implementation code while the team is still grooming the backlog, or from starting a fresh story while
the sprint is supposed to be closing out. ISSUE-0056 showed this isn't theoretical: a real eval run
left phase stuck on "Review, Retro & Release" for two whole sprints with nothing mechanically
preventing further work regardless. The question: can this become a real, tool-level gate instead of
text the model can ignore - and is that a large change?

**Answer: no, not a large change - the hook point already exists.** `log_tool_invocation_callback`
(`agents/scrum_team/agent.py:2422`) is already registered as the root agent's `before_tool_callback`
(`agent.py:2855`) and already runs before *every* tool call, for every role, and already
short-circuits calls it doesn't like by returning an error dict instead of letting the call proceed -
exactly the mechanism `_detect_transfer_loop`/`_detect_repeated_call_loop`/the self-transfer block
already use. Adding a phase-based allow/deny check is an incremental addition to an established
pattern: reuse `current_sprint_phase(state)` (already computed every turn) plus a new, small
per-phase tool policy table next to the existing `SPRINT_PHASE_GUIDANCE` dict in `helpers.py`. No new
subsystem, no new hook, no change to how phase itself is computed.

**The real design work is the policy table itself**, since tools don't sort cleanly into "always
allowed" vs. "implementation-only" by name alone. Surveying every role's actual `tools=[...]` list
(`agent.py:2666-2837`):

- Unambiguously **implementation-only** (block outside "Development"): `write_file`, `delete_file`,
  `start_feature_branch`, `mark_pr_ready_for_review`, `merge_story_pr`, `gh_pr_create`,
  `resolve_feature_branch_conflicts`, `git_push`, `check_build`.
- Unambiguously **planning-only** (the natural fit is "Conceptual Work"/"Plan & Start Sprint", not
  "Development"/closing - re-planning mid-sprint or mid-close is exactly the pattern other gates
  already discourage): `upsert_story`, `upsert_epic`, `upsert_prd`, `upsert_srs`, `plan_backlog_item`,
  `set_priority`, `declare_backlog_scope_complete`, `create_story_spec_pr`.
- Unambiguously **closing-only** (the natural fit is "Review, Retro & Release"): `create_sprint_report`,
  `update_sprint_report_with_kpis`, `calculate_kpis`, `add_retro_action`, `create_release_pr`,
  `gh_release_create`, `propose_steering_change`, `gather_workflow_improvement_proposals`,
  `generate_workflow_diagram`, `calculate_cost_breakdown`, `recommend_sprint_budget`,
  `optimize_process_for_budget`.
- **Needs stage-aware handling, not a blanket tool-name rule**: `advance_story_stage` is used for
  *both* backlog grooming (`Draft`->`Ready`, squarely a planning-phase action) *and* implementation
  progression (`Ready`->`Implemented`->`Reviewed`->`Tested`->`Accepted`). A phase gate on this tool
  must look at the `stage` argument, not just the tool name: block `Implemented`/`Reviewed`/`Tested`
  transitions outside "Development", but allow `Ready` during "Conceptual Work"/"Plan & Start Sprint"
  and allow `Accepted` during "Review, Retro & Release" (finishing acceptance on an already-Tested
  story is exactly what closing a sprint should still permit). `record_acceptance_check` is the same
  shape - tied to whichever story it names, not the phase as a blanket rule.
- **Always allowed, every phase** (cross-cutting/communication/recovery, never gated):
  `transfer_to_agent`, `read_doc`, `list_docs`, `log_decision`, `raise_story_blocker`,
  `resolve_story_blocker`, `deny_review`, `record_blocking_interaction`, `resolve_blocking_interaction`,
  `list_blocking_interactions`, `record_human_approval`, `gh_pr_status`, `gh_pr_checks`,
  `gh_pr_comment`, `gh_pr_comments`, `gh_pr_review`, `gh_pr_check_logs`, `get_budget_status`,
  `update_budgets`, `log_token_usage`, `repo_status`, `save_state_to_repo`, `load_state_from_repo`,
  every state-recovery tool (`get_corrupted_state_raw_content`, `save_repaired_state`,
  `reset_state_from_git`, `clear_corrupted_state`), `create_from_template`, `integrate_open_changes`,
  `update_roadmap` (reflects state, doesn't create new scope).
- **The sprint-boundary tools themselves** (`start_sprint`, `create_sprint_backlog_pr`,
  `plan_sprint_backlog_item`) belong to "Plan & Start Sprint" specifically - gating them elsewhere
  would block the very transition the phase model exists to describe.

## Why this must not ship before (or without) ISSUE-0056
Today, a wrongly-computed phase is harmless precisely because nothing acts on it. ISSUE-0056 showed
`current_sprint_phase` can get stuck on "Review, Retro & Release" for multiple sprints due to a stale,
never-reset token budget. If this issue's gate shipped on its own, that exact bug would stop being a
silent waste and become a **hard deadlock**: the team would be mechanically forbidden from any real
implementation work for the rest of the run, with no story ever able to progress. This must land
after, or together with, a fix for ISSUE-0056 - gating on a phase signal that is already known to get
stuck is strictly worse than the current nudge-only behavior.

## Acceptance Criteria
- A new per-phase tool policy (e.g. `PHASE_TOOL_POLICY` in `agents/scrum_team/helpers.py`, next to
  `SPRINT_PHASE_GUIDANCE`) maps each of `SPRINT_PHASES` to the set of tool names allowed during it,
  plus the cross-cutting "always allowed" set applied on top of every phase.
- `log_tool_invocation_callback` (or a new sibling function it calls, matching the existing
  `_detect_transfer_loop`/`_detect_unadvanced_implementation` pattern) checks the called tool against
  this policy before any other logic runs, and returns a `{"status": "error", "message": ...}` refusal
  - naming the current phase and what *is* allowed right now - instead of letting the call proceed,
  whenever the tool isn't in the current phase's allowed set.
- `advance_story_stage` and `record_acceptance_check` are evaluated by their actual effect (the
  `stage` argument / the story's current stage), not blocked or allowed as a whole tool - per the
  stage-aware rule above.
- A real, usable escape hatch exists for a genuine edge case (e.g. a critical fix discovered during
  QA that technically falls in "Review, Retro & Release") - matching every other mechanical gate in
  this codebase (`resolve_story_blocker`, `resolve_feature_branch_conflicts`'s own resolution path):
  an explicit override call (or reuse of an existing escalation path like `raise_story_blocker` ->
  human/Architect resolution) that is itself logged, not a silent bypass.
- A new regression test per phase: constructs state in that phase, attempts a tool outside its
  allowed set, asserts a refusal naming the phase; attempts a tool inside its allowed set, asserts no
  refusal. Separately covers the `advance_story_stage`/`record_acceptance_check` stage-aware cases
  (e.g. `Ready` allowed in "Conceptual Work", `Implemented` refused in the same phase).
- Depends on ISSUE-0056 (or an equivalent fix ensuring `current_sprint_phase` cannot get stuck) landing
  first or in the same change - call this out explicitly in the PR that implements this.

## Notes
- This does not change how `current_sprint_phase` itself is computed - only what happens once a phase
  is known. If `current_sprint_phase`'s own logic changes (ISSUE-0056's fix), this gate automatically
  benefits without further change, since it only ever reads the phase, never derives it independently.
- Scope is deliberately the `before_tool_callback` hook only - no changes to tool signatures, to
  `STORY_STAGES` ordering, or to any individual tool's own existing internal gates (those stay exactly
  as they are; this adds one more check ahead of all of them, for a different, complementary reason -
  the TOOL being called is fundamentally premature for the current phase, not that its arguments or the
  target story's state are wrong).
- A phase-gate refusal should feel like every other mechanical-gate refusal in this codebase (ISSUE-0053's
  loop breaker, `new_sprint_item_blocked`, etc.) - a clear, specific message naming the phase and
  pointing at what to do instead, not a bare "not allowed."
- Not addressed here: whether `current_sprint_phase`'s 4-phase model itself is the right granularity,
  or whether some roles should have role-specific exceptions beyond the global allow-list (e.g. Architect
  giving an early design opinion during "Conceptual Work" via `write_file` for an ADR draft - `upsert_adr`
  is already a cross-cutting planning-style tool in the "always allowed"/"planning" buckets above, so
  this specific case is already covered, but the next engineer should sanity-check every role's full
  tool list against the policy table before shipping, not just the categories sketched here).

## Test Approach
- New tests alongside the existing phase-awareness coverage (wherever `current_sprint_phase`/
  `phase_awareness_injection_callback` are tested, or a new `TestPhaseGatedToolEnforcement` class in
  `agents/scrum_team/tests/test_agent.py`): one case per `SPRINT_PHASES` entry, asserting the
  implementation-only tool set is refused outside "Development" and allowed inside it, the
  planning-only set the mirror image for "Conceptual Work"/"Plan & Start Sprint", and the closing-only
  set for "Review, Retro & Release".
- Dedicated cases for `advance_story_stage`'s stage-aware behavior: `Ready` allowed in "Conceptual
  Work", `Implemented` refused in "Conceptual Work" and in "Review, Retro & Release", `Accepted`
  allowed in "Review, Retro & Release".
- A case confirming the override/escalation path actually unblocks a legitimately-needed
  out-of-phase call, and that it leaves a logged trace (not a silent bypass).
- Full `agents/scrum_team/tests` suite re-run to confirm no existing test's fixture state
  (constructed without phase in mind) starts tripping a new refusal it didn't expect.
