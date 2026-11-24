#!/usr/bin/env python3
"""
Inspect and repair a project whose sprint is stuck on an exhausted budget
(SPEC-ISSUE-0096). The offline counterpart to `python3 run.py override-budget`
(SPEC-ISSUE-0092), which raises the limit live inside the running container
but can neither reset usage nor be undone before the next sprint.

    python3 reset_budget.py                       # read-only status report (default)
    python3 reset_budget.py --token-budget 8000000  # raise SPRINT_TOKEN_BUDGET in .env
    python3 reset_budget.py --usd-budget 25       # raise TOTAL_USD_BUDGET in .env (+ LiteLLM)
    python3 reset_budget.py --reset-usage         # zero this sprint's token counters
    python3 reset_budget.py --clear-override      # drop a live override-budget pin

Why this exists: the halt messages say "raise SPRINT_TOKEN_BUDGET in .env and
restart", which works for the *limit* (sync_budgets_from_env, agents/
scrum_team/tools/scrum.py, re-reads it every turn) - but nothing outside
start_sprint ever resets the accumulated *usage* (token_usage), and that
usage is persisted in two places that both have to agree:

- <STATE_REPO_PATH>/.hc/state.json - what a NEW ADK session loads on its
  first turn (ensure_state_initialized_callback -> load_state_from_repo).
- sessions/adk_sessions.db (ADK's sqlite session store) - what a RESUMED
  session carries forward; it overwrites state.json again on its next save.

Editing only one of them doesn't stick. This script edits both, using the
same keys start_sprint's own reset clears (sprint_budget_reset_state_delta,
agents/scrum_team/tools/budget.py - a test pins BUDGET_RESET_DELTA below to
that function so they can't drift apart, the SPEC-ISSUE-0049 failure mode).

Deliberately does NOT reset LiteLLM's recorded USD spend: TOTAL_USD_BUDGET is
a whole-engagement ceiling by design (docs/BUDGET.md section 2). To spend
more, raise the ceiling (--usd-budget) - an explicit human decision - rather
than erasing the record of what was already spent.

Stdlib only (plus this repo's own lib_env/lib_docker), so it runs on the
host without the agent container or its Python dependencies.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import lib_docker
import lib_env

# Budget-related subset of sprint_budget_reset_state_delta() (agents/
# scrum_team/tools/budget.py). Only the budget counters and the sticky
# "already halted/warned" flags - not sprint_report/transcript paths etc.,
# which belong to the real sprint boundary, not to a mid-sprint budget repair.
BUDGET_RESET_DELTA = {
    "token_usage": {"total": 0, "agents": {}},
    "budget_exhaustion_synced": False,
    "critical_halt_notified": False,
    "_budget_warning_pct_fired": 0,
}

TOKEN_HALT_MARKER = "[TOKEN BUDGET EXCEEDED]"
USD_HALT_MARKER = "[USD BUDGET EXCEEDED]"

ADK_APP_NAME = "scrum_team"
LITELLM_BUDGET_ID = "scrum-sprint-budget"
# Mirrors the in-code fallbacks (check_cost_budget_callback / sync_budgets_from_env).
DEFAULT_TOKEN_BUDGET = 5_000_000
DEFAULT_USD_BUDGET = 10.0


def fresh_reset_delta() -> dict:
    """A deep copy of BUDGET_RESET_DELTA, so callers never share nested dicts."""
    return json.loads(json.dumps(BUDGET_RESET_DELTA))


def apply_budget_reset(state: dict, resolve_markers: tuple = ()) -> dict:
    """Returns a copy of `state` with BUDGET_RESET_DELTA applied and every
    still-open blocking interaction whose summary contains one of
    resolve_markers marked resolved (kept as history, same as
    resolve_blocking_interaction in tools/notifications.py)."""
    new_state = json.loads(json.dumps(state))
    new_state.update(fresh_reset_delta())
    resolve_halts(new_state, resolve_markers)
    return new_state


def resolve_halts(state: dict, markers: tuple) -> int:
    """Marks open blocking_interactions that came from a budget halt (summary
    contains one of markers) resolved, in place. Returns how many changed."""
    if not markers:
        return 0
    from datetime import datetime, timezone
    count = 0
    for entry in state.get("blocking_interactions") or []:
        if entry.get("resolved"):
            continue
        summary = entry.get("summary") or ""
        if any(marker in summary for marker in markers):
            entry["resolved"] = True
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            count += 1
    return count


def set_budget_limits(state: dict, token_budget=None, usd_budget=None) -> dict:
    """Copy of state with budgets.total / budgets.total_usd updated - the same
    fields sync_budgets_from_env writes from .env on every turn.

    Also unpins that key from budget_human_override (the live
    `run.py override-budget` marker, SPEC-ISSUE-0092): while a key is pinned
    there, sync_budgets_from_env ignores .env for it, so the new .env value
    this script just wrote would otherwise be silently ignored until the
    next sprint starts."""
    new_state = json.loads(json.dumps(state))
    budgets = new_state.get("budgets") or {}
    unpin = []
    if token_budget is not None:
        budgets["total"] = int(token_budget)
        unpin.append("total")
    if usd_budget is not None:
        budgets["total_usd"] = float(usd_budget)
        unpin.append("total_usd")
    new_state["budgets"] = budgets
    if unpin:
        new_state = clear_human_override(new_state, unpin)
    return new_state


def clear_human_override(state: dict, keys=None) -> dict:
    """Copy of state with the `run.py override-budget` pin removed for `keys`
    (default: all of it), so .env applies again from the next turn - the
    same value sprint_budget_reset_state_delta() would leave at the next
    sprint start (None). budgets.* itself is left as-is; sync_budgets_from_env
    re-syncs it from .env on the very next turn once unpinned."""
    new_state = json.loads(json.dumps(state))
    override = dict(new_state.get("budget_human_override") or {})
    for key in (keys if keys is not None else list(override)):
        override.pop(key, None)
    if "budget_human_override" in new_state or override:
        new_state["budget_human_override"] = override or None
    return new_state


# --- state.json ------------------------------------------------------------

def state_file_path(state_repo: Path) -> Path:
    return Path(state_repo) / ".hc" / "state.json"


def read_state_json(path: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state_json(path: Path, state: dict) -> None:
    """Same format and atomic temp-file + os.replace as _write_state_atomically
    (tools/scrum.py), so the agent reads it back identically."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def checkpoint_state_commit(state_repo: Path, message: str) -> bool:
    """Commits only .hc/state.json in the state repo's local git history (the
    same restorable-checkpoint convention as _checkpoint_state_commit), so the
    repair itself can be reverted. Never pushes. False if not a git repo or
    nothing changed."""
    state_repo = Path(state_repo)
    if not (state_repo / ".git").exists():
        return False
    subprocess.run(["git", "add", ".hc/state.json"], cwd=state_repo, capture_output=True)
    result = subprocess.run(
        ["git", "commit", "-m", message, "--", ".hc/state.json"],
        cwd=state_repo, capture_output=True, text=True,
    )
    return result.returncode == 0


