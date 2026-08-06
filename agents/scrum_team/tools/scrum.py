# agents/scrum_team/tools/scrum.py
from __future__ import annotations
import os
import json
from typing import Any, Dict, List
from pathlib import Path
from .base import _configured_repo_root, _state_file_path, _project_root, _hc_version, _run
from .migrations import migrate_state
from ..helpers import blocks_direct_status_set, is_low_quality_retro_text, new_sprint_item_blocked, get_env_with_deprecated_fallback

DEFAULT_DOD = [
    "Code reviewed",
    "Automated tests passing",
    "Acceptance criteria met",
    "No critical security issues",
    "Docs updated if needed",
    "specs/ROADMAP.md updated via update_roadmap to reflect this story's completed status",
    "Actual tokens spent logged via log_story_tokens",
]

REPO_STATE_KEYS = [
    "version",
    "product_vision",
    "product_goals",
    "architecture_vision",
    "product_backlog",
    "definition_of_done",
    "sprint_goal",
    "sprint_number",
    "sprint_backlog_pr_sprint",
    "sprint_backlog",
    "impediment_log",
    "retro_actions",
    "decision_log",
    "sprint_report",
    "budgets",
    "token_usage",
    "story_estimates",
    "sprint_report_kpis",
    "repo",
    "messages",
    "hc_version",
    "retro_baseline",
    "human_approvals",
    "sprint_approval_baseline",
    "release_approval_baseline",
    "dev_touch_baseline",
    "sprint_files_touched",
    "last_check_build",
    "pr_review_calls",
    "architect_review_baseline",
    "qa_review_baseline",
    "sprint_report_pending_release",
    "blocking_interactions",
    "budget_reset_since_last_sprint_start",
]
# Deliberately excluded from the above: github_token, github_app,
# litellm_keys, last_auto_auth_error - these are real secrets/session-only
# auth material and must never be written into the target repo's
# .hc/state.json. See SECURITY.md.
#
# Also deliberately excluded: transcript (GH issue #127) - the raw,
# unbounded multi-agent debug transcript has no place in a git-committed,
# human-reviewable state file; it stays in-memory session state for the
# sprint report excerpt and the markdown transcript renderer
# (write_conversation_transcript in tools/budget.py), and is separately
# made durable via the per-run log at /app/sessions/transcript-<session-id>.
# log (see transcript_logger in agent.py). `messages` (the flat
# ScrumOrchestrator-only history used to resume a session on a fresh
# checkout of the state repo) stays, since it serves a distinct functional
# purpose, not just a debug record.

