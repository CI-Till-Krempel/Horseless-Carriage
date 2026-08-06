# agents/scrum_team/tools/budget.py
from __future__ import annotations
import math
import os
import re
import requests
from typing import Any, Dict, List
from .base import _configured_repo_root
from ..helpers import (
    get_process_overhead_percentage,
    is_story_done,
    get_interaction_level,
    required_pre_implementation_approval,
    report_detail_level,
    get_env_with_deprecated_fallback,
)

def update_budgets(total_usd: float = None, tool_context=None) -> Dict[str, Any]:
    """
    Update the total USD budget for the sprint.
    - total_usd: USD budget for LiteLLM proxy
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    budgets = s.get("budgets", {})
    if total_usd is not None:
        budgets["total_usd"] = total_usd
    s["budgets"] = budgets
    save_state_to_repo(tool_context)
    return {"status": "ok", "budgets": budgets}

def get_budget_status(tool_context=None) -> Dict[str, Any]:
    """
    Return the current budget usage and status.
    """
    s = tool_context.state
    budgets = s.get("budgets", {})
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    status = {
        "budgets": budgets,
        "usage": usage,
        "total_usd": budgets.get("total_usd"),
    }
    return {"status": "ok", "budget_status": status}

def log_token_usage(agent_name: str, tokens: int, tool_context=None) -> Dict[str, Any]:
    """
    Manually log token usage for an agent (e.g. after a meeting or if automatic tracking is missing).
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    usage["total"] += tokens
    usage["agents"][agent_name] = usage["agents"].get(agent_name, 0) + tokens
    
    s["token_usage"] = usage
    save_state_to_repo(tool_context)
    return {"status": "ok", "usage": usage}

