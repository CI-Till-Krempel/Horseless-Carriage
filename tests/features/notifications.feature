Feature: Notifications (blocking interactions, GH issue #53)
  record_blocking_interaction (agents/scrum_team/tools/notifications.py) is
  the single mechanism that pushes a "genuinely needs a human" moment (a
  rejected approval gate, a critical budget halt, an orchestrator stall)
  somewhere a human will actually notice - not just a tool return value the
  calling LLM might or might not paraphrase into the chat. It persists the
  event in blocking_interactions (state) and fires every configured
  Notifier plugin, unconditionally, regardless of whether any of them fail.

  Background:
    # notifications.py:61-78 (get_configured_notifiers) - NOTIFICATION_PLUGINS
    # is a comma-separated list of names in NOTIFIER_REGISTRY, defaulting to
    # "console" (the always-available, zero-config safety net) if unset.
    Given NOTIFICATION_PLUGINS is read from the environment (default "console")

  # notifications.py:69-77 - a typo'd/unknown plugin name degrades (skipped,
  # one warning printed) rather than failing the whole call over one bad entry.
  @automatable
  Scenario: An unknown notifier name in NOTIFICATION_PLUGINS is skipped, not fatal
    Given NOTIFICATION_PLUGINS="console,slck" (a typo)
    When get_configured_notifiers() is called
    Then it returns only the ConsoleNotifier instance
    And "WARNING: unknown notification plugin 'slck'" is printed once, to stderr
    And record_blocking_interaction still succeeds and still notifies "console"

  # notifications.py:120-124 (record_blocking_interaction's notify loop) -
  # one notifier raising an exception must never stop the interaction from
  # being recorded, nor stop any OTHER configured notifier from firing.
  @automatable
  Scenario: One notifier raising an exception does not block recording or other notifiers
    Given two notifiers are configured: one that always raises, one that always succeeds
    When record_blocking_interaction("critical_error", "budget halted", tool_context=tc) is called
    Then the interaction is still appended to state["blocking_interactions"]
    And save_state_to_repo is still called (the event is persisted immediately)
    And the always-succeeds notifier's notify() is still invoked
    And no exception propagates out of record_blocking_interaction

  # notifications.py:100-101 - a blank/whitespace-only summary is rejected
  # outright; this is not something a caller can silently get wrong.
  @automatable
  Scenario: A blocking interaction always needs a real summary
    When record_blocking_interaction("approval", "   ") is called
    Then it returns status "error" mentioning "summary is required"
    And nothing is appended to blocking_interactions

  # notifications.py:81-82 (_new_interaction_id) and :107-117 - every
  # interaction gets a unique, monotonically increasing id and starts
  # unresolved.
  @automatable
  Scenario: Every recorded interaction gets a fresh id and starts unresolved
    Given blocking_interactions already has one entry with id=1
    When record_blocking_interaction("stalled", "orchestrator idle") is called
    Then the new entry has id=2, resolved=False, resolved_at=None

  # notifications.py:129-148 (resolve_blocking_interaction) - marks an entry
  # resolved without deleting it; refuses to double-resolve.
  @automatable
  Scenario: Resolving a blocking interaction is idempotent-safe, not silently repeatable
    Given an interaction with id=3 exists and is not yet resolved
    When resolve_blocking_interaction(3) is called
    Then it returns status "ok" and the entry now has resolved=True
    But calling resolve_blocking_interaction(3) again returns status "error"
      mentioning "already resolved"

  # notifications.py:151-159 (list_blocking_interactions) - resolved entries
  # are hidden by default (only what's still waiting on a human), but stay
  # visible with include_resolved=True (full history).
  @automatable
  Scenario: list_blocking_interactions defaults to open-only, but keeps full history on request
    Given blocking_interactions has 2 resolved entries and 1 open entry
    When list_blocking_interactions() is called
    Then it returns count=1 (only the open entry)
    But list_blocking_interactions(include_resolved=True) returns count=3

  # notifications.py:38-53 (ConsoleNotifier) - the always-available default:
  # a hard-to-miss banner to stderr, picked up by `docker compose logs agent`
  # even with no external integration configured at all.
  @manual-qa
  Scenario: ConsoleNotifier prints a hard-to-miss banner visible in container logs
    Given NOTIFICATION_PLUGINS is unset (defaults to "console")
    When a blocking interaction is recorded during a real `docker compose up` run
    Then `docker compose logs agent` shows a "!"-banner containing
      "[ACTION NEEDED - <kind>] <summary>"
