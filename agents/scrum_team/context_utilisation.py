# agents/scrum_team/context_utilisation.py
"""
Runtime context-window utilisation KPI (GH issue #566, US-0034).

US-0033 (GH-513) already measures how much of each role's context window
its *static* system prompt costs (tools/quality.py's
calculate_prompt_context_usage) - a fixed number per role, computed once.
What it can't show is how full the context actually gets at runtime, once
conversation history, injected sprint status and tool results pile up on
top of that prompt. This module tracks exactly that: per LLM call, the
prompt/input tokens the provider actually billed for
(`usage_metadata.prompt_token_count`) against the role's model's context
window, aggregated per sprint and per role into mean/peak utilisation and a
count of calls above a high-water threshold - so a maintainer can tell when
a role is close to its model's limit and a larger-context model, a
prompt trim, or history compaction is warranted.

Deliberately pure stdlib (no litellm/requests/ADK imports): the host-side
dashboard (dashboard.py) and the eval analysis script import the
summarize/format helpers below directly, the same way dashboard.py already
imports agents.scrum_team.helpers. Resolving a model's context window (which
needs the LiteLLM proxy) lives in tools/quality.py's
resolve_context_window instead.

Observability only - nothing here feeds budget enforcement
(check_cost_budget_callback, SPRINT_TOKEN_BUDGET, ...; see docs/BUDGET.md
and AGENTS.md section 12). The state key it writes, `context_utilisation`,
is additive and optional: an older state.json without it simply reads as
"no data recorded yet".

State shape (ScrumState.context_utilisation):

    {
      "sprints": {
        "<sprint_number>": {
          "high_water_percent": 80.0,
          "agents": {
            "<RoleName>": {
              "model": "scrum-dev",
              "context_window_tokens": 1048576,
              "window_source": "litellm_proxy" | "override" | "litellm_model_map" | "fallback",
              "calls": 12,
              "sum_percent": 123.4,
              "sum_prompt_tokens": 1294000,
              "peak_percent": 45.6,
              "peak_prompt_tokens": 478000,
              "high_water_calls": 0
            }
          }
        }
      }
    }

Keyed by sprint number (not reset by start_sprint) so a few sprints of
history stay available for trends; pruned to the most recent
MAX_SPRINTS_RETAINED sprints so it can't grow without bound.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_HIGH_WATER_PERCENT = 80.0
DEFAULT_FALLBACK_CONTEXT_WINDOW_TOKENS = 128_000
MAX_SPRINTS_RETAINED = 10

# The window_source value meaning "we didn't actually know this model's
# window and used CONTEXT_WINDOW_FALLBACK_TOKENS instead" - surfaced
# alongside every number derived from it, never silently presented as real.
FALLBACK_SOURCE = "fallback"


def high_water_percent() -> float:
    """CONTEXT_UTILISATION_HIGH_WATER_PERCENT (default 80) - a call whose
    prompt fills at least this much of its model's context window counts as
    a high-water call. Invalid/non-positive values fall back to the default."""
    raw = os.getenv("CONTEXT_UTILISATION_HIGH_WATER_PERCENT", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HIGH_WATER_PERCENT
    return value if 0 < value <= 100 else DEFAULT_HIGH_WATER_PERCENT


def fallback_context_window_tokens() -> int:
    """CONTEXT_WINDOW_FALLBACK_TOKENS (default 128000) - the denominator used
    when a model's real context window can't be determined from an override,
    the LiteLLM proxy, or litellm's own model map."""
    raw = os.getenv("CONTEXT_WINDOW_FALLBACK_TOKENS", "")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_FALLBACK_CONTEXT_WINDOW_TOKENS
    return value if value > 0 else DEFAULT_FALLBACK_CONTEXT_WINDOW_TOKENS


def parse_context_window_overrides(raw: Optional[str] = None) -> Dict[str, int]:
    """Parses CONTEXT_WINDOW_OVERRIDES - comma-separated `<model>=<tokens>`
    pairs keyed by the model alias a role is configured with (e.g.
    "scrum-dev=32768,scrum-po=1048576"). Useful for a local/Ollama model
    whose effective window (num_ctx) is smaller than litellm's map claims,
    or a model litellm doesn't know at all. Malformed entries are skipped,
    never an error."""
    if raw is None:
        raw = os.getenv("CONTEXT_WINDOW_OVERRIDES", "")
    overrides: Dict[str, int] = {}
    for chunk in (raw or "").split(","):
        if "=" not in chunk:
            continue
        model, _, tokens = chunk.partition("=")
        model = model.strip()
        try:
            value = int(float(tokens.strip()))
        except (TypeError, ValueError):
            continue
        if model and value > 0:
            overrides[model] = value
    return overrides


