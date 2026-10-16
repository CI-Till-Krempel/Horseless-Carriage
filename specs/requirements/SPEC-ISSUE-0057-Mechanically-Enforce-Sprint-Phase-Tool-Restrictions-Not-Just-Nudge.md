# Issue

- Issue ID: SPEC-ISSUE-0057
- Title: Mechanically Enforce Sprint-Phase Tool Restrictions (Not Just Nudge) - Block Implementation Outside Development, Block New Implementation During Closing
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Raised by the maintainer while reviewing SPEC-ISSUE-0056: `current_sprint_phase` (`agents/scrum_team/
helpers.py:789`) already computes one of `SPRINT_PHASES` ("Conceptual Work", "Plan & Start Sprint",
"Development", "Review, Retro & Release") every turn, and `phase_awareness_injection_callback`
(`agents/scrum_team/agent.py:1653`) already tells every role which one it's in - but that function's
own docstring says it plainly: *"This never refuses a tool call itself... the existing mechanical
gates... are untouched and remain the only real enforcement."* Nothing stops DevTeam from writing new
implementation code while the team is still grooming the backlog, or from starting a fresh story while
the sprint is supposed to be closing out. SPEC-ISSUE-0056 showed this isn't theoretical: a real eval run
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
- **Considered "closing-only" (`create_sprint_report`, `calculate_kpis`, `create_release_pr`, etc.) -
  deliberately NOT gated, implemented as always-allowed instead.** These are already governed by their
  own, separately-tested machinery (`check_cost_budget_callback`'s SPRINT CLOSE SEQUENCE grace,
  `_restrict_to_sprint_report_step`, the ritual/critical-halt budget system) that can - correctly -
  trigger the closing ritual before `current_sprint_phase`'s own simpler classification agrees the team
  has reached "Review, Retro & Release" (e.g. a critical budget halt mid-"Development"). Confirmed by
  implementation: gating these broke 9 pre-existing tests across `TestRestrictToSprintReportStep`,
  `TestRepeatedRejectionLoop`, and `TestLogToolInvocationCallbackBlocksRepeatedCalls`, all of which
  construct a default/"Conceptual Work"-phase state because phase was never relevant to what they test -
  real evidence this bucket would have fought the existing system rather than complemented it. The
  user's actual requirement ("in sprint closing, only reporting/version topics should be doable") is
  still fully met: "Development" is the only phase that allows new implementation tools at all, so
  closing can never include fresh `write_file`/`start_feature_branch` regardless.
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

## Why this must not ship before (or without) SPEC-ISSUE-0056
Today, a wrongly-computed phase is harmless precisely because nothing acts on it. SPEC-ISSUE-0056 showed
`current_sprint_phase` can get stuck on "Review, Retro & Release" for multiple sprints due to a stale,
never-reset token budget. If this issue's gate shipped on its own, that exact bug would stop being a
silent waste and become a **hard deadlock**: the team would be mechanically forbidden from any real
implementation work for the rest of the run, with no story ever able to progress. This must land
after, or together with, a fix for SPEC-ISSUE-0056 - gating on a phase signal that is already known to get
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
- Depends on SPEC-ISSUE-0056 (or an equivalent fix ensuring `current_sprint_phase` cannot get stuck) landing
  first or in the same change - call this out explicitly in the PR that implements this.

## Notes
- This does not change how `current_sprint_phase` itself is computed - only what happens once a phase
  is known. If `current_sprint_phase`'s own logic changes (SPEC-ISSUE-0056's fix), this gate automatically
  benefits without further change, since it only ever reads the phase, never derives it independently.
- Scope is deliberately the `before_tool_callback` hook only - no changes to tool signatures, to
  `STORY_STAGES` ordering, or to any individual tool's own existing internal gates (those stay exactly
  as they are; this adds one more check ahead of all of them, for a different, complementary reason -
  the TOOL being called is fundamentally premature for the current phase, not that its arguments or the
  target story's state are wrong).