# --- ADK session store -----------------------------------------------------

def list_sessions(db_path: Path) -> list:
    """[(user_id, session_id, update_time, state_dict)] for the scrum_team app,
    most recently updated first. Empty if the DB doesn't exist/has no table."""
    if not Path(db_path).is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT user_id, id, update_time, state FROM sessions WHERE app_name = ? ORDER BY update_time DESC",
            (ADK_APP_NAME,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    sessions = []
    for user_id, session_id, update_time, raw in rows:
        try:
            state = json.loads(raw) if raw else {}
        except ValueError:
            state = {}
        sessions.append((user_id, session_id, update_time, state if isinstance(state, dict) else {}))
    return sessions


def write_session_state(db_path: Path, user_id: str, session_id: str, state: dict) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions SET state = ? WHERE app_name = ? AND user_id = ? AND id = ?",
            (json.dumps(state), ADK_APP_NAME, user_id, session_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- LiteLLM ---------------------------------------------------------------

def update_litellm_budget(base_url: str, master_key: str, max_budget: float, timeout: int = 5):
    """POST /budget/update for the shared scrum-sprint-budget object - the
    same call create_litellm_virtual_key makes, which otherwise only happens
    when a role's key is (re)created. Returns (ok, message)."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/budget/update",
        data=json.dumps({"budget_id": LITELLM_BUDGET_ID, "max_budget": max_budget}).encode(),
        headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)


def litellm_host_url(env_path: Path) -> str:
    """The proxy as reachable from the host (not the in-container
    http://litellm:4000 LITELLM_PROXY_API_BASE points at)."""
    port = lib_env.read_env_var(env_path, "LITELLM_HOST_PORT") or "4000"
    return f"http://localhost:{port}"


# --- reporting -------------------------------------------------------------

def effective_token_limit(env_value: str, state: dict) -> int:
    """What check_cost_budget_callback will enforce after the next restart:
    a live `run.py override-budget` pin wins (sync_budgets_from_env skips .env
    for a pinned key), then .env (re-synced every turn), then state, then the
    default."""
    pinned = ((state or {}).get("budget_human_override") or {}).get("total")
    if pinned:
        return int(pinned)
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    total = ((state or {}).get("budgets") or {}).get("total") or 0
    return total if total > 0 else DEFAULT_TOKEN_BUDGET


def describe_state(label: str, state: dict, token_limit: int) -> list:
    budgets = state.get("budgets") or {}
    usage = state.get("token_usage") or {}
    used = usage.get("total") or 0
    pct = f"{used / token_limit:.0%}" if token_limit else "n/a"
    lines = [
        f"{label}:",
        f"  sprint_number:      {state.get('sprint_number', '?')}",
        f"  token usage:        {used:,} / {token_limit:,} ({pct})"
        + ("  <-- EXHAUSTED" if token_limit and used >= token_limit else ""),
        f"  budgets in state:   total={budgets.get('total')}, total_usd={budgets.get('total_usd')}, "
        f"last seen USD spend={budgets.get('current_usd_spend')}",
    ]
    agents = usage.get("agents") or {}
    if agents:
        lines.append("  per-role usage:     " + ", ".join(f"{k}={v:,}" for k, v in sorted(agents.items())))
    open_halts = [
        e for e in (state.get("blocking_interactions") or [])
        if not e.get("resolved") and any(m in (e.get("summary") or "") for m in (TOKEN_HALT_MARKER, USD_HALT_MARKER))
    ]
    if open_halts:
        lines.append(f"  open budget halts:  {len(open_halts)} (latest: {open_halts[-1].get('summary', '')[:90]})")
    override = state.get("budget_human_override") or {}
    if override:
        lines.append(f"  live override pin:  {override}  (from `run.py override-budget`; .env is ignored for "
                     "these keys until the next sprint or --clear-override)")
    return lines


def agent_running(repo_root: Path) -> bool:
    compose_args = lib_docker.compose_file_args(repo_root) + lib_docker.compose_project_args("dev", repo_root)
    return "agent" in lib_docker.compose_running_services(compose_args, cwd=repo_root)


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --- main ------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Inspect and repair a sprint stuck on an exhausted token/USD budget. "
                    "Read-only unless a mutating flag is given. See docs/BUDGET.md "
                    "'Recovering From A Stuck Budget Halt'. For a live raise while the agent keeps "
                    "running, use `python3 run.py override-budget` instead.",
    )
    p.add_argument("--token-budget", type=int, metavar="TOKENS",
                   help="Set SPRINT_TOKEN_BUDGET in .env (and budgets.total in state).")
    p.add_argument("--usd-budget", type=float, metavar="USD",
                   help="Set TOTAL_USD_BUDGET in .env (and budgets.total_usd in state), and update "
                        "LiteLLM's scrum-sprint-budget max_budget if the proxy is reachable.")
    p.add_argument("--reset-usage", action="store_true",
                   help="Zero this sprint's token_usage and clear the sticky halt/warning flags, in "
                        "both .hc/state.json and the ADK session store. Does not touch USD spend.")
    p.add_argument("--clear-override", action="store_true",
                   help="Remove a live `run.py override-budget` pin (budget_human_override) from the ADK "
                        "session, so .env's SPRINT_TOKEN_BUDGET/TOTAL_USD_BUDGET apply again on the next "
                        "turn instead of only at the next sprint start.")
    target = p.add_mutually_exclusive_group()
    target.add_argument("--session-id", help="Only edit this ADK session (default: the most recent one).")
    target.add_argument("--all-sessions", action="store_true", help="Edit every stored scrum_team session.")
    p.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation.")
    p.add_argument("--force", action="store_true",
                   help="Proceed even though the agent container is running (not recommended: a live "
                        "session can overwrite the edit on its next save).")
    return p.parse_args(argv)


def run(repo_root: Path, argv=None) -> int:
    args = parse_args(argv)
    repo_root = Path(repo_root)
    env_path = repo_root / ".env"
    if not env_path.is_file():
        print("ERROR: .env not found - run this from your Horseless Carriage checkout (or run setup_all.py first).")
        return 1

    for name, value in (("--token-budget", args.token_budget), ("--usd-budget", args.usd_budget)):
        if value is not None and value <= 0:
            print(f"ERROR: {name} must be positive (the agent would replace 0 with its default anyway).")
            return 1

    state_repo_str = lib_env.read_env_var(env_path, "STATE_REPO_PATH")
    state_repo = Path(state_repo_str).expanduser() if state_repo_str else None
    state_path = state_file_path(state_repo) if state_repo else None
    repo_state = read_state_json(state_path) if state_path and state_path.is_file() else None
    db_path = repo_root / "sessions" / "adk_sessions.db"
    sessions = list_sessions(db_path)

    env_token = lib_env.read_env_var(env_path, "SPRINT_TOKEN_BUDGET")
    env_usd = lib_env.read_env_var(env_path, "TOTAL_USD_BUDGET") or lib_env.read_env_var(env_path, "SPRINT_USD_BUDGET")

    # --- status (always printed) ---
    print("--- Budget status ---")
    print(f".env: SPRINT_TOKEN_BUDGET={env_token or '(unset)'}  TOTAL_USD_BUDGET={env_usd or '(unset)'}")
    if repo_state is not None:
        limit = effective_token_limit(env_token, repo_state)
        print("\n".join(describe_state(f"State repo ({state_path})", repo_state, limit)))
    else:
        print(f"State repo: no readable .hc/state.json at {state_path} "
              "(run check_state_repo.py if you expected one)")
    if sessions:
        user_id, session_id, _, sstate = sessions[0]
        limit = effective_token_limit(env_token, sstate)
        print("\n".join(describe_state(f"Most recent ADK session ({session_id}, {len(sessions)} stored)", sstate, limit)))
    else:
        print(f"ADK session store: no scrum_team sessions in {db_path}")

    if args.token_budget is None and args.usd_budget is None and not args.reset_usage and not args.clear_override:
        print("\nRead-only - nothing changed. To unstick a sprint, see docs/BUDGET.md "
              "'Recovering From A Stuck Budget Halt', e.g.:\n"
              "  python3 run.py override-budget --tokens 8000000 --reason \"...\"  # live, agent keeps running\n"
              "  python3 reset_budget.py --reset-usage           # agent stopped: same sprint, fresh token allowance\n"
              "  python3 reset_budget.py --token-budget 8000000  # agent stopped: raise the per-sprint limit\n"
              "  python3 reset_budget.py --usd-budget 25         # agent stopped: raise the USD ceiling (+ LiteLLM)\n"
              "  python3 reset_budget.py --clear-override        # agent stopped: drop a live override pin")
        return 0

    # --- mutating path ---
    if agent_running(repo_root) and not args.force:
        print("\nERROR: the agent container is running. Stop it first so a live session can't overwrite "
              "this edit on its next save:\n"
              "  docker compose " + " ".join(lib_docker.compose_file_args(repo_root) + lib_docker.compose_project_args("dev", repo_root))
              + " stop agent\n"
              "(or pass --force to edit anyway).")
        return 1

    if args.session_id:
        targets = [s for s in sessions if s[1] == args.session_id]
        if not targets:
            print(f"ERROR: no scrum_team session with id {args.session_id} in {db_path}.")
            return 1
    elif args.all_sessions:
        targets = sessions
    else:
        targets = sessions[:1]

    plan = []
    if args.token_budget is not None:
        plan.append(f"set SPRINT_TOKEN_BUDGET={args.token_budget} in .env")
    if args.usd_budget is not None:
        plan.append(f"set TOTAL_USD_BUDGET={args.usd_budget:g} in .env and LiteLLM's {LITELLM_BUDGET_ID}")
    if args.reset_usage:
        plan.append("zero token_usage and clear budget halt/warning flags")
    if args.clear_override:
        plan.append("remove the live override-budget pin (budget_human_override)")
    where = [".env"] if (args.token_budget is not None or args.usd_budget is not None) else []
    if repo_state is not None:
        where.append(str(state_path))
    where += [f"ADK session {t[1]}" for t in targets]
    print("\nAbout to: " + "; ".join(plan))
    print("In: " + (", ".join(where) or "(nothing found to edit)"))
    if not args.yes and not _confirm("Proceed?"):
        print("Aborted - nothing changed.")
        return 1

    # .env
    if args.token_budget is not None:
        lib_env.update_env_var(env_path, "SPRINT_TOKEN_BUDGET", str(args.token_budget))
    if args.usd_budget is not None:
        lib_env.update_env_var(env_path, "TOTAL_USD_BUDGET", f"{args.usd_budget:g}")

    markers = ()
    if args.reset_usage or args.token_budget is not None:
        markers += (TOKEN_HALT_MARKER,)
    if args.usd_budget is not None:
        markers += (USD_HALT_MARKER,)

    def _transform(state: dict) -> dict:
        state = set_budget_limits(state, args.token_budget, args.usd_budget)
        if args.clear_override:
            state = clear_human_override(state)
        if args.reset_usage:
            return apply_budget_reset(state, markers)
        resolve_halts(state, markers)
        return state

    if repo_state is not None:
        write_state_json(state_path, _transform(repo_state))
        committed = checkpoint_state_commit(state_repo, "checkpoint: reset_budget.py budget repair")
        print(f"[OK] Updated {state_path}" + (" (committed as a local checkpoint)" if committed else ""))
    for user_id, session_id, _, sstate in targets:
        write_session_state(db_path, user_id, session_id, _transform(sstate))
        print(f"[OK] Updated ADK session {session_id}")
    if args.token_budget is not None or args.usd_budget is not None:
        print("[OK] Updated .env")

    if args.usd_budget is not None:
        master_key = lib_env.read_env_var(env_path, "LITELLM_MASTER_KEY")
        if lib_env.is_placeholder(master_key) or not master_key:
            print("[WARN] LITELLM_MASTER_KEY not set - raise 'scrum-sprint-budget' in the LiteLLM UI yourself.")
        else:
            ok, msg = update_litellm_budget(litellm_host_url(env_path), master_key, args.usd_budget)
            if ok:
                print(f"[OK] LiteLLM {LITELLM_BUDGET_ID} max_budget set to ${args.usd_budget:g}")
            else:
                print(f"[WARN] Could not update LiteLLM's {LITELLM_BUDGET_ID} ({msg}). It is re-synced from "
                      "TOTAL_USD_BUDGET the next time a role's key is created; or raise it in the "
                      f"LiteLLM UI ({litellm_host_url(env_path)}/ui).")

    print("\nNext: restart the agent so it picks this up:  python3 run.py")
    if len(sessions) > len(targets):
        print(f"Note: {len(sessions) - len(targets)} older session(s) were left untouched - resume the "
              "edited one, or start a new session (it loads the repaired .hc/state.json).")
    return 0


def main() -> None:
    sys.exit(run(Path(__file__).resolve().parent, sys.argv[1:]))


if __name__ == "__main__":
    main()
