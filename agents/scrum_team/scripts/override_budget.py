#!/usr/bin/env python3
"""
Human override for a sprint stuck on a 🚫 [TOKEN BUDGET EXCEEDED] / 🚫 [USD
BUDGET EXCEEDED] halt (check_cost_budget_callback, agents/scrum_team/agent.py).

The only previously-documented recovery was: raise SPRINT_TOKEN_BUDGET/
TOTAL_USD_BUDGET in .env and restart the agent container. That's slow and
heavy-handed for "let this one stuck sprint finish" - this script instead
writes a live budgets.total/total_usd bump straight into the running ADK
session (via the same sqlite session store run_agent.sh points `adk web`/
`adk run` at), so the very next agent turn sees it with no restart needed.

Intended to be invoked from the HOST via `python3 run.py override-budget`
(see run.py), which execs this inside the already-running `agent` container.
Can also be run directly inside the container for the same effect.

See sync_budgets_from_env (tools/scrum.py) and sprint_budget_reset_state_delta
(tools/budget.py) for how this override is kept from going stale: it's
scoped to exactly the sprint it was applied during, auto-cleared the moment
the next sprint starts.
"""
import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

# Same hardcoded path run_agent.sh's own DB_FILE points `adk web`/`adk run`
# at (--session_service_uri) - not configurable there either, so no env var
# for it here.
DB_FILE = "/app/sessions/adk_sessions.db"


def _resolve_session(app_name, user_id, session_id):
    """Pick the (app_name, user_id, session_id) triple to target. Explicit
    flags win outright; any left unset fall back to whichever session in
    the store was updated most recently - the same "most recent wins"
    heuristic run_agent.sh's own startup banner already uses, and a safe
    bet since the stuck sprint is virtually always the one most recently
    active."""
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"No session store found at {DB_FILE} - is the agent container running?")

    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT app_name, user_id, id FROM sessions "
            "WHERE (? IS NULL OR app_name = ?) AND (? IS NULL OR user_id = ?) "
            "AND (? IS NULL OR id = ?) "
            "ORDER BY update_time DESC LIMIT 1",
            (app_name, app_name, user_id, user_id, session_id, session_id),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise SystemExit("No matching session found in the session store - check --app-name/--user-id/--session-id.")
    return row


async def _apply_override(app_name: str, user_id: str, session_id: str, tokens, usd, reason: str) -> None:
    try:
        from google.adk.sessions import DatabaseSessionService
        from google.adk.events import Event
        from google.adk.events.event_actions import EventActions
    except ImportError as e:
        # ADK's DatabaseSessionService - the exact same class run_agent.sh's
        # own --session_service_uri sqlite:///... already depends on for
        # every persisted sprint - is gated behind google-adk's own [db]
        # extra (sqlalchemy). If that's missing, the agent container's
        # whole persistent-session feature is broken, not just this script -
        # raise that clearly instead of a bare traceback.
        raise SystemExit(
            f"Could not import ADK's database session service ({e}). This agent image is likely "
            "missing google-adk's 'db' extra (sqlalchemy) - add it to requirements.txt (e.g. "
            "`google-adk[db]==<version>`) and rebuild the agent image. Note: if this is missing, "
            "persisted sessions (the sqlite DB this very script and run_agent.sh's "
            "--session_service_uri both rely on) don't actually work at all right now, independent "
            "of this override."
        ) from e

    service = DatabaseSessionService(db_url=f"sqlite:///{DB_FILE}")
    session = await service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    if session is None:
        raise SystemExit(f"Session app={app_name} user={user_id} id={session_id} not found.")

    budgets = dict(session.state.get("budgets") or {})
    override = dict(session.state.get("budget_human_override") or {})
    # blocking_interactions is the established audit-trail list (see
    # record_blocking_interaction, tools/notifications.py) - appending here
    # directly (no live agent turn exists to call that tool through) keeps
    # this override visible in the same place every other human-facing
    # incident already shows up.
    interactions = list(session.state.get("blocking_interactions") or [])

    changes = []
    if tokens is not None:
        changes.append(f"token budget {budgets.get('total', 0):,} -> {tokens:,}")
        budgets["total"] = tokens
        override["total"] = tokens
    if usd is not None:
        changes.append(f"USD budget ${budgets.get('total_usd', 0):,.2f} -> ${usd:,.2f}")
        budgets["total_usd"] = usd
        override["total_usd"] = usd

    now = datetime.now(timezone.utc).isoformat()
    next_id = max((entry.get("id", 0) for entry in interactions), default=0) + 1
    interactions.append({
        "id": next_id,
        "kind": "budget_override",
        "summary": "Human override: " + "; ".join(changes),
        "detail": reason,
        "created_at": now,
        "resolved": True,
        "resolved_at": now,
    })

    state_delta = {
        "budgets": budgets,
        "budget_human_override": override,
        "blocking_interactions": interactions,
    }
    event = Event(author="human_override", actions=EventActions(state_delta=state_delta))
    await service.append_event(session, event)

    print(f"Applied to session app={app_name} user={user_id} id={session_id}:")
    print("  " + "; ".join(changes))
    print("This override is scoped to the current sprint only - it's cleared automatically "
          "the moment the next sprint starts (see sprint_budget_reset_state_delta, tools/budget.py).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tokens", type=int, help="New sprint token budget ceiling")
    parser.add_argument("--usd", type=float, help="New sprint USD budget ceiling")
    parser.add_argument("--reason", required=True, help="Why - recorded in blocking_interactions for the audit trail")
    parser.add_argument("--app-name", default=None, help="Defaults to the most recently updated session")
    parser.add_argument("--user-id", default=None, help="Defaults to the most recently updated session")
    parser.add_argument("--session-id", default=None, help="Defaults to the most recently updated session")
    args = parser.parse_args()

    if args.tokens is None and args.usd is None:
        parser.error("pass --tokens and/or --usd")

    app_name, user_id, session_id = _resolve_session(args.app_name, args.user_id, args.session_id)
    print(f"Targeting session: app={app_name} user={user_id} id={session_id}")
    asyncio.run(_apply_override(app_name, user_id, session_id, args.tokens, args.usd, args.reason))


if __name__ == "__main__":
    main()