def reset_sprint_budget(tool_context=None) -> Dict[str, Any]:
    """
    Resets the LOGICAL token budget for a new sprint. SPRINT_TOKEN_BUDGET is
    a per-sprint allowance, not a cumulative total for the whole engagement -
    without this, token_usage.total only ever grows, so a sprint that used
    most of the budget silently starves every later sprint of any further
    LLM calls (check_cost_budget_callback compares token_usage.total against
    the same never-reset budgets.total). Call this once, at the start of
    every sprint after the first, before Sprint Planning.

    Deliberately does NOT touch budgets.total_usd: the USD guardrail is an
    intentional whole-run financial ceiling enforced by the LiteLLM proxy's
    shared scrum-sprint-budget object (see BUDGET.md), not a per-sprint one.

    Also clears the exhaustion-sync guard (see check_cost_budget_callback in
    agent.py) so a new sprint's exhaustion, if it happens, syncs the roadmap
    again rather than being silently skipped because a *previous* sprint
    already tripped it once. Likewise clears the critical-halt notification
    guard (GH issue #112) - see _notify_critical_halt in agent.py - so a
    halt in this new sprint notifies again rather than being silently
    skipped because a previous sprint's halt already fired it once.

    Also marks the budget as freshly reset (see GH issue #110) - start_sprint
    requires this to have happened since the previous sprint started (except
    for the very first sprint, which has no previous sprint's usage to
    clear), instead of relying on SM_PROMPT's "MANDATORY" text alone.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    s["token_usage"] = {"total": 0, "agents": {}}
    s["budget_exhaustion_synced"] = False
    s["budget_reset_since_last_sprint_start"] = True
    s["critical_halt_notified"] = False
    save_state_to_repo(tool_context)
    return {"status": "ok", "token_usage": s["token_usage"]}


def log_story_tokens(title_or_id: str, actual_tokens: int, tool_context=None) -> Dict[str, Any]:
    """
    Records the actual tokens spent implementing a story, alongside its
    estimate (see plan_sprint_backlog_item's `estimate` field), so
    create_sprint_report can show estimate-vs-actual per story instead of
    just the estimate - part of Definition of Done (see spec-templates/
    DOD.md): log this for a story before marking it Done.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    estimates = s.get("story_estimates", {})
    entry = estimates.get(title_or_id)
    # Older/other callers may still leave a bare number here (the original
    # shape, before actuals existed) - normalize to a dict without losing it.
    entry = entry if isinstance(entry, dict) else ({"estimate": entry} if entry is not None else {})
    entry["actual"] = actual_tokens
    estimates[title_or_id] = entry
    s["story_estimates"] = estimates
    save_state_to_repo(tool_context)
    return {"status": "ok", "story_estimates": estimates}

def create_litellm_virtual_key(agent_name: str, max_budget: float = None, budget_duration: str = None, models: List[str] = None, tool_context=None) -> Dict[str, Any]:
    """
    Generate a LiteLLM Virtual Key for a specific agent role with an optional budget.
    """
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://litellm:4000")
    
    if not master_key:
        return {"status": "error", "message": "LITELLM_MASTER_KEY environment variable not set."}

    budget_id = "scrum-sprint-budget"
    
    # 1. Ensure the shared budget object exists in LiteLLM
    try:
        get_resp = requests.post(
            f"{proxy_base}/budget/info",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json={"budgets": [budget_id]},
            timeout=5
        )
        get_resp.raise_for_status()
        
        # HARD GUARDRAIL: Use state budget if available, otherwise fallback to environment or safe default
        total_budget_usd = tool_context.state.get("budgets", {}).get("total_usd")
        if not total_budget_usd or total_budget_usd <= 0:
            try:
                total_budget_usd = float(get_env_with_deprecated_fallback("TOTAL_USD_BUDGET", "SPRINT_USD_BUDGET") or 10.0)
            except (ValueError, TypeError):
                total_budget_usd = 10.0
        
        # Check if the budget exists in the returned list
        exists = False
        budget_info_list = get_resp.json()
        if budget_info_list and isinstance(budget_info_list, list) and len(budget_info_list) > 0:
            exists = True
        
        if exists:
            upd_resp = requests.post(
                f"{proxy_base}/budget/update",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd
                },
                timeout=5
            )
            upd_resp.raise_for_status()
        else:
            new_resp = requests.post(
                f"{proxy_base}/budget/new",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd,
                    "budget_duration": "30d"
                },
                timeout=5
            )
            new_resp.raise_for_status()
    except Exception as e:
        return {"status": "error", "message": f"Budget API error: {e}"}
    
    # 2. Generate the Key
    url = f"{proxy_base}/key/generate"
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json"
    }
    
    if models is None:
        # Derived from get_model_name, not hardcoded route names (GH issue
        # #155): the eval harness (run_eval.py) points every role at
        # scrum-eval-cheap via SCRUM_<ROLE>_MODEL env overrides before this
        # ever runs, so a hardcoded production route list here left the
        # generated key unable to access whatever model each role is
        # actually configured to call - "key not allowed to access model
        # ... Tried to access scrum-eval-cheap". Deduplicated since an eval
        # run maps every role onto the same alias.
        from ..agent import get_model_name
        roles = ["po", "sm", "dev", "qa", "arch", "orchestrator", "quality"]
        models = list(dict.fromkeys(get_model_name(role) for role in roles))
    
    data = {
        "models": models,
        "metadata": {"agent": agent_name},
        "key_alias": f"key-{agent_name.lower()}",
        "budget_id": budget_id 
    }
    
    if max_budget is not None:
        # HARD GUARDRAIL: a real eval run crashed when the model picked
        # max_budget=0.1 for one agent's key (anchored on this wizard's own
        # illustrative "e.g., 0.50 for the sprint" example, see prompts.py),
        # while the shared budget_id above already enforces the real,
        # correctly-sized ceiling (total_budget_usd) across every agent -
        # this per-key cap is redundant at best. Worse, it's never reset or
        # recreated between sprints (reset_sprint_budget only clears local
        # token counters), so a too-small value starves that agent for the
        # rest of the run once hit, and the eventual litellm.RateLimitError
        # used to crash the whole process (see _patched_adk_acompletion).
        # Never let an individual key's cap be tighter than the shared
        # budget that's meant to be the real ceiling.
        max_budget = max(max_budget, total_budget_usd)
        data["max_budget"] = max_budget
    if budget_duration:
        data["budget_duration"] = budget_duration
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        if resp.status_code != 200:
            return {"status": "error", "message": f"Failed to generate LiteLLM key: {resp.status_code} {resp.text}"}
        resp.raise_for_status()
        res = resp.json()
        key = res.get("key")
        
        if not key:
            return {"status": "error", "message": "No key returned from LiteLLM proxy."}
        
        keys = tool_context.state.get("litellm_keys", {})
        keys[agent_name] = key
        tool_context.state["litellm_keys"] = keys
        
        return {"status": "ok", "agent": agent_name, "key": key, "max_budget": max_budget, "budget_duration": budget_duration, "budget_id": budget_id}
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate LiteLLM key: {e}"}

