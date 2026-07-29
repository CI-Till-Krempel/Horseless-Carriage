"""
Blocking-interaction tracking + a pluggable notification interface
(GH issue #53): the team runs unsupervised during a sprint, so a critical
tool error or a gate that's genuinely waiting on a human must be pushed
somewhere a human will actually notice - not just left as a tool return
value the calling LLM agent might (or might not) paraphrase into the chat.

blocking_interactions (see state.py) is the "task list style list of
blocking interactions" the issue asks for - a persisted, checkpointed
(save_state_to_repo already commits it - see ISSUE-0024) record of every
interaction that has ever needed a human, open or resolved, independent of
whatever's currently visible in the chat transcript.

Notifier is the "plugin interface so different integrations can later be
written" - a new integration (Slack, email, a webhook) is a new Notifier
subclass registered in NOTIFIER_REGISTRY; nothing about
record_blocking_interaction or its callers needs to change.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List


class Notifier:
    """Base class for a notification plugin. Subclass and override
    notify(); register the subclass in NOTIFIER_REGISTRY below to make it
    selectable via the NOTIFICATION_PLUGINS env var."""

    name = "base"

    def notify(self, interaction: Dict[str, Any]) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """Always-available, zero-config default: prints a hard-to-miss
    banner to stderr, picked up by `docker compose logs agent` (or a
    foreground terminal) regardless of whether any external integration
    is configured. Every other notifier is opt-in via NOTIFICATION_PLUGINS;
    this one is the safety net that never depends on external setup."""

    name = "console"

    def notify(self, interaction: Dict[str, Any]) -> None:
        banner = "!" * 70
        print(banner, file=sys.stderr)
        print(f"[ACTION NEEDED - {interaction.get('kind', 'blocking')}] {interaction.get('summary', '')}", file=sys.stderr)
        if interaction.get("detail"):
            print(interaction["detail"], file=sys.stderr)
        print(banner, file=sys.stderr)


NOTIFIER_REGISTRY: Dict[str, type] = {
    "console": ConsoleNotifier,
}


def get_configured_notifiers() -> List[Notifier]:
    """Which notifiers to fire for a new blocking interaction -
    NOTIFICATION_PLUGINS (.env) is a comma-separated list of names in
    NOTIFIER_REGISTRY. Defaults to just "console" (always available, no
    setup needed) if unset. An unknown name is skipped with a warning
    printed once rather than failing the whole call over one bad entry -
    a typo in NOTIFICATION_PLUGINS should degrade, not break notifications
    entirely."""
    raw = os.environ.get("NOTIFICATION_PLUGINS", "console")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    notifiers = []
    for name in names:
        cls = NOTIFIER_REGISTRY.get(name)
        if cls is None:
            print(f"WARNING: unknown notification plugin {name!r} in NOTIFICATION_PLUGINS - skipping.", file=sys.stderr)
            continue
        notifiers.append(cls())
    return notifiers


def _new_interaction_id(existing: List[Dict[str, Any]]) -> int:
    return max((entry.get("id", 0) for entry in existing), default=0) + 1


def record_blocking_interaction(kind: str, summary: str, detail: str = "", tool_context=None) -> Dict[str, Any]:
    """
    Records a blocking interaction - an "absolutely necessary human
    feedback" moment (e.g. a rejected approval-gated action) or a critical
    tool error (GH issue #53) - in the blocking_interactions state list,
    and fires every configured notifier. kind is a short free-text tag
    ("approval", "critical_error", ...) for filtering, not a closed enum
    like record_human_approval's approval_type - new kinds don't need a
    code change here.

    Persists immediately via save_state_to_repo (same convention as
    record_human_approval/add_impediment) so an interaction survives a
    crash right after it's recorded, not just once something else happens
    to save state later.
    """
    if not summary or not summary.strip():
        return {"status": "error", "message": "summary is required"}

    from .scrum import save_state_to_repo

    s = tool_context.state
    interactions = list(s.get("blocking_interactions", []) or [])
    entry = {
        "id": _new_interaction_id(interactions),
        "kind": kind,
        "summary": summary.strip(),
        "detail": detail.strip() if detail else "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "resolved_at": None,
    }
    interactions.append(entry)
    s["blocking_interactions"] = interactions
    _ = save_state_to_repo(tool_context)

    for notifier in get_configured_notifiers():
        try:
            notifier.notify(entry)
        except Exception:
            pass  # one bad notifier must never stop the interaction from being recorded

    return {"status": "ok", "interaction": entry}


def resolve_blocking_interaction(interaction_id: int, tool_context=None) -> Dict[str, Any]:
    """Marks a previously recorded blocking interaction resolved - e.g.
    once record_human_approval covers the gate that raised it, or a
    retried action succeeds. Doesn't delete the entry, so
    list_blocking_interactions(include_resolved=True) still shows it as
    history."""
    from .scrum import save_state_to_repo

    s = tool_context.state
    interactions = list(s.get("blocking_interactions", []) or [])
    for entry in interactions:
        if entry.get("id") == interaction_id:
            if entry.get("resolved"):
                return {"status": "error", "message": f"Interaction {interaction_id} is already resolved."}
            entry["resolved"] = True
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            s["blocking_interactions"] = interactions
            _ = save_state_to_repo(tool_context)
            return {"status": "ok", "interaction": entry}
    return {"status": "error", "message": f"No blocking interaction with id {interaction_id}."}


def list_blocking_interactions(include_resolved: bool = False, tool_context=None) -> Dict[str, Any]:
    """The task-list view GH issue #53 asks for: every interaction still
    waiting on a human by default, or the full history including resolved
    ones if include_resolved=True."""
    s = tool_context.state
    interactions = list(s.get("blocking_interactions", []) or [])
    if not include_resolved:
        interactions = [entry for entry in interactions if not entry.get("resolved")]
    return {"status": "ok", "interactions": interactions, "count": len(interactions)}
