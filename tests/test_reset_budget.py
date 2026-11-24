"""Tests for reset_budget.py (SPEC-ISSUE-0096)."""
import ast
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import reset_budget

REPO_ROOT = Path(__file__).resolve().parent.parent


def _agent_reset_delta() -> dict:
    """The dict literal sprint_budget_reset_state_delta() returns, read via
    ast rather than imported - the agent package needs the container's
    dependencies, which the host suite doesn't have."""
    source = (REPO_ROOT / "agents" / "scrum_team" / "tools" / "budget.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "sprint_budget_reset_state_delta":
            for stmt in node.body:
                if isinstance(stmt, ast.Return):
                    return ast.literal_eval(stmt.value)
    raise AssertionError("sprint_budget_reset_state_delta not found")


def test_budget_reset_delta_matches_start_sprint_reset():
    """SPEC-ISSUE-0049's failure mode: two hand-copied reset lists drifting
    apart. Every key this script clears must be cleared to the exact same
    value start_sprint's own reset uses."""
    agent_delta = _agent_reset_delta()
    for key, value in reset_budget.BUDGET_RESET_DELTA.items():
        assert key in agent_delta, f"{key} is not part of sprint_budget_reset_state_delta()"
        assert agent_delta[key] == value


def _stuck_state():
    return {
        "sprint_number": 3,
        "budgets": {"total": 1000, "total_usd": 10.0},
        "token_usage": {"total": 1500, "agents": {"DevTeam": 1200, "ScrumMaster": 300}},
        "budget_exhaustion_synced": True,
        "critical_halt_notified": True,
        "_budget_warning_pct_fired": 90,
        "sprint_report": "keep me",
        "product_backlog": [{"id": "US-0001"}],
        "blocking_interactions": [
            {"id": 1, "kind": "critical_error", "summary": "🚫 [TOKEN BUDGET EXCEEDED] Sprint token limit (1,000) reached.", "resolved": False},
            {"id": 2, "kind": "critical_error", "summary": "🚫 [USD BUDGET EXCEEDED] Total USD budget ($10.00) reached.", "resolved": False},
            {"id": 3, "kind": "approval", "summary": "Release needs approval", "resolved": False},
        ],
    }


def test_apply_budget_reset_clears_counters_and_keeps_everything_else():
    original = _stuck_state()
    new = reset_budget.apply_budget_reset(original, (reset_budget.TOKEN_HALT_MARKER,))
    assert new["token_usage"] == {"total": 0, "agents": {}}
    assert new["budget_exhaustion_synced"] is False
    assert new["critical_halt_notified"] is False
    assert new["_budget_warning_pct_fired"] == 0
    # unrelated state untouched
    assert new["sprint_report"] == "keep me"
    assert new["product_backlog"] == [{"id": "US-0001"}]
    assert new["budgets"] == {"total": 1000, "total_usd": 10.0}
    # only the token halt is resolved; USD halt and approval stay open
    resolved = {e["id"]: e["resolved"] for e in new["blocking_interactions"]}
    assert resolved == {1: True, 2: False, 3: False}
    # input not mutated
    assert original["token_usage"]["total"] == 1500


def test_set_budget_limits():
    new = reset_budget.set_budget_limits(_stuck_state(), token_budget=8_000_000, usd_budget=25)
    assert new["budgets"] == {"total": 8_000_000, "total_usd": 25.0}


def test_effective_token_limit_prefers_env():
    state = {"budgets": {"total": 1000}}
    assert reset_budget.effective_token_limit("6000000", state) == 6_000_000
    assert reset_budget.effective_token_limit("", state) == 1000
    assert reset_budget.effective_token_limit("", {}) == reset_budget.DEFAULT_TOKEN_BUDGET


# --- end-to-end against a fixture install ---------------------------------

@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake HC checkout: .env, a git state repo with a stuck state.json,
    and an ADK sqlite store holding two sessions (newest also stuck)."""
    hc = tmp_path / "hc"
    state_repo = tmp_path / "product"
    (state_repo / ".hc").mkdir(parents=True)
    (hc / "sessions").mkdir(parents=True)
    (hc / ".env").write_text(f"STATE_REPO_PATH='{state_repo}'\nSPRINT_TOKEN_BUDGET='1000'\n")

    state_path = state_repo / ".hc" / "state.json"
    state_path.write_text(json.dumps(_stuck_state(), indent=2))
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=state_repo, check=True)
    subprocess.run([*git, "add", "."], cwd=state_repo, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=state_repo, check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")

    db = hc / "sessions" / "adk_sessions.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT, create_time REAL, update_time REAL)")
    old = dict(_stuck_state(), token_usage={"total": 42, "agents": {}})
    conn.execute("INSERT INTO sessions VALUES ('scrum_team','user','old',?,1,1)", (json.dumps(old),))
    conn.execute("INSERT INTO sessions VALUES ('scrum_team','user','new',?,2,2)", (json.dumps(_stuck_state()),))
    conn.commit()
    conn.close()

    monkeypatch.setattr(reset_budget, "agent_running", lambda repo_root: False)
    return hc, state_repo, state_path, db


def _session_states(db):
    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, state FROM sessions").fetchall())
    conn.close()
    return {k: json.loads(v) for k, v in rows.items()}


def test_status_is_read_only(install, capsys):
    hc, _, state_path, db = install
    before = (state_path.read_text(), _session_states(db), (hc / ".env").read_text())
    assert reset_budget.run(hc, []) == 0
    assert before == (state_path.read_text(), _session_states(db), (hc / ".env").read_text())
    out = capsys.readouterr().out
    assert "EXHAUSTED" in out
    assert "Read-only" in out


def test_reset_usage_edits_state_json_and_newest_session(install):
    hc, state_repo, state_path, db = install
    assert reset_budget.run(hc, ["--reset-usage", "--yes"]) == 0

    repo_state = json.loads(state_path.read_text())
    assert repo_state["token_usage"] == {"total": 0, "agents": {}}
    assert repo_state["critical_halt_notified"] is False

    sessions = _session_states(db)
    assert sessions["new"]["token_usage"] == {"total": 0, "agents": {}}
    assert sessions["old"]["token_usage"]["total"] == 42  # untouched without --all-sessions

    log = subprocess.run(["git", "log", "--format=%s"], cwd=state_repo, capture_output=True, text=True).stdout
    assert "reset_budget.py" in log.splitlines()[0]
    # .env untouched by a pure usage reset
    assert "SPRINT_TOKEN_BUDGET='1000'" in (hc / ".env").read_text()


def test_all_sessions_and_token_budget(install):
    hc, _, state_path, db = install
    assert reset_budget.run(hc, ["--token-budget", "8000000", "--all-sessions", "--yes"]) == 0
    assert "SPRINT_TOKEN_BUDGET='8000000'" in (hc / ".env").read_text()
    for state in _session_states(db).values():
        assert state["budgets"]["total"] == 8_000_000
        # raising the limit alone doesn't wipe usage...
        assert state["token_usage"]["total"] in (42, 1500)
        # ...but does resolve the stale token-halt interaction
        assert state["blocking_interactions"][0]["resolved"] is True
    assert json.loads(state_path.read_text())["budgets"]["total"] == 8_000_000


def test_usd_budget_updates_litellm(install, monkeypatch):
    hc, _, _, db = install
    with open(hc / ".env", "a") as f:
        f.write("LITELLM_MASTER_KEY='sk-real-looking-test-value'\n")
    calls = []
    monkeypatch.setattr(reset_budget, "update_litellm_budget", lambda url, key, usd: calls.append((url, usd)) or (True, "HTTP 200"))
    assert reset_budget.run(hc, ["--usd-budget", "25", "--yes"]) == 0
    assert calls == [("http://localhost:4000", 25.0)]
    assert "TOTAL_USD_BUDGET='25'" in (hc / ".env").read_text()
    new = _session_states(db)["new"]
    assert new["budgets"]["total_usd"] == 25.0
    assert {e["id"]: e["resolved"] for e in new["blocking_interactions"]} == {1: False, 2: True, 3: False}


def test_refuses_while_agent_running(install, monkeypatch):
    hc, _, state_path, _ = install
    monkeypatch.setattr(reset_budget, "agent_running", lambda repo_root: True)
    before = state_path.read_text()
    assert reset_budget.run(hc, ["--reset-usage", "--yes"]) == 1
    assert state_path.read_text() == before


def test_rejects_non_positive_budget(install):
    hc, _, _, _ = install
    assert reset_budget.run(hc, ["--token-budget", "0", "--yes"]) == 1


def test_unknown_session_id(install):
    hc, _, _, _ = install
    assert reset_budget.run(hc, ["--reset-usage", "--session-id", "nope", "--yes"]) == 1


def test_declined_confirmation_changes_nothing(install, monkeypatch):
    hc, _, state_path, _ = install
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    before = state_path.read_text()
    assert reset_budget.run(hc, ["--reset-usage"]) == 1
    assert state_path.read_text() == before


# --- interplay with `run.py override-budget` (SPEC-ISSUE-0092) -------------

def test_token_budget_unpins_only_the_token_override():
    state = dict(_stuck_state(), budget_human_override={"total": 2000, "total_usd": 50.0})
    new = reset_budget.set_budget_limits(state, token_budget=8_000_000)
    # .env's new value would otherwise be ignored by sync_budgets_from_env
    assert new["budget_human_override"] == {"total_usd": 50.0}
    new = reset_budget.set_budget_limits(new, usd_budget=60)
    assert new["budget_human_override"] is None


def test_clear_human_override():
    state = dict(_stuck_state(), budget_human_override={"total": 2000})
    assert reset_budget.clear_human_override(state)["budget_human_override"] is None
    # no override key at all -> don't invent one
    assert "budget_human_override" not in reset_budget.clear_human_override(_stuck_state())


def test_effective_token_limit_respects_override_pin():
    state = {"budgets": {"total": 1000}, "budget_human_override": {"total": 2000}}
    assert reset_budget.effective_token_limit("6000000", state) == 2000


def test_clear_override_end_to_end(install, capsys):
    hc, _, _, db = install
    conn = sqlite3.connect(db)
    pinned = dict(_stuck_state(), budget_human_override={"total": 2000})
    conn.execute("UPDATE sessions SET state = ? WHERE id = 'new'", (json.dumps(pinned),))
    conn.commit()
    conn.close()

    assert reset_budget.run(hc, []) == 0
    assert "live override pin" in capsys.readouterr().out

    assert reset_budget.run(hc, ["--clear-override", "--yes"]) == 0
    new = _session_states(db)["new"]
    assert new["budget_human_override"] is None
    assert new["token_usage"]["total"] == 1500  # usage untouched without --reset-usage