def _summarize_transcript(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Condenses a transcript to one representative (most recent) entry per
    agent, ordered by each agent's first appearance. A plain tail-N cut would
    let one chatty agent crowd out every other agent's key contribution;
    this guarantees every agent that spoke is represented.
    """
    order: List[str] = []
    last_by_agent: Dict[str, Dict[str, Any]] = {}
    for entry in transcript:
        agent_name = entry.get("agent_name", "unknown")
        if agent_name not in last_by_agent:
            order.append(agent_name)
        last_by_agent[agent_name] = entry
    return [last_by_agent[name] for name in order]

def _sprint_length_feedback(s: Dict[str, Any]) -> str:
    """
    Advisory-only: flags when this sprint looks budget-starved (hit its
    token cap with stories still unfinished) and suggests a new per-sprint
    token budget extrapolated from the observed tokens-per-completed-story
    rate. Never applied automatically - a human must change the actual
    config (SPRINT_TOKEN_BUDGET / EVAL_SPRINT_TOKEN_BUDGET) - see README.md
    "Budget Management" / RELEASE.md "Team performance evaluation".
    """
    budgets = s.get("budgets", {})
    token_limit = budgets.get("total", 0)
    if token_limit <= 0:
        try:
            token_limit = int(os.environ.get("SPRINT_TOKEN_BUDGET", 1000000))
        except (ValueError, TypeError):
            token_limit = 1000000

    token_used = s.get("token_usage", {}).get("total", 0)
    backlog = s.get("sprint_backlog", []) or []
    total_items = len(backlog)
    done_items = len([i for i in backlog if is_story_done(i.get("status"))])
    unfinished = total_items - done_items
    pct = (token_used / token_limit * 100) if token_limit else 0.0

    lines = [
        "\n## Sprint Length Feedback\n",
        f"- Tokens used: {token_used:,} / {token_limit:,} ({pct:.0f}%)\n",
        f"- Stories: {done_items}/{total_items} completed this sprint\n",
    ]

    if total_items == 0:
        lines.append("- No sprint backlog recorded - nothing to assess.\n")
        return "".join(lines)
    if unfinished == 0:
        lines.append("- All planned stories were completed within budget - no sprint-length change suggested.\n")
        return "".join(lines)

    if token_limit > 0 and token_used >= 0.9 * token_limit:
        avg_per_done = token_used / done_items if done_items else token_used / total_items
        projected_total = avg_per_done * total_items
        suggested = math.ceil(projected_total * 1.2 / 50_000) * 50_000
        lines += [
            f"- This sprint used {pct:.0f}% of its token budget and left {unfinished}/{total_items} "
            "stories unfinished - the per-sprint token budget looks too small for the amount of work "
            "planned, not necessarily a quality problem.\n",
            f"- **Suggested new per-sprint token budget: ~{suggested:,} tokens** (extrapolated from "
            f"~{avg_per_done:,.0f} tokens/completed story x {total_items} planned stories, +20% headroom).\n",
            "- **This is a recommendation only - it is NOT applied automatically.** A human must "
            "approve it and set it manually (`SPRINT_TOKEN_BUDGET` / `EVAL_SPRINT_TOKEN_BUDGET`; see "
            "README.md \"Budget Management\").\n",
        ]
    else:
        remaining = max(token_limit - token_used, 0)
        lines.append(
            f"- {unfinished}/{total_items} stories are unfinished despite {remaining:,} tokens of "
            "budget headroom still available - the sprint-length token budget is likely NOT the "
            "cause; consider reviewing process/quality issues instead (see Top Problems / retro "
            "actions) rather than increasing the budget.\n"
        )
    return "".join(lines)


_SPRINT_REPORT_NUM_PATTERN = re.compile(r"SPRINT-REPORT-(\d+)\.md$")


def _next_sprint_report_path(tool_context) -> str:
    """
    Picks the next sequential specs/reports/SPRINT-REPORT-NNN.md path by
    scanning what's already there - self-healing like _generate_next_id,
    no separate counter in state to drift out of sync. Previously every
    sprint overwrote the same SPRINT-REPORT-LATEST.md, so only the final
    sprint's report ever survived in the repo.
    """
    repo_root = _configured_repo_root(tool_context)
    reports_dir = repo_root / "specs" / "reports"
    max_num = 0
    if reports_dir.exists():
        for fp in reports_dir.glob("SPRINT-REPORT-*.md"):
            m = _SPRINT_REPORT_NUM_PATTERN.match(fp.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
    return f"specs/reports/SPRINT-REPORT-{max_num + 1:03d}.md"


_TRANSCRIPT_NUM_PATTERN = re.compile(r"TRANSCRIPT-(\d+)\.md$")


def _next_transcript_path(tool_context) -> str:
    """Mirrors _next_sprint_report_path, one sequence per artifact type."""
    repo_root = _configured_repo_root(tool_context)
    reports_dir = repo_root / "specs" / "reports"
    max_num = 0
    if reports_dir.exists():
        for fp in reports_dir.glob("TRANSCRIPT-*.md"):
            m = _TRANSCRIPT_NUM_PATTERN.match(fp.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
    return f"specs/reports/TRANSCRIPT-{max_num + 1:03d}.md"


def _write_conversation_transcript(tool_context=None) -> Dict[str, Any]:
    """
    Renders state.transcript - every agent's model turns
    (history_management_after_callback) and tool calls
    (log_tool_invocation_callback), both in agent.py - as a human-readable
    Markdown file grouped by agent in chronological order, and writes it
    into the target repo alongside the sprint report (GH issue #127).
    Replaces what used to be a raw, unbounded JSON blob written straight
    into the target repo's git-committed .hc/state.json - state.transcript
    itself is now in-memory-only session state, used just to render this
    file and the sprint report's excerpt. A per-run raw log additionally
    exists at /app/sessions/transcript-<session-id>.log (see
    transcript_logger in agent.py) independent of this markdown file.
    """
    from .docs import write_file
    s = tool_context.state
    transcript = s.get("transcript", []) or []

    lines = ["# Conversation Transcript\n"]
    if not transcript:
        lines.append("\nNo transcript recorded yet for this sprint.\n")
    else:
        current_agent = None
        for entry in transcript:
            agent_name = entry.get("agent_name", "unknown")
            if agent_name != current_agent:
                lines.append(f"\n## {agent_name}\n")
                current_agent = agent_name
            content = entry.get("content", "")
            if entry.get("role") == "tool_call":
                lines.append(f"- \U0001f527 `{content}`\n")
            else:
                lines.append(f"\n{content}\n")

    report = "".join(lines)
    numbered_path = _next_transcript_path(tool_context)
    write_file(numbered_path, report, overwrite=True, tool_context=tool_context)
    latest_path = "specs/reports/TRANSCRIPT-LATEST.md"
    write_file(latest_path, report, overwrite=True, tool_context=tool_context)

    return {"status": "ok", "path": numbered_path, "latest_path": latest_path, "entries": len(transcript)}


def _file_retro_items_as_issues(tool_context) -> List[str]:
    """
    GH issue #164: retro actions and impediments must become real,
    trackable backlog work, not just a text log nobody ever revisits - the
    exact failure mode reported (a real eval run's retrospective logged
    that pytest couldn't generate coverage, but nothing turned that into a
    fix, so it silently blocked every later sprint the same way).

    Files every not-yet-converted retro_actions/impediment_log entry
    (tracked via its own "issue_id" once filed, so re-running this on a
    later sprint's report doesn't re-file the same items) as an Issue via
    upsert_issue - the same backlog pipeline a real Product Owner works
    from, so it's plannable/prioritizable, not a separate list nobody
    grooms. At the "Product" interaction level, priority is left for the
    human Product Owner to set via their own backlog grooming; at every
    other level (no human review step in that loop), it's auto-prioritized
    "Must" so it can't be silently starved the way the reported failure
    was.
    """
    from .requirements import upsert_issue, set_priority

    filed_ids = []
    for collection_name, text_field in (("retro_actions", "action"), ("impediment_log", "description")):
        collection = list(tool_context.state.get(collection_name, []))
        for entry in collection:
            if entry.get("issue_id"):
                continue
            text = (entry.get(text_field) or "").strip()
            if not text:
                continue
            res = upsert_issue(
                {"title": text, "overview": text, "owner": entry.get("owner", "")},
                tool_context=tool_context,
            )
            if res.get("status") != "ok":
                continue
            issue_id = res["item"]["id"]
            entry["issue_id"] = issue_id
            filed_ids.append(issue_id)
            if get_interaction_level() != "Product":
                set_priority(issue_id, "Must", tool_context=tool_context)
        tool_context.state[collection_name] = collection
    return filed_ids


def create_sprint_report(summary: str, accomplishments: List[str], tool_context=None) -> Dict[str, Any]:
    """
    Generate a management summary report for the current sprint.
    """
    from .docs import write_file
    s = tool_context.state
    budgets = s.get("budgets", {})
    usage = s.get("token_usage", {"total": 0})
    retro = s.get("retro_actions", [])
    impediments = s.get("impediment_log", [])

    # Mandatory: refuse to close the sprint report unless a *new* retro
    # action or impediment has actually been logged since the last one -
    # not just "one exists somewhere in history" (retro_actions/
    # impediment_log accumulate across the whole run, so that check would
    # trivially pass forever after the first sprint ever logs one). Across
    # real eval runs, the Scrum Master's retrospective step was reliably
    # skipped every single sprint - the prompt said it was mandatory, but
    # nothing actually stopped the sprint from "closing" without it. This
    # is the mechanical backstop: create_sprint_report itself won't
    # complete without it, forcing an actual hand-off to Scrum Master
    # rather than a step that's silently optional in practice.
    process_signals = len(retro) + len(impediments)
    baseline = s.get("retro_baseline", 0)
    if process_signals <= baseline:
        return {
            "status": "error",
            "message": (
                "Cannot close the sprint report: no new retrospective action or impediment has "
                "been logged since the last sprint report. Transfer to Scrum Master to actually "
                "run the retrospective (add_retro_action) or log a real impediment "
                "(add_impediment) first - see SM_PROMPT's RETROSPECTIVE REASONING - then retry "
                "create_sprint_report. This is mandatory, not optional."
            ),
        }

    # GH issue #164: convert this sprint's retro/impediment findings into
    # real backlog work before rendering the report below, so the report
    # can actually say what each item was filed as instead of the finding
    # just sitting in this log unactioned (see _file_retro_items_as_issues).
    _file_retro_items_as_issues(tool_context)
    retro = s.get("retro_actions", [])
    impediments = s.get("impediment_log", [])

    hc_version = s.get("hc_version", "unknown")
    hc_version_line = f"Horseless Carriage v{hc_version}" if hc_version != "unknown" else "Horseless Carriage (version unknown)"
    # See docs/INTERACTION-LEVELS.md - this is the "management summary" a
    # CEO-level human relies on instead of reviewing individual sprints/
    # releases, so it always states which level generated it.
    report = (
        f"# Sprint Review Report\n\n**Generated by {hc_version_line}** "
        f"(Interaction Level: {get_interaction_level()})\n\n## Summary\n{summary}\n\n## Accomplishments\n"
    )
    for item in accomplishments:
        report += f"- {item}\n"
        
    # How much of what follows actually gets rendered depends on the
    # interaction level (see docs/INTERACTION-LEVELS.md) - "full" (Product,
    # EVAL) keeps everything below; "business" (Stakeholder) drops
    # internal/technical numbers; "executive" (CEO) is budget + headline
    # outcomes only. The underlying data (retro_actions, impediment_log,
    # story_estimates, transcript) is never trimmed in state - only what
    # this one rendering surfaces to the human reading it.
    detail = report_detail_level()
    omitted_sections = []

    report += f"\n## Budget and Usage\n"
    if budgets.get("total_usd"):
        report += f"- USD Budget (LiteLLM): ${budgets.get('total_usd'):.2f}\n"
        # Actual spend (GH issue #111) - set by check_cost_budget_callback's
        # live LiteLLM proxy check (agent.py), so it's only available once
        # that check has actually run at least once this session (e.g.
        # never, for a purely local/Ollama sprint - see LLM_LOCAL_PROVIDER).
        current_spend = budgets.get("current_usd_spend")
        if current_spend is not None:
            report += f"- Actual USD Spend (LiteLLM): ${current_spend:.2f}\n"
        else:
            report += "- Actual USD Spend (LiteLLM): not yet available (no live proxy budget check has run this session)\n"

    report += f"- Process Overhead: {get_process_overhead_percentage()}%\n"

    if detail == "full":
        report += "\n### Per-Agent Token Usage\n"
        for agent, agent_usage in usage.get("agents", {}).items():
            report += f"  - {agent}: {agent_usage}\n"
    else:
        omitted_sections.append("Per-Agent Token Usage")

    # Budget recommendation - relevant at every level, including CEO, since
    # it's literally a suggested change to the number a CEO approves.
    report += _sprint_length_feedback(s)

    # Any story still BLOCKED (raise_story_blocker, agents/scrum_team/tools/
    # requirements.py) when the sprint closes - never mind an unresolved
    # question, or a mechanical loop-detection trip that couldn't find a
    # resolution this sprint. Rendered at every interaction level
    # (unconditionally, unlike the detail-gated sections below): a
    # Stakeholder giving guidance is exactly who this is for, but a CEO
    # reading only the executive summary still needs to know the increment
    # is incomplete because of a genuine open question, not just budget.
    blocked_stories = []
    seen_ids = set()
    for collection in (s.get("product_backlog", []) or [], s.get("sprint_backlog", []) or []):
        for item in collection:
            blocked = item.get("blocked")
            if not blocked:
                continue
            story_id = item.get("id") or item.get("title")
            if story_id in seen_ids:
                continue
            seen_ids.add(story_id)
            blocked_stories.append((story_id, item.get("title", story_id), blocked))

    report += "\n## Open Questions for Stakeholder\n"
    if blocked_stories:
        for story_id, story_title, blocked in blocked_stories:
            report += (
                f"- **{story_id}** ({story_title}) - {blocked.get('category')}: {blocked.get('question')} "
                f"(raised by {blocked.get('raised_by', 'unknown')})\n"
            )
    else:
        report += "No stories are currently blocked.\n"

    if detail in ("full", "business"):
        report += "\n## Retrospective Actions (including efficiency improvements)\n"
        if retro:
            for action in retro:
                issue_note = f", filed as {action['issue_id']}" if action.get("issue_id") else ""
                report += f"- {action['action']} (Owner: {action['owner']}, Status: {action['status']}{issue_note})\n"
        else:
            # This branch is now only reachable when the mandatory gate above
            # was satisfied by a *new impediment* instead of a retro action -
            # create_sprint_report refuses to run at all otherwise. Rendered
            # explicitly anyway so the report never implies retro happened
            # when only an impediment did.
            report += "No retro actions recorded - see Impediments below for what satisfied this sprint's requirement.\n"

        report += "\n## Impediments\n"
        if impediments:
            for imp in impediments:
                issue_note = f", filed as {imp['issue_id']}" if imp.get("issue_id") else ""
                report += f"- {imp['description']} (Owner: {imp['owner']}, Status: {imp['status']}{issue_note})\n"
        else:
            report += "No impediments logged.\n"
    else:
        omitted_sections += ["Retrospective Actions", "Impediments"]

    # Include story estimates if present
    estimates = s.get("story_estimates", {})
    if detail in ("full", "business"):
        if estimates:
            report += "\n## Story Estimates vs Actual Tokens\n"
            for title, entry in estimates.items():
                if isinstance(entry, dict):
                    estimate = entry.get("estimate", "n/a")
                    actual = entry.get("actual", "not logged")
                else:
                    # Pre-actual-tracking shape: a bare number was always the estimate.
                    estimate, actual = entry, "not logged"
                report += f"- {title}: estimate={estimate}, actual={actual}\n"
    elif estimates:
        omitted_sections.append("Story Estimates vs Actual Tokens")

    # GH issue #127: the multi-agent transcript is now a human-readable
    # Markdown file written into the target repo (write_conversation_
    # transcript), not a raw blob inside the target repo's git-committed
    # .hc/state.json - written unconditionally so the file exists
    # regardless of which detail level's report text references it.
    # "full" adds a condensed per-agent excerpt so a technical reviewer can
    # trace which agent made which decision without opening the full
    # transcript file; "business" keeps just the location pointer, since
    # that level of technical trace has no use for a business stakeholder;
    # "executive" omits the section entirely (see omitted_sections below).
    transcript = s.get("transcript", [])
    transcript_result = _write_conversation_transcript(tool_context)
    if detail in ("full", "business"):
        report += "\n## Conversation Transcript\n"
        if transcript:
            report += (
                f"Full transcript ({len(transcript)} entries) written to "
                f"`{transcript_result['path']}` (also mirrored at "
                f"`{transcript_result['latest_path']}`).\n\n"
            )
            if detail == "full":
                report += "Most recent contribution per agent:\n"
                for entry in _summarize_transcript(transcript):
                    agent_name = entry.get("agent_name", "unknown")
                    content = entry.get("content", "")
                    excerpt = content if len(content) <= 200 else content[:200].rstrip() + "..."
                    report += f"- **{agent_name}**: {excerpt}\n"
        else:
            report += "No transcript available yet for this sprint.\n"
    else:
        omitted_sections.append("Conversation Transcript")

    if omitted_sections:
        # The underlying data isn't trimmed anywhere except this rendering -
        # Retrospective Actions/Impediments/Story Estimates still live in
        # full in state (`.hc/state.json`, or `read_doc`/state tools
        # in-session); the Conversation Transcript, if omitted here, is
        # still written in full to `specs/reports/TRANSCRIPT-LATEST.md`
        # regardless of interaction level. Available in full at the
        # Product/EVAL interaction level either way.
        report += (
            f"\n## Full Process Detail\nOmitted from this {detail} summary (Interaction Level: "
            f"{get_interaction_level()}): {', '.join(omitted_sections)}. This underlying data is "
            "not deleted - see the note above for exactly where each item still lives.\n"
        )

    s["sprint_report"] = report
    numbered_path = _next_sprint_report_path(tool_context)
    write_file(numbered_path, report, overwrite=True, tool_context=tool_context)
    latest_path = "specs/reports/SPRINT-REPORT-LATEST.md"
    write_file(latest_path, report, overwrite=True, tool_context=tool_context)

    # Snapshot the count that satisfied this sprint's requirement, so next
    # sprint's gate demands something *new* again rather than trivially
    # passing forever on the same old entries.
    s["retro_baseline"] = process_signals

    # ISSUE-0001: closing this sprint's report "uses up" its human
    # pre-implementation approval - the next sprint's stories can't reach
    # Implemented again until a fresh one is recorded via
    # record_human_approval(<type>, ...), where <type> depends on the
    # configured interaction level (see docs/INTERACTION-LEVELS.md) - e.g.
    # "budget" instead of "sprint" at the CEO level. Left unchanged (there's
    # nothing to snapshot) at levels that require no such approval (EVAL).
    required_approval = required_pre_implementation_approval()
    if required_approval:
        s["sprint_approval_baseline"] = sum(
            1 for a in s.get("human_approvals", []) if a.get("type") == required_approval
        )

    # ISSUE-0010: a report now exists for this sprint but its release PR
    # hasn't necessarily gone out yet - advance_story_stage's Implemented
    # gate refuses further story work until create_release_pr clears this.
    s["sprint_report_pending_release"] = True

    return {"status": "ok", "report": report, "path": numbered_path, "latest_path": latest_path}

def calculate_cost_breakdown(tool_context=None) -> Dict[str, Any]:
    """
    Calculates the cost breakdown of the specific roles and the percentage of tokens used for feature implementation.
    """
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    total_tokens = usage.get("total", 1)  # Avoid division by zero

    cost_breakdown = {
        "per_role": usage.get("agents", {}),
        "feature_implementation_percentage": (
            usage.get("agents", {}).get("DevTeam", 0) / total_tokens
        )
        * 100,
    }
    return {"status": "ok", "cost_breakdown": cost_breakdown}

def recommend_sprint_budget(tool_context=None) -> Dict[str, Any]:
    """
    Recommends a sprint budget based on historical data.
    """
    # In a real implementation, this would analyze historical data to provide a more accurate recommendation.
    return {"status": "ok", "recommended_budget": 10.0}

def optimize_process_for_budget(tool_context=None) -> Dict[str, Any]:
    """
    Optimizes the amount of overhead spent on process based on the sprint budget.
    """
    s = tool_context.state
    budgets = s.get("budgets", {})
    
    process_overhead_percentage = get_process_overhead_percentage()
    
    if budgets.get("total_usd", 0) * (process_overhead_percentage / 100) < 2:
        # Lightweight process for small budgets
        process_optimizations = [
            "Reduced number of meetings",
            "Simplified reporting",
        ]
    else:
        # Standard process for larger budgets
        process_optimizations = []

    return {"status": "ok", "process_optimizations": process_optimizations}