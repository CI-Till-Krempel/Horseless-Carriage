# Issue

- Issue ID: ISSUE-0037
- Title: Repair/Reset/Delete Options For Corrupted State
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #85): "If a state json is corrupted, the setup assistant should offer an
option to try to repair it with help of the llm, or reset the state to the state persisted in the
git, or clear it completely (delete the json)."

**Investigation findings**:
- `load_state_from_repo()` (ISSUE-0024/GH issue #59) already auto-recovers from git when possible,
  but only checked `HEAD` - if the *latest* checkpoint commit's own snapshot was itself corrupted
  (e.g. a torn write got committed before anyone noticed), recovery gave up immediately even though
  an earlier, perfectly good commit might still exist.
- `check_state_repo.py` (host-side) already *detected* corruption via `validate_state.py`, but only
  ever reported it and exited - no remediation of any kind.
- `doctor.py` doesn't check `state.json`'s content at all (only the `specs/` directory structure).
- Worse than any of the above: `init_scrum_state()` called `load_state_from_repo()` but discarded its
  return value entirely (`_ = load_state_from_repo(tool_context)`), wrapped in a try/except that only
  catches real exceptions - `load_state_from_repo()` returns an error *dict*, it doesn't raise. So a
  corrupted-and-unrecoverable-even-from-git `state.json` was silently swallowed: the session just
  quietly started with blank/default state, with **no indication anything was ever wrong and no
  chance to intervene** - the actual, sharper form of the bug this issue is describing.
- "Repair with help of the LLM" genuinely cannot be done host-side (`setup_llm.py`/`check_state_repo.py`
  make no LLM calls at all - only the container-side agent has LLM access via the LiteLLM proxy), so
  that option specifically had to live in the agent's own tools/chat, not the host setup scripts.

## Acceptance Criteria
- Git-based recovery (both automatic and the new explicit tool) searches all of git history for
  `.hc/state.json`, not just `HEAD` - an earlier good checkpoint is found even if the latest one is
  also corrupted.
- A corrupted-and-unrecoverable `state.json` is no longer silently discarded during
  `init_scrum_state()` - it's flagged (`state_json_corrupted`), recorded as a blocking interaction
  (GH issue #53/ISSUE-0025), and surfaced to the human in the Orchestrator's very first message.
- Three new tools give the Orchestrator (and, through it, the human) all three remediation options
  the issue asks for: `get_corrupted_state_raw_content()` + `save_repaired_state(...)` ("repair with
  help of the LLM" - the Orchestrator itself reconstructs valid JSON from the raw corrupted text),
  `reset_state_from_git()` (explicit, on-demand history search), and `clear_corrupted_state()`
  (delete outright). All three refuse to act on a `state.json` that already parses fine - none of
  them can be used to reset or discard perfectly good state by mistake.
- `check_state_repo.py`, run interactively (a real terminal), offers the same reset/delete choice
  (minus LLM-assisted repair, which needs the agent's own LLM access) instead of just failing with a
  dead-end message. Non-interactive runs (CI, `doctor.py`) are unaffected.

## Notes
- The LLM-assisted repair option is deliberately split across two tools
  (`get_corrupted_state_raw_content` / `save_repaired_state`) rather than one that calls an LLM
  itself - the Orchestrator *is* the LLM already reasoning in this conversation; there's no need for
  a tool to make its own separate model call when the calling agent can just do the reasoning inline
  between one tool call and the next.
- `save_repaired_state`/`reset_state_from_git`/`clear_corrupted_state` all guard against acting on
  state that isn't actually corrupted (checked via the same `_parse_state_json` used by
  `load_state_from_repo`) - this is a deliberate safety net so these can't accidentally become a way
  to reset or nuke good state instead of a genuinely broken one.
- This does not touch `doctor.py` - it still only checks the cheap structural things (per its own
  documented scope); `check_state_repo.py` remains the "fuller picture" script for `state.json`
  content, now with remediation built in.

## Test Approach
- `agents/scrum_team/tests/test_state_persistence.py::TestLoadStateGitRecovery::
  test_recovers_an_earlier_commit_if_the_latest_one_is_also_corrupted` - history-walking recovery,
  not just `HEAD`.
- `agents/scrum_team/tests/test_state_persistence.py::TestStateRepairTools` (new) - all four
  remediation paths (raw-content read, repaired-state save, git reset, delete), each including the
  "refuses when not actually corrupted" guard.
- `agents/scrum_team/tests/test_scrum.py::TestInitScrumStateCorruptionSurfacing` (new) -
  unrecoverable corruption sets the flag + records a blocking interaction; recoverable corruption and
  "no file yet" do not.
- `agents/scrum_team/tests/test_agent.py::test_sprint_status_injection_surfaces_corrupted_state_notice`
  (new) - the first-message context names all three recovery tools when the flag is set.
- `tests/test_check_state_repo.py::TestInteractiveStateRepair` (new) - all three menu choices
  (reset/delete/leave-as-is, including the history-walk-past-a-corrupted-HEAD case), the
  non-interactive path unchanged, and default-on-empty-input behavior.
- `pytest tests/`: 297 passed, no regressions.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 266 passed,
  no regressions.

## Resolution
- `agents/scrum_team/tools/scrum.py`: `_recover_state_json_from_git` now walks git history (up to 50
  commits, newest first) instead of only checking `HEAD`; `init_scrum_state` now surfaces
  unrecoverable corruption instead of silently discarding it; four new tools -
  `get_corrupted_state_raw_content`, `save_repaired_state`, `reset_state_from_git`,
  `clear_corrupted_state`.
- `agents/scrum_team/tools/__init__.py`, `agents/scrum_team/agent.py`: new tools exported and given
  to the `ScrumOrchestrator`.
- `agents/scrum_team/agent.py`'s `sprint_status_injection_callback`: names all three recovery tools
  in the first-message context when `state_json_corrupted` is set.
- `check_state_repo.py`: new `_walk_git_history_for_valid_state_json`/`_offer_state_repair`; `run()`
  gains `interactive`/`prompt` params (default-detecting/`input`) and offers the repair menu when
  validation fails interactively.
- `docs/STATE-REPOSITORY.md`, `MANUAL.md`: document the new recovery options.