def record_call(
    ctx: Optional[Dict[str, Any]],
    sprint_number: Any,
    role: str,
    model: str,
    prompt_tokens: int,
    context_window_tokens: int,
    window_source: str,
    threshold_percent: Optional[float] = None,
) -> Dict[str, Any]:
    """Returns a NEW context_utilisation dict with one LLM call folded into
    `role`'s aggregate for `sprint_number`. Never mutates `ctx` (it may be a
    view into live session state). A non-positive prompt_tokens or context
    window is a no-op (returns an unchanged copy)."""
    result: Dict[str, Any] = copy.deepcopy(ctx) if isinstance(ctx, dict) else {}
    try:
        prompt_tokens = int(prompt_tokens or 0)
        context_window_tokens = int(context_window_tokens or 0)
    except (TypeError, ValueError):
        return result
    if prompt_tokens <= 0 or context_window_tokens <= 0:
        return result

    threshold = high_water_percent() if threshold_percent is None else float(threshold_percent)
    sprints = result.get("sprints")
    if not isinstance(sprints, dict):
        sprints = {}
        result["sprints"] = sprints
    key = str(sprint_number if sprint_number is not None else 0)
    sprint = sprints.get(key)
    if not isinstance(sprint, dict):
        sprint = {"high_water_percent": threshold, "agents": {}}
        sprints[key] = sprint
    agents = sprint.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        sprint["agents"] = agents
    entry = agents.get(role)
    if not isinstance(entry, dict):
        entry = {
            "calls": 0,
            "sum_percent": 0.0,
            "sum_prompt_tokens": 0,
            "peak_percent": 0.0,
            "peak_prompt_tokens": 0,
            "high_water_calls": 0,
        }
        agents[role] = entry

    percent = 100.0 * prompt_tokens / context_window_tokens
    sprint_threshold = sprint.get("high_water_percent") or threshold
    entry["model"] = model
    entry["context_window_tokens"] = context_window_tokens
    entry["window_source"] = window_source
    entry["calls"] = int(entry.get("calls", 0)) + 1
    entry["sum_percent"] = round(float(entry.get("sum_percent", 0.0)) + percent, 4)
    entry["sum_prompt_tokens"] = int(entry.get("sum_prompt_tokens", 0)) + prompt_tokens
    if percent >= float(entry.get("peak_percent", 0.0)):
        entry["peak_percent"] = round(percent, 2)
        entry["peak_prompt_tokens"] = prompt_tokens
    if percent >= float(sprint_threshold):
        entry["high_water_calls"] = int(entry.get("high_water_calls", 0)) + 1

    # Bound growth: keep only the most recent MAX_SPRINTS_RETAINED sprints.
    if len(sprints) > MAX_SPRINTS_RETAINED:
        def _sort_key(k: str):
            try:
                return (0, int(k))
            except ValueError:
                return (1, 0)
        for stale in sorted(sprints.keys(), key=_sort_key)[: len(sprints) - MAX_SPRINTS_RETAINED]:
            sprints.pop(stale, None)
    return result


def summarize(ctx: Optional[Dict[str, Any]], sprint_number: Any) -> Dict[str, Any]:
    """Per-role and team-level KPI summary for one sprint:

        {"available": bool, "sprint_number": ..., "high_water_percent": 80.0,
         "team": {"calls", "mean_percent", "peak_percent", "high_water_calls", "peak_role"},
         "agents": {role: {"model", "context_window_tokens", "window_source", "calls",
                           "mean_percent", "peak_percent", "mean_prompt_tokens",
                           "peak_prompt_tokens", "high_water_calls"}},
         "flagged_roles": [role, ...],   # peak >= high-water threshold
         "estimated_window_roles": [role, ...]}  # window_source == "fallback"

    `available` is False (with a `note`) when nothing was recorded for that
    sprint - never a fabricated zero."""
    threshold = high_water_percent()
    empty = {
        "available": False,
        "sprint_number": sprint_number,
        "high_water_percent": threshold,
        "team": {},
        "agents": {},
        "flagged_roles": [],
        "estimated_window_roles": [],
        "note": "no LLM calls with prompt-token usage were recorded for this sprint",
    }
    if not isinstance(ctx, dict):
        return empty
    sprint = (ctx.get("sprints") or {}).get(str(sprint_number if sprint_number is not None else 0))
    if not isinstance(sprint, dict):
        return empty
    agents_raw = sprint.get("agents") or {}
    if not isinstance(agents_raw, dict) or not agents_raw:
        return empty
    threshold = float(sprint.get("high_water_percent") or threshold)

    agents: Dict[str, Dict[str, Any]] = {}
    total_calls = 0
    total_sum_percent = 0.0
    total_high_water = 0
    peak_percent = 0.0
    peak_role = None
    for role, entry in agents_raw.items():
        if not isinstance(entry, dict):
            continue
        calls = int(entry.get("calls") or 0)
        if calls <= 0:
            continue
        role_peak = float(entry.get("peak_percent") or 0.0)
        agents[role] = {
            "model": entry.get("model"),
            "context_window_tokens": entry.get("context_window_tokens"),
            "window_source": entry.get("window_source"),
            "calls": calls,
            "mean_percent": round(float(entry.get("sum_percent") or 0.0) / calls, 2),
            "peak_percent": round(role_peak, 2),
            "mean_prompt_tokens": int(round(int(entry.get("sum_prompt_tokens") or 0) / calls)),
            "peak_prompt_tokens": int(entry.get("peak_prompt_tokens") or 0),
            "high_water_calls": int(entry.get("high_water_calls") or 0),
        }
        total_calls += calls
        total_sum_percent += float(entry.get("sum_percent") or 0.0)
        total_high_water += int(entry.get("high_water_calls") or 0)
        if peak_role is None or role_peak > peak_percent:
            peak_percent = role_peak
            peak_role = role
    if not total_calls:
        return empty

    return {
        "available": True,
        "sprint_number": sprint_number,
        "high_water_percent": threshold,
        "team": {
            "calls": total_calls,
            "mean_percent": round(total_sum_percent / total_calls, 2),
            "peak_percent": round(peak_percent, 2),
            "peak_role": peak_role,
            "high_water_calls": total_high_water,
        },
        "agents": agents,
        "flagged_roles": sorted(r for r, a in agents.items() if a["peak_percent"] >= threshold),
        "estimated_window_roles": sorted(r for r, a in agents.items() if a.get("window_source") == FALLBACK_SOURCE),
    }