def init_scrum_state(tool_context=None) -> Dict[str, Any]:
    """
    Initialize all Scrum artifacts in session.state if missing.
    """
    from .github import configure_github_app
    from .requirements import sync_stories_from_markdown
    from .base import _normalize_private_key
    
    s = tool_context.state
    
    from ..state import ScrumState
    default_version = ScrumState().version

    # Initialize defaults
    s.setdefault("version", default_version)
    s.setdefault("product_vision", "")
    s.setdefault("product_goals", [])
    s.setdefault("architecture_vision", "")
    s.setdefault("product_backlog", [])
    s.setdefault("definition_of_done", list(DEFAULT_DOD))
    s.setdefault("sprint_goal", "")
    s.setdefault("sprint_number", 0)
    s.setdefault("sprint_backlog_pr_sprint", 0)
    s.setdefault("sprint_backlog", [])
    s.setdefault("impediment_log", [])
    s.setdefault("retro_actions", [])
    s.setdefault("decision_log", [])
    s.setdefault("sprint_report", "")
    s.setdefault("budgets", {"total": 0, "total_usd": 0.0, "agents": {}})
    s.setdefault("token_usage", {"total": 0, "agents": {}})
    s.setdefault("story_estimates", {})
    s.setdefault("transcript", [])
    s.setdefault("retro_baseline", 0)
    s.setdefault("human_approvals", [])
    s.setdefault("sprint_approval_baseline", 0)
    s.setdefault("release_approval_baseline", 0)
    s.setdefault("dev_touch_baseline", 0)
    # GH issue #119: previously only dev_touch_baseline was persisted
    # (REPO_STATE_KEYS), not the running sprint_files_touched list it's
    # compared against - after any state reload mid-sprint (a restart, a
    # corrupted-state recovery), the baseline survived but the list reset
    # to empty, requiring new writes to exceed a stale baseline again before
    # the Implemented-stage "real source file written" gate would pass,
    # even for a story whose files were already written earlier in the
    # sprint.
    s.setdefault("sprint_files_touched", [])
    s.setdefault("last_check_build", None)
    s.setdefault("pr_review_calls", {})
    s.setdefault("architect_review_baseline", 0)
    s.setdefault("qa_review_baseline", 0)
    s.setdefault("sprint_report_pending_release", False)
    s.setdefault("blocking_interactions", [])
    s.setdefault("orchestrator_stall_count", 0)
    # True by default: the very first sprint needs no reset_sprint_budget()
    # call (see GH issue #110) - there's no previous sprint's token usage to
    # clear yet.
    s.setdefault("budget_reset_since_last_sprint_start", True)

    # 1. Try to load from repo if present first, so environment can override
    state_json_corrupted = False
    try:
        repo_root = _configured_repo_root(tool_context)
        fp = _state_file_path(repo_root)
        if fp.exists():
            load_result = load_state_from_repo(tool_context)
            # GH issue #85: a corrupted-and-unrecoverable-even-from-git
            # state.json used to be discarded silently here (this whole
            # block only ever caught real exceptions, and load_state_from_repo
            # returns an error dict rather than raising) - the session would
            # just quietly start blank, with no indication anything was ever
            # wrong or any chance to intervene. "not found" isn't corruption
            # (a brand new state repo has no state.json at all - normal, not
            # an error worth surfacing), so only flag the genuine case.
            if load_result.get("status") == "error" and "not found" not in load_result.get("message", ""):
                state_json_corrupted = True
    except Exception:
        pass

    # hc_version always reflects the version actually running this session,
    # never the (possibly older) value just loaded from the state repo -
    # see RELEASE.md "Tracking the HC version in the state repo".
    s["hc_version"] = _hc_version()

    # 2. Load/Override from environment variables
    # (Environment variables take precedence over persisted state for configuration)
    
    # Budgets
    budgets = s.get("budgets", {}) or {}
    env_token_budget = os.environ.get("SPRINT_TOKEN_BUDGET")
    if env_token_budget:
        try:
            budgets["total"] = int(env_token_budget)
        except (ValueError, TypeError):
            pass
    
    env_usd_budget = get_env_with_deprecated_fallback("TOTAL_USD_BUDGET", "SPRINT_USD_BUDGET")
    if env_usd_budget:
        try:
            budgets["total_usd"] = float(env_usd_budget)
        except (ValueError, TypeError):
            pass
    
    # HARD GUARDRAIL: Never allow 0 budget if not explicitly intended (and even then, discourage it)
    # Default to sensible values if still 0
    if budgets.get("total", 0) <= 0:
        budgets["total"] = 1000000  # Default 1M tokens
    if budgets.get("total_usd", 0.0) <= 0.0:
        budgets["total_usd"] = 10.0 # Default $10.00
        
    s["budgets"] = budgets

    # Repo
    env_repo_url = os.environ.get("GITHUB_REPO_URL")
    if env_repo_url:
        repo_cfg = s.get("repo", {}) or {}
        repo_cfg["url"] = env_repo_url
        repo_cfg["local_path"] = os.environ.get("STATE_REPO_PATH") or repo_cfg.get("local_path") or ""
        repo_cfg["default_branch"] = os.environ.get("GITHUB_REPO_BRANCH") or repo_cfg.get("default_branch") or "main"
        s["repo"] = repo_cfg

    # 2.1 Load GitHub App credentials
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    inst_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    
    if app_id and private_key and inst_id:
        app_cfg = s.get("github_app", {})
        clean_key = _normalize_private_key(private_key)
        # We check against strings to avoid type issues
        if (app_cfg.get("app_id") != str(app_id) or 
            app_cfg.get("installation_id") != str(inst_id) or
            not s.get("github_token")):
            
            res = configure_github_app(app_id, clean_key, inst_id, tool_context=tool_context)
            if res.get("status") == "error":
                s["last_auto_auth_error"] = res.get("message")
    
    # 2.2 Fallback to GITHUB_TOKEN if provided and no app token set
    env_github_token = os.environ.get("GITHUB_TOKEN")
    if env_github_token and not s.get("github_token"):
        s["github_token"] = env_github_token

    # 3. Load stories and requirements from Markdown
    try:
        from .requirements import (
            sync_stories_from_markdown,
            sync_requirements_from_markdown,
            sync_architecture_vision_from_markdown,
        )
        _ = sync_stories_from_markdown(tool_context)
        _ = sync_requirements_from_markdown(tool_context)
        _ = sync_architecture_vision_from_markdown(tool_context)
    except Exception:
        pass

    if state_json_corrupted:
        s["state_json_corrupted"] = True
        try:
            from .notifications import record_blocking_interaction
            record_blocking_interaction(
                "state_corrupted",
                "state.json exists but could not be loaded, even after searching git history for an "
                "earlier valid checkpoint - starting this session with blank/default state instead.",
                detail=(
                    "Recovery options: get_corrupted_state_raw_content() to see the raw file yourself "
                    "(or have the Orchestrator attempt an LLM-assisted repair from it, then "
                    "save_repaired_state()); reset_state_from_git() to search all of git history (not "
                    "just the latest commit) for a usable earlier checkpoint; or "
                    "clear_corrupted_state() to explicitly discard it (this session already started "
                    "blank - that tool just makes it official rather than implicit)."
                ),
                tool_context=tool_context,
            )
        except Exception:
            pass

    return {"status": "ok", "initialized": True, "state_json_corrupted": state_json_corrupted}