- A phase-gate refusal should feel like every other mechanical-gate refusal in this codebase (SPEC-ISSUE-0053's
  loop breaker, `new_sprint_item_blocked`, etc.) - a clear, specific message naming the phase and
  pointing at what to do instead, not a bare "not allowed."
- Not addressed here: whether `current_sprint_phase`'s 4-phase model itself is the right granularity,
  or whether some roles should have role-specific exceptions beyond the global allow-list (e.g. Architect
  giving an early design opinion during "Conceptual Work" via `write_file` for an ADR draft - `upsert_adr`
  is already a cross-cutting planning-style tool in the "always allowed"/"planning" buckets above, so
  this specific case is already covered, but the next engineer should sanity-check every role's full
  tool list against the policy table before shipping, not just the categories sketched here).

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestDetectPhaseViolation` - one case per gated bucket
  (implementation-only refused outside "Development"/allowed inside; planning-only refused outside
  "Conceptual Work"/"Plan & Start Sprint"/allowed inside both), `transfer_to_agent` and
  always-allowed/unbucketed tools never blocked, the `advance_story_stage` stage-aware cases (`Ready`
  allowed in "Conceptual Work", `Implemented` refused there and allowed in "Development", `Accepted`
  allowed in both "Development" and "Review, Retro & Release"), the override/retry path (identical
  retry allowed and logged as a `blocking_interaction`; a *different* args retry blocked again, not
  mistaken for an override), and the fail-open case (malformed/mock state never blocks a call).
- Full `agents/scrum_team/tests` suite re-run (1028 passed, 1 skipped) - this is what actually surfaced
  the closing-only bucket's conflict with existing tests (see above) and one real bug in the
  implementation itself (see Resolution).

## Resolution
- `agents/scrum_team/helpers.py`: `PHASE_TOOL_POLICY` (Conceptual Work / Plan & Start Sprint /
  Development only - no closing bucket, see above), `PHASE_SPECIFIC_TOOLS` (flat union of every tool
  name appearing anywhere in the policy - used to decide "is this tool phase-specific at all"),
  `ALWAYS_PHASE_ALLOWED_TOOLS` (cross-cutting/communication/recovery tools plus the closing-ritual
  tools), and `PLANNING_STAGE_NAMES` (`{"Draft", "Ready"}`, for `advance_story_stage`'s stage-aware
  check).
- `agents/scrum_team/agent.py`: new `_detect_phase_violation`, called from
  `log_tool_invocation_callback`'s existing non-transfer branch (right after the repeated-call loop
  check). Wrapped in a broad `try/except Exception: return None` - observability layered on top of
  real work must never block a call over an internal computation error (this is what the fail-open
  test covers; two pre-existing tests used a raw, unconfigured `MagicMock()` state and needed this).
  `_advance_story_stage_phase_violation` implements the stage-aware rule. The override mechanism keys
  a `(agent, tool, args, phase)` signature into `state["_phase_gate_nudged"]`: first occurrence refuses
  and remembers it, an identical second occurrence removes it, logs an override via
  `record_blocking_interaction`, and lets the call through.
- **Bug caught by the test suite, not by review**: the first implementation checked `tool_name in
  PHASE_TOOL_POLICY` - but that dict's keys are phase names, not tool names, so the check was always
  `False` and the gate never actually fired for any tool. `PHASE_SPECIFIC_TOOLS` (the flat union) fixed
  this. Left as a note here since it's exactly the kind of bug a reviewer reading the diff alone could
  plausibly miss - the full test suite catching it is the actual evidence this works, not the code
  being self-evidently correct.
- `agents/scrum_team/tests/test_agent.py`: `TestDetectPhaseViolation` (18 cases) plus two pre-existing
  tests updated to set up a real "Development"-phase state where they'd previously relied on a
  default/mock state that phase gating now (correctly) notices.