def render_report_section(summary: Dict[str, Any], detail: str) -> Tuple[str, List[str]]:
    """Markdown for the sprint report's "Context Utilisation" section.

    The team-level headline (and any high-water advisory) renders at every
    interaction level - it's a single line and exactly the signal that
    should trigger a model-choice/prompt review. The per-role breakdown is
    "full"-detail only, matching Per-Agent Token Usage's own gating; at lower
    levels its name is returned in the omitted-sections list instead."""
    omitted: List[str] = []
    text = "\n### Context Utilisation\n"
    if not isinstance(summary, dict) or not summary.get("available"):
        note = (summary or {}).get("note") if isinstance(summary, dict) else None
        text += f"- Not available ({note or 'no data recorded'}).\n"
        return text, omitted

    team = summary.get("team") or {}
    threshold = summary.get("high_water_percent", DEFAULT_HIGH_WATER_PERCENT)
    text += (
        f"- Team: mean {team.get('mean_percent')}% / peak {team.get('peak_percent')}% of context window "
        f"(peak: {team.get('peak_role')}) across {team.get('calls')} LLM calls; "
        f"{team.get('high_water_calls')} call(s) at or above the {threshold:g}% high-water mark\n"
    )
    flagged = summary.get("flagged_roles") or []
    if flagged:
        text += (
            f"- ⚠️ Context pressure: {', '.join(flagged)} reached the {threshold:g}% high-water mark - "
            "consider a larger-context model for that role, trimming its prompt modules, or compacting its "
            "conversation history (GH issue #566).\n"
        )
    estimated = summary.get("estimated_window_roles") or []
    if estimated:
        text += (
            f"- Note: context window for {', '.join(estimated)} was not known and the configured fallback "
            "(CONTEXT_WINDOW_FALLBACK_TOKENS) was used - set CONTEXT_WINDOW_OVERRIDES for exact figures.\n"
        )

    if detail == "full":
        text += "\n#### Per-Agent Context Utilisation\n"
        for role, entry in sorted((summary.get("agents") or {}).items()):
            window = entry.get("context_window_tokens") or 0
            text += (
                f"  - {role} ({entry.get('model')}, {window:,}-token window, {entry.get('window_source')}): "
                f"mean {entry.get('mean_percent')}% / peak {entry.get('peak_percent')}% "
                f"({entry.get('peak_prompt_tokens'):,} tokens) over {entry.get('calls')} calls, "
                f"{entry.get('high_water_calls')} at/above high-water\n"
            )
    else:
        omitted.append("Per-Agent Context Utilisation")
    return text, omitted


def short_label(summary: Dict[str, Any]) -> str:
    """One-line form for compact surfaces (dashboard cell): "ctx mean 12.3% /
    peak 45.6% (2 ≥80%)", or "" when nothing is available."""
    if not isinstance(summary, dict) or not summary.get("available"):
        return ""
    team = summary.get("team") or {}
    threshold = summary.get("high_water_percent", DEFAULT_HIGH_WATER_PERCENT)
    return (
        f"ctx mean {team.get('mean_percent')}% / peak {team.get('peak_percent')}% "
        f"({team.get('high_water_calls', 0)} ≥{threshold:g}%)"
    )