def _write_state_atomically(state_path: Path, snapshot: dict) -> None:
    """Write-to-temp-file + os.replace (atomic on POSIX and Windows) rather
    than a plain write_text - a process killed mid-write can otherwise
    leave a torn, half-written state.json behind with no warning (GH issue
    #59: "ensure the state is always restorable even if the container gets
    killed"). os.replace either completes fully or not at all, so the
    previous checkpoint's bytes on disk are never partially overwritten."""
    tmp_path = state_path.with_name(state_path.name + ".tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, state_path)

def _checkpoint_state_commit(repo_root: Path, tool_context=None) -> None:
    """Best-effort local git commit of .hc/state.json, so every
    save_state_to_repo() call leaves behind a restorable checkpoint in git
    history (GH issue #59) - not just whatever bytes are currently sitting
    in the working tree. Silently does nothing if repo_root isn't a git
    repo at all (e.g. tests, or a STATE_REPO_PATH that's a plain
    directory); _run() itself already swallows a "nothing to commit"
    failure (the overwhelmingly common case - most saves don't actually
    change anything) without raising, so there's nothing further to catch
    here. Never pushes - see git_push() for the deliberate,
    protected-branch-aware remote-push path; this is a purely local safety
    net."""
    if not (repo_root / ".git").exists():
        return
    _run(["git", "add", ".hc/state.json"], cwd=str(repo_root), tool_context=tool_context)
    _run(["git", "commit", "-m", "checkpoint: save_state_to_repo"], cwd=str(repo_root), tool_context=tool_context)

def _parse_state_json(text: str):
    """dict if text is valid JSON representing an object, else None -
    shared by load_state_from_repo's primary read and its git-recovery
    fallback below, so both apply the exact same "is this actually usable
    state" test."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None

_RECOVERY_HISTORY_DEPTH = 50


def _recover_state_json_from_git(repo_root: Path, max_commits: int = _RECOVERY_HISTORY_DEPTH):
    """The most recent commit touching `.hc/state.json` whose own snapshot
    still parses as valid JSON, as a dict - or None if repo_root isn't a git
    repo, there's no commit touching that path yet, or none of the last
    `max_commits` do either. This is the fallback path GH issue #59 asks
    for: "these checkpoints in git are the fallback option if the state
    gets corrupted".

    Walks history (newest first) rather than only checking HEAD (GH issue
    #85: "offer options to repair or delete corrupted state") - if the
    *latest* checkpoint commit's own snapshot is itself corrupted (e.g. a
    torn write got committed before anyone noticed), an earlier one may
    still be perfectly recoverable, and HEAD-only recovery would otherwise
    give up immediately in exactly that case.
    """
    if not (repo_root / ".git").exists():
        return None
    log_result = _run(["git", "log", "--format=%H", "--", ".hc/state.json"], cwd=str(repo_root))
    if log_result.get("status") != "ok":
        return None
    shas = [line for line in log_result.get("stdout", "").splitlines() if line.strip()][:max_commits]
    for sha in shas:
        show_result = _run(["git", "show", f"{sha}:.hc/state.json"], cwd=str(repo_root))
        if show_result.get("status") != "ok":
            continue
        data = _parse_state_json(show_result.get("stdout", ""))
        if data is not None:
            return data
    return None

def save_state_to_repo(tool_context=None) -> Dict[str, Any]:
    """
    Persist selected scrum state keys into the configured repo, then
    commit that snapshot to the repo's local git history as a checkpoint
    (GH issue #59) - see _checkpoint_state_commit for why this is the
    fallback-if-corrupted mechanism load_state_from_repo below relies on.
    """
    repo_root = _configured_repo_root(tool_context)
    try:
        repo_root.mkdir(parents=True, exist_ok=True)
        state_dir = repo_root / ".hc"
        state_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {k: tool_context.state.get(k) for k in REPO_STATE_KEYS}
        state_path = _state_file_path(repo_root)
        _write_state_atomically(state_path, snapshot)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    _checkpoint_state_commit(repo_root, tool_context)
    return {"status": "ok", "path": str(state_path)}

def load_state_from_repo(tool_context=None) -> Dict[str, Any]:
    """
    Load previously persisted scrum state. Falls back to the last
    git-committed checkpoint if state.json exists but is corrupted/invalid
    (GH issue #59), repairing the working-tree file with the recovered
    content in the process so the next load doesn't hit the same
    corruption again.
    """
    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if not fp.exists():
        return {"status": "error", "message": f"State file not found: {fp}"}
    try:
        data = _parse_state_json(fp.read_text(encoding="utf-8", errors="replace"))
        recovered_from_git = False
        if data is None:
            data = _recover_state_json_from_git(repo_root)
            recovered_from_git = data is not None
        if data is None:
            return {"status": "error", "message": "Invalid state format in state.json, and no recoverable git checkpoint was found"}

        data = migrate_state(data, data.get("hc_version", "unknown"))
        for k, v in data.items():
            tool_context.state[k] = v

        result = {"status": "ok", "loaded_keys": list(data.keys())}
        if recovered_from_git:
            result["recovered_from_git"] = True
            try:
                _write_state_atomically(fp, data)
            except Exception:
                pass
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _current_state_json_is_corrupted(fp: Path) -> bool:
    """True if state.json exists and does NOT currently parse as a valid
    JSON object - the safety check every remediation tool below uses before
    touching anything, so none of them can be pointed at perfectly good
    state by mistake (GH issue #85)."""
    if not fp.exists():
        return False
    return _parse_state_json(fp.read_text(encoding="utf-8", errors="replace")) is None


def get_corrupted_state_raw_content(tool_context=None) -> Dict[str, Any]:
    """
    Returns the raw text of the current, corrupted state.json - the "repair
    it with help of the LLM" option from GH issue #85. Only ever returns
    content if state.json is actually currently invalid; refuses otherwise,
    so this can't be used to go rewrite perfectly good state from scratch.
    Once you've worked out a corrected version, call
    save_repaired_state(repaired_state) to persist it.
    """
    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if not fp.exists():
        return {"status": "error", "message": f"No state.json exists yet at {fp} - nothing to repair."}
    if not _current_state_json_is_corrupted(fp):
        return {"status": "error", "message": "state.json currently parses fine - nothing to repair."}
    return {"status": "ok", "path": str(fp), "raw_content": fp.read_text(encoding="utf-8", errors="replace")}


def save_repaired_state(repaired_state: dict, tool_context=None) -> Dict[str, Any]:
    """
    Persists a hand-repaired (or LLM-reconstructed) replacement for a
    currently-corrupted state.json - the write side of GH issue #85's
    "repair it with help of the LLM" option, paired with
    get_corrupted_state_raw_content() above. Validates against ScrumState
    first (refusing anything that doesn't parse as a real scrum state, so a
    bad repair attempt can't make things worse), then writes it atomically,
    checkpoints it to git, and loads it into the live session so the repair
    takes effect immediately rather than only on the next restart. Refuses
    if state.json isn't actually currently corrupted - this repairs a
    genuine problem, it doesn't overwrite good state.
    """
    from ..state import ScrumState
    from pydantic import ValidationError

    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if fp.exists() and not _current_state_json_is_corrupted(fp):
        return {"status": "error", "message": "state.json currently parses fine - refusing to overwrite it via save_repaired_state()."}
    if not isinstance(repaired_state, dict):
        return {"status": "error", "message": "repaired_state must be a JSON object (dict)."}
    try:
        validated = ScrumState(**repaired_state).model_dump()
    except ValidationError as e:
        return {"status": "error", "message": f"repaired_state does not validate as a ScrumState: {e}"}

    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        _write_state_atomically(fp, validated)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    _checkpoint_state_commit(repo_root, tool_context)
    for k, v in validated.items():
        tool_context.state[k] = v
    return {"status": "ok", "path": str(fp), "loaded_keys": list(validated.keys())}


def reset_state_from_git(tool_context=None) -> Dict[str, Any]:
    """
    Explicit, on-demand version of load_state_from_repo()'s automatic
    git-recovery fallback (GH issue #85's "reset to the state persisted in
    git" option) - searches all of git history for `.hc/state.json` (not
    just the automatic path's implicit attempt), restores the newest
    checkpoint that still parses as valid, writes it back to the working
    tree, and loads it into the live session. Refuses if state.json isn't
    actually currently corrupted, since this discards whatever's in the
    working tree now - not something to do to state that's already fine.
    """
    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if fp.exists() and not _current_state_json_is_corrupted(fp):
        return {"status": "error", "message": "state.json currently parses fine - nothing to reset."}

    data = _recover_state_json_from_git(repo_root)
    if data is None:
        return {"status": "error", "message": "No usable checkpoint found anywhere in git history for .hc/state.json."}

    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        _write_state_atomically(fp, data)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    _checkpoint_state_commit(repo_root, tool_context)
    for k, v in data.items():
        tool_context.state[k] = v
    tool_context.state["state_json_corrupted"] = False
    return {"status": "ok", "path": str(fp), "loaded_keys": list(data.keys())}


def clear_corrupted_state(tool_context=None) -> Dict[str, Any]:
    """
    Deletes a currently-corrupted state.json (GH issue #85's "clear it
    completely" option) so the next init_scrum_state() call starts
    genuinely fresh, rather than the corruption silently lingering on disk.
    Refuses if state.json isn't actually currently corrupted - this is a
    deliberate discard of a real problem, not a way to reset perfectly good
    state.
    """
    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if not fp.exists():
        return {"status": "error", "message": f"No state.json exists at {fp} - nothing to clear."}
    if not _current_state_json_is_corrupted(fp):
        return {"status": "error", "message": "state.json currently parses fine - refusing to delete it via clear_corrupted_state()."}

    try:
        fp.unlink()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    tool_context.state["state_json_corrupted"] = False
    return {"status": "ok", "path": str(fp), "message": "Corrupted state.json cleared - the team will start fresh."}

def log_decision(title: str, decision: str, rationale: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Append a decision to decision_log.
    """
    s = tool_context.state
    entry = {
        "title": title.strip(),
        "decision": decision.strip(),
        "rationale": rationale.strip(),
        "owner": owner.strip(),
    }
    s["decision_log"] = list(s.get("decision_log", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "decision": entry}

def add_impediment(description: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an impediment to impediment_log.
    """
    if is_low_quality_retro_text(description):
        return {
            "status": "error",
            "message": (
                "description is blank, a generic placeholder, or too short to be a concrete "
                "impediment (see SM_PROMPT's RETROSPECTIVE REASONING) - describe what actually "
                "blocked the process this sprint."
            ),
        }
    s = tool_context.state
    imp = {"description": description.strip(), "owner": owner.strip(), "status": "open"}
    s["impediment_log"] = list(s.get("impediment_log", [])) + [imp]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "impediment": imp}

def add_retro_action(action: str, owner: str, success_metric: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an action item from retrospectives.
    """
    if is_low_quality_retro_text(action) or is_low_quality_retro_text(success_metric):
        return {
            "status": "error",
            "message": (
                "action/success_metric is blank, a generic placeholder ('communicate better' and "
                "similar), or too short - SM_PROMPT's RETROSPECTIVE REASONING requires an action "
                "tied to what actually happened this sprint, with a real success metric, not a "
                "formality to unblock create_sprint_report."
            ),
        }
    s = tool_context.state
    entry = {
        "action": action.strip(),
        "owner": owner.strip(),
        "success_metric": success_metric.strip(),
        "status": "open",
    }
    s["retro_actions"] = list(s.get("retro_actions", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "retro_action": entry}

_APPROVAL_TYPES = ("sprint", "release", "budget")


def record_human_approval(approval_type: str, note: str = "", tool_context=None) -> Dict[str, Any]:
    """
    Records an explicit human approval event (see ORCHESTRATOR_PROMPT's
    ITERATION MODE: "A sprint can ONLY start after explicit human review and
    approval of the sprint goal and sprint backlog", and PO/SM_PROMPT's
    "Ensure Human Review is done for each increment") - the mechanical
    counterpart `advance_story_stage(..., "Implemented")` and
    `create_release_pr` (see ISSUE-0001) actually check for, instead of
    trusting the model's own assertion that a human reviewed something.
    - approval_type: "sprint" (this sprint's goal + backlog), "release"
      (this increment, before create_release_pr), or "budget" (this
      sprint's token/USD budget - required instead of "sprint" at the CEO
      interaction level). Which of these is actually required by the two
      gates above depends on INTERACTION_LEVEL - see
      docs/INTERACTION-LEVELS.md and
      agents/scrum_team/helpers.py's required_pre_implementation_approval/
      required_pre_release_approval. A rejected gate's own error message
      names exactly which type to call this with.
    """
    if approval_type not in _APPROVAL_TYPES:
        return {"status": "error", "message": f"Unknown approval_type '{approval_type}'. Must be one of {_APPROVAL_TYPES}."}
    s = tool_context.state
    entry = {"type": approval_type, "note": note.strip()}
    s["human_approvals"] = list(s.get("human_approvals", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "approval": entry}

def start_sprint(goal: str, tool_context=None) -> Dict[str, Any]:
    """
    Sets `sprint_goal` to kick off a new sprint - the mechanical counterpart
    of ORCHESTRATOR_PROMPT's ITERATION MODE ("the team works in iterations"):
    before this tool existed, nothing in the codebase ever set `sprint_goal`
    to a real value (`init_scrum_state` only ever defaults it to ""), so a
    user saying "let's start the sprint" produced narrated text but no
    persisted sprint at all - see ISSUE-0011.
    - Rejects a blank/generic/too-short goal (mirrors `is_low_quality_retro_text`'s
      guard on retro text) - a real sprint goal states what this sprint is
      actually meant to achieve, not a placeholder.
    - Refuses to start while the previous sprint's close sequence is left
      unfinished (`new_sprint_item_blocked` - see ISSUE-0010), for the same
      reason `plan_sprint_backlog_item` refuses new backlog items in that
      state: starting a new sprint goal is exactly the kind of "new sprint
      work" that gate exists to catch, and doing it while the previous
      sprint's release is still hanging open leaves that stuck permanently.
    - Refuses to start a SECOND (or later) sprint unless reset_sprint_budget
      has been called since the previous one started (see GH issue #110) -
      previously this was "MANDATORY" only in SM_PROMPT's text, with no code
      backing it at all, so a forgotten call silently carried over whatever
      token budget headroom the previous sprint left (or didn't leave),
      shrinking or eliminating the new sprint's real budget with no error
      until an unexplained early halt partway through. The very first sprint
      needs no reset (there's no previous sprint's leftover usage to clear),
      detected by sprint_goal still being unset.
    """
    if is_low_quality_retro_text(goal):
        return {
            "status": "error",
            "message": (
                "goal is blank, a generic placeholder, or too short to be a real sprint goal - "
                "state what this sprint is actually meant to achieve."
            ),
        }
    s = tool_context.state
    block_msg = new_sprint_item_blocked(s)
    if block_msg:
        return {"status": "error", "message": block_msg}
    if s.get("sprint_goal") and not s.get("budget_reset_since_last_sprint_start", True):
        return {
            "status": "error",
            "message": (
                "Cannot start a new sprint - reset_sprint_budget() must be called first. "
                "SPRINT_TOKEN_BUDGET is a per-sprint allowance, not cumulative; without a fresh "
                "reset, this sprint would silently inherit whatever token budget headroom the "
                "previous sprint left over."
            ),
        }
    s["sprint_goal"] = goal.strip()
    # Consumed by this start - a fresh reset_sprint_budget() call is required
    # again before the *next* sprint can start (same "must be NEW since last
    # time" pattern as create_sprint_report's retro_baseline).
    s["budget_reset_since_last_sprint_start"] = False
    # Used by create_sprint_backlog_pr (tools/github.py) to name that
    # sprint's "Sprint Backlog #<N>" PR - the only place a sprint number is
    # tracked in agent state at all (run_eval.py's own sprint counter is a
    # harness-only concept, invisible to the agents' own state/tools).
    s["sprint_number"] = s.get("sprint_number", 0) + 1
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "sprint_goal": s["sprint_goal"], "sprint_number": s["sprint_number"]}


def plan_sprint_backlog_item(title_or_id: str, plan: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add/update an item in sprint_backlog with implementation plan fields.
    """
    from .requirements import _update_story_markdown

    if blocks_direct_status_set(plan.get("status")):
        return {
            "status": "error",
            "message": (
                f"Cannot set status to '{plan.get('status')}' directly - stage transitions (and "
                "legacy 'Done'/'completed'/'closed', which are treated as every stage complete) "
                "must go through advance_story_stage(title_or_id, stage), which enforces ordering "
                "and stage ownership. Omit 'status' here and call advance_story_stage instead."
            ),
        }

    s = tool_context.state
    sprint: List[Dict[str, Any]] = list(s.get("sprint_backlog", []))

    estimates = s.get("story_estimates", {})
    if "estimate" in plan:
        # {estimate, actual} shape - actual is filled in later via
        # log_story_tokens (agents/scrum_team/tools/budget.py). A bare
        # number here is the old pre-actual-tracking shape, where the
        # stored value was always the estimate - keep it as one rather
        # than misreading it as an actual.
        entry = estimates.get(title_or_id)
        entry = entry if isinstance(entry, dict) else {}
        entry["estimate"] = plan["estimate"]
        estimates[title_or_id] = entry
        s["story_estimates"] = estimates

    updated_item = None
    key = title_or_id
    for i, x in enumerate(sprint):
        if x.get("id") == key or x.get("title") == key:
            sprint[i] = {**x, **plan}
            s["sprint_backlog"] = sprint
            updated_item = sprint[i]
            break

    if not updated_item:
        # ISSUE-0010: this is a genuinely new sprint_backlog item (not an
        # update to one already there) - refuse it if the previous sprint's
        # close sequence (retro/report done, but release PR never
        # completed) is still hanging open.
        block_msg = new_sprint_item_blocked(s)
        if block_msg:
            return {"status": "error", "message": block_msg}

        # Inherit title/user_story/acceptance_criteria/type from the matching
        # product_backlog entry (PO-owned, via upsert_story) rather than
        # defaulting title to the bare lookup key - that default is exactly
        # what produced story files literally titled "US-0008" in real eval
        # runs (plan_sprint_backlog_item's own plan dict never carries those
        # PO-owned fields at all).
        product_match = next(
            (x for x in s.get("product_backlog", []) if x.get("id") == key or x.get("title") == key),
            {},
        )
        entry = {**product_match, **plan}
        entry.setdefault("title", key)
        sprint.append(entry)
        s["sprint_backlog"] = sprint
        updated_item = entry

    _ = save_state_to_repo(tool_context)
    story_md_result = _update_story_markdown(updated_item, tool_context)
    if story_md_result.get("status") != "ok":
        # Surface the failure (e.g. a Definition-of-Ready rejection) instead
        # of silently discarding it - a caller that only checks this
        # top-level status must be able to see the story file wasn't
        # actually written.
        return {"status": "error", "message": story_md_result.get("message"), "item": updated_item, "story_markdown": story_md_result}
    return {"status": "ok", "updated": (updated_item != ({"title": key, **plan})), "item": updated_item, "story_markdown": story_md_result}
