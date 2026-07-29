# Issue

- Issue ID: ISSUE-0025
- Title: Blocking Human Feedback and Critical Tool Errors Went Unnoticed
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #53): "As the system is intended to run unsupervised during the sprint,
critical tool errors, or absolutely necessary human feedback might go unnoticed. Implement a task
list style list of blocking interactions, and notify the user about it. Add a plugin interface so
different integrations can later be written for the notifications."

Investigation found two existing "needs a human" mechanisms, neither of which pushed anything to
anyone: `record_human_approval` (`agents/scrum_team/tools/scrum.py`) only records approvals *after*
a human acts - nothing recorded that the team was *currently* waiting, when `advance_story_stage(...,
"Implemented")` or `create_release_pr` rejected a call for lack of one. Separately,
`check_cost_budget_callback` (`agents/scrum_team/agent.py`) already detects genuinely critical halts
(token/USD budget exhausted, the LiteLLM proxy's own budget check failing) and injects a
`🚫`/`❌`-prefixed message into the model's own response stream - but that's purely conversational; a
human not actively watching that session's chat would never know the team stopped.

## Acceptance Criteria
- A `blocking_interactions` list in `ScrumState`, persisted and checkpointed the same way as
  `human_approvals`/`impediment_log` - the "task list style list of blocking interactions" the issue
  asks for, with entries surviving independent of the chat transcript.
- `record_blocking_interaction(kind, summary, detail="", tool_context=None)`,
  `resolve_blocking_interaction(interaction_id, tool_context=None)`, and
  `list_blocking_interactions(include_resolved=False, tool_context=None)` tools - available to
  ScrumMaster (which already owns `impediment_log`/`record_human_approval`) and the root orchestrator
  (`list_blocking_interactions` only, for its own situational awareness).
- Every recorded interaction fires a configurable notifier - a `Notifier` base class with one
  `notify(interaction) -> None` method, a `NOTIFIER_REGISTRY` new integrations register themselves
  into, and a `NOTIFICATION_PLUGINS` env var (comma-separated names) selecting which fire. This is
  the "plugin interface so different integrations can later be written" the issue asks for.
- A zero-config default notifier (`ConsoleNotifier`) that always works - prints a hard-to-miss banner
  to stderr, picked up by `docker compose logs agent` - so notification works out of the box without
  requiring a Slack/email/webhook integration to be written first.
- Wired into the two existing "absolutely necessary human feedback" rejection points
  (`advance_story_stage`'s Implemented gate, `create_release_pr`'s gate) and the four "critical tool
  error" halt branches in `check_cost_budget_callback` (token budget exceeded, no USD budget
  configured, USD budget exceeded, budget-check request exception).
- A single bad/misconfigured notifier must never prevent the interaction from being recorded, or stop
  any other configured notifier from firing.

## Notes
- **Not wired in**: the "no budget-capped virtual key yet" halt in `check_cost_budget_callback` -
  that's routine per-agent bootstrap (resolves itself once `create_litellm_virtual_key` runs, normally
  without any human involved), not a genuine "needs a human" moment; notifying on it would produce a
  false alarm on effectively every fresh sprint start.
- **Not implemented**: any real external integration (Slack, email, a webhook). The issue's ask was
  the plugin *interface*, not a specific integration - `docs/NOTIFICATIONS.md` shows exactly what a
  new `Notifier` subclass looks like, and adding one is a follow-up someone can now pick up
  independently, entirely additive (no changes needed to `record_blocking_interaction` or its
  callers).
- Where the gap lived: no structured "we're waiting on you" state existed anywhere before this -
  `record_human_approval` only ever recorded the *answer*, never the *question*; the budget-halt
  callbacks only ever spoke into the chat, never persisted or notified independently of it.

## Test Approach
- `agents/scrum_team/tests/test_notifications.py` (new) - `record_blocking_interaction` (entry shape,
  blank-summary rejection, incrementing ids, persistence via `save_state_to_repo`, every configured
  notifier fires, one failing notifier doesn't break recording), `resolve_blocking_interaction`
  (marks resolved, already-resolved/unknown-id rejected), `list_blocking_interactions` (open-only
  default, `include_resolved=True`), `ConsoleNotifier` (prints summary/detail, handles missing
  detail), `get_configured_notifiers` (defaults to console, skips+warns on an unknown plugin name).
- `agents/scrum_team/tests/test_requirements.py::test_implemented_rejection_records_blocking_interaction`
  and `test_github.py::test_create_release_pr_rejection_records_blocking_interaction` - the two
  approval-gate wiring points.
- `agents/scrum_team/tests/test_agent.py::TestCriticalHaltNotifications` - all four budget-halt
  branches record a `critical_error` interaction; `_sync_roadmap_on_exhaustion_once` (pre-existing,
  unrelated behavior that does real git operations) is patched away in each so these tests stay
  scoped to the notification wiring this issue adds, rather than exercising or risking that separate
  mechanism.
- Run via `docker compose --env-file .env.test run --rm --entrypoint "" -e PYTHONPATH=/app agent
  pytest agents/scrum_team/tests` (per `docs/TESTING.md`): 206 passed, no regressions (up from 191
  before this change).
- `pytest tests/` (host-side suite, unaffected by this change): 215 passed.

## Resolution
- `agents/scrum_team/state.py`: added `blocking_interactions: List[Dict[str, Any]]`.
- `agents/scrum_team/tools/notifications.py` (new): `Notifier`/`ConsoleNotifier`/`NOTIFIER_REGISTRY`/
  `get_configured_notifiers`, plus `record_blocking_interaction`/`resolve_blocking_interaction`/
  `list_blocking_interactions`.
- `agents/scrum_team/tools/scrum.py`: `blocking_interactions` added to `REPO_STATE_KEYS` and
  `init_scrum_state`'s defaults.
- `agents/scrum_team/tools/requirements.py`, `agents/scrum_team/tools/github.py`: the two approval
  gates now call `record_blocking_interaction` on rejection.
- `agents/scrum_team/agent.py`: `_notify_critical_halt` helper, called from the four budget-halt
  branches; new tools registered for ScrumMaster (`record_blocking_interaction`,
  `resolve_blocking_interaction`, `list_blocking_interactions`) and the root orchestrator
  (`list_blocking_interactions`).
- `agents/scrum_team/prompts.py`: SM_PROMPT updated with the new tools and guidance to check/resolve
  the list during event facilitation.
- `docs/NOTIFICATIONS.md` (new), linked from `README.md`; `.env.example` documents
  `NOTIFICATION_PLUGINS`.
