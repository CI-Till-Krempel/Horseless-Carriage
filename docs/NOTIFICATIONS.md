[← Back to README](../README.md)

# Blocking Interactions & Notifications

The Scrum team is designed to run unsupervised during a sprint - which means a moment that
genuinely needs a human (a release blocked on a missing approval, the sprint budget running out)
can go unnoticed if the human isn't actively watching the session's chat. `blocking_interactions`
(see `agents/scrum_team/state.py`) is a persisted, checkpointed task list of exactly these moments,
independent of whatever's currently visible in the transcript - and every time one is recorded, a
configurable notifier is also fired so it's pushed somewhere a human will actually see it.

## The blocking-interactions list

Recorded via `record_blocking_interaction(kind, summary, detail="", tool_context=None)`
(`agents/scrum_team/tools/notifications.py`) - `kind` is a short free-text tag (e.g. `"approval"`,
`"critical_error"`), not a closed enum, so new call sites don't need a code change here. Two things
already call it automatically:

- `advance_story_stage(..., "Implemented")` and `create_release_pr`, when rejected for lack of a
  fresh `record_human_approval(...)` - the "absolutely necessary human feedback" case.
- `check_cost_budget_callback`'s halt branches (token budget exhausted, no USD budget configured,
  USD budget exhausted, or the LiteLLM proxy's own budget check failing) - the "critical tool error"
  case, since these halt the whole sprint for every agent until a human intervenes.

Query it with `list_blocking_interactions(include_resolved=False, tool_context=None)` (open items by
default, or the full history with `include_resolved=True`), and clear one with
`resolve_blocking_interaction(interaction_id, tool_context=None)` once it's actually been addressed
(e.g. a fresh approval was recorded, or the underlying issue was fixed). Entries are never deleted,
only marked resolved - `blocking_interactions` doubles as a history, not just a live queue.

Persisted the same way as `record_human_approval`/`add_impediment`: written into
`.hc/state.json` and checkpointed to the state repository's local git history on every call (see
[State Repository § Checkpointing and recovery](STATE-REPOSITORY.md#checkpointing-and-recovery)) -
so a blocking interaction survives a crash immediately after it's recorded, not just once something
else happens to save state later.

## The notification plugin interface

`Notifier` (`agents/scrum_team/tools/notifications.py`) is a small base class with one method,
`notify(interaction: dict) -> None`. A new integration - Slack, email, a generic webhook - is a new
subclass registered in `NOTIFIER_REGISTRY`; nothing about `record_blocking_interaction` or its
callers needs to change.

```python
class SlackNotifier(Notifier):
    name = "slack"

    def notify(self, interaction: dict) -> None:
        ...  # post interaction["summary"]/["detail"] to a configured webhook URL

NOTIFIER_REGISTRY["slack"] = SlackNotifier
```

Which notifiers actually fire is controlled by `NOTIFICATION_PLUGINS` in `.env` - a comma-separated
list of names from `NOTIFIER_REGISTRY`. Defaults to `"console"` if unset: `ConsoleNotifier` prints a
hard-to-miss banner to stderr, picked up by `docker compose logs agent` (or a foreground terminal)
with zero external configuration - the safety net every other notifier is layered on top of, not a
placeholder to be replaced. An unrecognized name in `NOTIFICATION_PLUGINS` is skipped with a warning
rather than failing every notification over one typo.

A notifier's own `notify()` failing (network error, bad webhook URL, ...) never prevents the
blocking interaction itself from being recorded, and never stops any other configured notifier from
still firing - each is called independently, best-effort.

## Tool calls in the CLI (`AGENT_MODE=cli`)

The ADK web UI (`AGENT_MODE=web`, the default) renders every tool call and its result in its own
tool-call panel, so this is a non-issue there. The plain interactive CLI (`adk run`, which
`run_agent.sh` invokes for `AGENT_MODE=cli`) is different: its REPL
(`google.adk.cli.cli.run_interactively`/`run_input_file`) only echoes events that carry `.text`
content - a pure tool call/tool result event has none, so **every tool call was completely invisible
in CLI mode**, including gated actions a human might expect to notice (e.g. a release PR blocked on
a missing approval).

`log_tool_invocation_callback` (`agents/scrum_team/agent.py`, registered as `before_tool_callback` on
every agent) closes this gap independently of whichever ADK frontend is running: it prints
`🔧 [AgentName] tool_name(arg_names)` to stderr for every tool call, so it shows up in the CLI
terminal itself and in `docker compose logs agent` for daemon mode. Only argument *names* are logged,
never values - tool arguments can carry large file contents or PR bodies, which shouldn't end up
dumped into logs. This is a passive trace only; it never blocks or alters a tool call, and is a
harmless duplicate of what the web UI already shows on its own.
