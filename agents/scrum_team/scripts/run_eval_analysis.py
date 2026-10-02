#!/usr/bin/env python3
"""
Post-run analysis for the team-performance evaluation harness (see
RELEASE.md "Team performance evaluation", run_eval.py).

Reads a run manifest (produced by run_eval.py) plus the final state of the
eval repo's branch, sends both to a judge LLM call against a fixed rubric
(code quality, requirements quality, team efficiency), and writes a
Markdown report with the top problems and suggested fixes.

Usage:
    python3 -m agents.scrum_team.scripts.run_eval_analysis \
        --manifest eval-run-<id>.json --repo-path <local clone path> \
        --report-path EVAL-REPORT-<id>.md \
        --run-id <id> --base-branch eval/<id>/main
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# scrum-quality (the "real" quality-review role alias) points at
# gemini-1.5-pro like every other production alias, which 404s as a
# retired model against a live key today (see litellm.yaml's
# scrum-eval-cheap comment) - reusing the harness's own known-working
# cheap alias here instead, consistent with the "small budget, cheap
# models" approach for the whole harness.
DEFAULT_JUDGE_MODEL = "scrum-eval-cheap"

# File extensions worth showing the judge in full. Everything else (state
# files, lockfiles, etc.) is listed by path only - keeps the prompt a
# reasonable size and focused on what a human reviewer would actually read.
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".md", ".txt", ".json", ".yaml", ".yml"}
SKIP_DIRS = {".git", ".hc", "node_modules", "__pycache__", ".venv"}
MAX_FILE_CHARS = 8000
MAX_TOTAL_CODE_CHARS = 60000

# Underlying provider model per driver-model alias (see litellm.yaml) - used
# only to look up litellm's static per-token pricing table for a rough cost
# estimate. manifest token_usage doesn't split prompt/completion tokens, so
# _estimate_cost_range reports a [low, high] bound (all-input vs
# all-output), not a single exact figure.
MODEL_PRICING_LOOKUP = {
    "scrum-eval-cheap": "gemini/gemini-flash-lite-latest",
}


def _total_tokens_used(manifest: dict) -> int:
    """
    token_usage.total now resets at the start of every sprint (see
    run_eval.py's _run_one_sprint state_delta / reset_sprint_budget) - each
    sprint row reflects only that sprint's own consumption, so the run's
    total is the SUM of every sprint row, not the last (biggest) one.
    """
    totals = [(s.get("token_usage") or {}).get("total", 0) for s in manifest.get("sprints", [])]
    return sum(totals)


def _estimate_cost_range(total_tokens: int, model_alias: str):
    """Returns (low, high) USD, or None if the model isn't in MODEL_PRICING_LOOKUP or litellm has no pricing for it."""
    import litellm

    underlying = MODEL_PRICING_LOOKUP.get(model_alias)
    if not underlying or total_tokens <= 0:
        return None
    try:
        low, _ = litellm.cost_per_token(model=underlying, prompt_tokens=total_tokens, completion_tokens=0)
        _, high = litellm.cost_per_token(model=underlying, prompt_tokens=0, completion_tokens=total_tokens)
        return (low, high) if low <= high else (high, low)
    except Exception:
        return None


def _collect_repo_snapshot(repo_path: Path) -> dict:
    """Real file tree + capped file contents - never fabricated, and
    honest about what got truncated."""
    all_paths = []
    file_contents = {}
    total_chars = 0
    truncated_files = []

    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = str(full.relative_to(repo_path))
            all_paths.append(rel)
            if full.suffix not in CODE_EXTENSIONS:
                continue
            if total_chars >= MAX_TOTAL_CODE_CHARS:
                truncated_files.append(rel)
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + "\n...[truncated]..."
            file_contents[rel] = content
            total_chars += len(content)

    return {
        "all_paths": sorted(all_paths),
        "file_contents": file_contents,
        "truncated_files": truncated_files,
    }


def _sprint_metrics_table(manifest: dict) -> str:
    """Stories Planned counts items newly added to sprint_backlog THIS
    sprint (diffed against the previous sprint's sprint_backlog keys, same
    approach as _kpi_time_series - see that function's own docstring for
    why a raw len() here is wrong). sprint_backlog is never reset between
    sprints, so a raw len() would just report the run's whole accumulated
    scope so far, flat and identical, on every single row once initial
    planning committed it all - exactly what a real run (0.1.0-run42)
    showed: "6" on every one of its 5 sprint rows."""
    rows = ["| Sprint | Tokens Used | Stories Planned | Sprint Report? | PR Merges (after) |",
            "|---|---|---|---|---|"]
    prev_backlog_keys = set()
    for sprint in manifest.get("sprints", []):
        n = sprint.get("sprint_number")
        tokens = (sprint.get("token_usage") or {}).get("total", "n/a")
        backlog = sprint.get("sprint_backlog") or []
        backlog_keys = {_backlog_item_key(item, i) for i, item in enumerate(backlog)}
        planned = len(backlog_keys - prev_backlog_keys)
        prev_backlog_keys = backlog_keys
        has_report = "yes" if sprint.get("sprint_report") else "no"
        merges = [m for m in manifest.get("pr_merges", []) if m.get("after_sprint") == n]
        merged_count = sum(1 for m in merges if m.get("merged"))
        rows.append(f"| {n} | {tokens} | {planned} | {has_report} | {merged_count}/{len(merges)} |")
    return "\n".join(rows)


# GH issue #124: which KPI each series is sourced from. The first four are
# derived straight from each sprint's own backlog snapshot (sprint_backlog),
# always present. The last three come from QualityGuardian's calculate_kpis/
# update_sprint_report (see its prompt's "YOU DO" and tools/quality.py) via
# sprint_report_kpis, which only exists for a sprint if QualityGuardian
# actually got to run - a sprint that ran out of budget/events first simply
# has no data point for these, reported honestly as missing rather than a
# fabricated 0 or a repeat of the last known value.
_ACCEPTED_STAGE = "Accepted"
_IMPLEMENTED_STAGE = "Implemented"


def _backlog_item_key(item: dict, index: int):
    """id-else-title fallback (same pattern as budget.py's blocked-stories
    collection), further falling back to `index` (this item's position in
    its sprint_backlog list) so two distinct items that both lack an id
    AND a title don't collapse into the same dedup key and undercount -
    real backlog items always get an id via upsert_backlog_item, but
    nothing here should silently miscount if a caller's data doesn't."""
    return item.get("id") or item.get("title") or f"__index_{index}"


def _kpi_time_series(manifest: dict) -> dict:
    """Returns {kpi_name: [(sprint_number, value), ...]} - one list per KPI,
    containing only the sprints that actually have a value for it.

    sprint_backlog is NOT reset between sprints (it's the run's whole
    selected scope, accumulating stage progress sprint over sprint) - so a
    raw per-sprint count of "items with stage X completed" is a running
    total across the whole run, not that sprint's own throughput. A real
    run (0.1.0-run42) showed exactly this: Velocity/Stories implemented
    read 1, 3, 5, 6, 6 - monotonically non-decreasing, because sprint 3's
    "5" still counted sprint 1 and 2's already-accepted items too. Diffing
    each sprint's set of items-at-stage-X against the previous sprint's set
    (by item key, not just the total count) gives the actual per-sprint
    delta - how many items newly reached that stage THIS sprint - which is
    what "velocity" means everywhere else this term is used."""
    series = {
        "Velocity (items accepted)": [],
        "Issues fixed": [],
        "Stories implemented": [],
        "Testplan scenarios": [],
        "Say-Do Ratio": [],
        "Quality (defect escape rate)": [],
        "Test Coverage": [],
    }
    prev_accepted_keys = set()
    prev_issue_accepted_keys = set()
    prev_implemented_keys = set()
    for sprint in manifest.get("sprints", []):
        n = sprint.get("sprint_number")
        backlog = sprint.get("sprint_backlog") or []

        accepted_keys = {
            _backlog_item_key(item, i) for i, item in enumerate(backlog)
            if _ACCEPTED_STAGE in (item.get("stages_completed") or [])
        }
        issue_accepted_keys = {
            _backlog_item_key(item, i) for i, item in enumerate(backlog)
            if item.get("type") == "Issue" and _ACCEPTED_STAGE in (item.get("stages_completed") or [])
        }
        implemented_keys = {
            _backlog_item_key(item, i) for i, item in enumerate(backlog)
            if item.get("type") != "Issue" and _IMPLEMENTED_STAGE in (item.get("stages_completed") or [])
        }
        scenarios = sum(len(item.get("acceptance_criteria") or []) for item in backlog)

        series["Velocity (items accepted)"].append((n, len(accepted_keys - prev_accepted_keys)))
        series["Issues fixed"].append((n, len(issue_accepted_keys - prev_issue_accepted_keys)))
        series["Stories implemented"].append((n, len(implemented_keys - prev_implemented_keys)))
        series["Testplan scenarios"].append((n, scenarios))

        prev_accepted_keys = accepted_keys
        prev_issue_accepted_keys = issue_accepted_keys
        prev_implemented_keys = implemented_keys

        kpis = sprint.get("sprint_report_kpis") or {}
        say_do = (kpis.get("team_effectiveness") or {}).get("say_do_ratio")
        if say_do is not None:
            series["Say-Do Ratio"].append((n, say_do))
        defect_escape_rate = (kpis.get("result_quality") or {}).get("defect_escape_rate")
        if defect_escape_rate is not None:
            series["Quality (defect escape rate)"].append((n, defect_escape_rate))
        coverage = (kpis.get("maintainability") or {}).get("test_coverage")
        if coverage is not None:
            series["Test Coverage"].append((n, coverage))
    return series


def _format_kpi_value(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


# GH issue #347: which (parent dict, note key) inside a sprint's
# sprint_report_kpis payload explains an empty series for each of the 3 KPIs
# sourced from calculate_kpis (tools/quality.py) - used only to distinguish
# "calculate_kpis never ran this run at all" from "it ran every sprint, but
# this specific sub-metric has no underlying data source yet", which
# calculate_kpis already reports honestly via its own *_note field in that
# same payload. Confusing the two misrepresented a real run (0.1.0-run43):
# the report claimed QualityGuardian "was not called in any sprint" for
# defect escape rate, while the same run's Say-Do Ratio - sourced from the
# exact same calculate_kpis calls - had real values for 4 of 5 sprints.
_KPI_NOTE_LOCATIONS = {
    "Say-Do Ratio": ("team_effectiveness", "say_do_ratio_note"),
    "Quality (defect escape rate)": ("result_quality", "defect_escape_rate_note"),
    "Test Coverage": ("maintainability", "test_coverage_note"),
}


def _kpi_unavailable_message(manifest: dict, name: str) -> str:
    """Why `name`'s series ended up empty in _kpi_time_series - see
    _KPI_NOTE_LOCATIONS' own comment for the exact distinction this draws."""
    location = _KPI_NOTE_LOCATIONS.get(name)
    if location:
        parent_key, note_key = location
        for sprint in manifest.get("sprints", []):
            kpis = sprint.get("sprint_report_kpis") or {}
            if not kpis:
                continue
            note = (kpis.get(parent_key) or {}).get(note_key)
            if note:
                return (
                    f"No data available for this run - {note} (QualityGuardian's "
                    "calculate_kpis did run this run, this specific metric just has no "
                    "data source yet)."
                )
    return (
        "No data available for this run - never computed (QualityGuardian's "
        "calculate_kpis/update_sprint_report was not called in any sprint)."
    )


def _render_kpi_graphs(manifest: dict) -> str:
    """Renders one mermaid xychart-beta line graph per KPI (GitHub renders
    mermaid natively in Markdown) plus a combined table, so the trend is
    visible even where mermaid rendering isn't available (e.g. a plain-text
    reading of the CI artifact)."""
    total_sprints = len(manifest.get("sprints", []))
    series = _kpi_time_series(manifest)
    lines = ["## KPI Trends", ""]

    if total_sprints < 2:
        lines.append(
            "Fewer than 2 completed sprints this run - not enough data points for a "
            "meaningful trend line.\n"
        )
        return "\n".join(lines)

    table_rows = ["| KPI | " + " | ".join(f"Sprint {s['sprint_number']}" for s in manifest.get("sprints", [])) + " |",
                  "|---|" + "---|" * total_sprints]
    for name, points in series.items():
        by_sprint = dict(points)
        table_rows.append(
            "| " + name + " | " +
            " | ".join(
                _format_kpi_value(by_sprint[s["sprint_number"]]) if s["sprint_number"] in by_sprint else "n/a"
                for s in manifest.get("sprints", [])
            ) + " |"
        )
    lines += table_rows + [""]

    for name, points in series.items():
        if not points:
            lines += [
                f"### {name}",
                "",
                _kpi_unavailable_message(manifest, name),
                "",
            ]
            continue
        lines += [
            f"### {name}",
            "",
            "```mermaid",
            "xychart-beta",
            f'    title "{name}"',
            "    x-axis [" + ", ".join(f"\"Sprint {n}\"" for n, _ in points) + "]",
            f'    y-axis "{name}"',
            "    line [" + ", ".join(_format_kpi_value(v) for _, v in points) + "]",
            "```",
            "",
        ]
        missing = total_sprints - len(points)
        if missing:
            lines.append(
                f"({missing} of {total_sprints} sprints have no data point for this KPI "
                "and are omitted above - see the table for exactly which.)\n"
            )

    return "\n".join(lines)


def _build_judge_prompt(manifest: dict, snapshot: dict) -> str:
    sprint_reports = "\n\n".join(
        f"### Sprint {s['sprint_number']} report\n{s.get('sprint_report') or '(none produced)'}"
        for s in manifest.get("sprints", [])
    )
    code_section = "\n\n".join(
        f"--- {path} ---\n{content}" for path, content in snapshot["file_contents"].items()
    )
    truncation_note = (
        f"\n\n(Note: {len(snapshot['truncated_files'])} additional files were not "
        f"included due to size limits: {snapshot['truncated_files']})"
        if snapshot["truncated_files"] else ""
    )

    return f"""
You are evaluating the output of an AI Scrum team's fixed-length run against a
known, fixed product vision (a simple to-do list web app). Be a skeptical,
concrete reviewer - cite actual file paths and actual text, don't generalize.

## Full file tree produced
{json.dumps(snapshot['all_paths'], indent=2)}

## Code and doc file contents (capped for size){truncation_note}
{code_section}

## Sprint reports (team's own account of what happened)
{sprint_reports}

## Per-sprint metrics
{_sprint_metrics_table(manifest)}

## Your task
Respond with ONLY a JSON object (no markdown fences, no prose outside the
JSON), matching exactly this shape:

{{
  "code_quality": {{"score": <1-5 int>, "summary": "<2-4 sentences>"}},
  "requirements_quality": {{"score": <1-5 int>, "summary": "<2-4 sentences>"}},
  "team_efficiency": {{"score": <1-5 int>, "summary": "<2-4 sentences>"}},
  "top_problems": [
    {{"problem": "<one sentence>", "evidence": "<specific file/quote>", "severity": "high|medium|low", "suggested_fix": "<concrete, actionable, or 'no clear fix - <why>' if none exists>"}}
  ]
}}

Rules:
- score 1 = did not deliver anything close to the vision; 5 = a person could
  actually use this end to end as described in the vision's "what done looks
  like" section.
- top_problems: 3-8 entries, ranked most severe first. Every entry must cite
  real evidence from what's shown above - do not invent a problem that isn't
  actually visible in the material given.
- If something genuinely can't be fixed without changing the evaluation
  methodology itself (e.g. "5 sprints is too few for X"), say so honestly in
  suggested_fix rather than forcing a fake code-level fix.
""".strip()


def _call_judge(prompt: str, model: str) -> dict:
    import litellm

    litellm.api_base = os.environ.get("LITELLM_PROXY_API_BASE")
    litellm.api_key = os.environ.get("LITELLM_PROXY_API_KEY") or os.environ.get("LITELLM_MASTER_KEY")

    response = litellm.completion(
        model=f"litellm_proxy/{model}",
        messages=[{"role": "user", "content": prompt}],
        api_base=litellm.api_base,
        api_key=litellm.api_key,
    )
    raw_text = response["choices"][0]["message"]["content"].strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        return {"error": f"Judge response was not valid JSON: {e}", "raw_response": raw_text}


_STOP_REASON_EXPLANATIONS = {
    "max_duration_exceeded": "the run's own wall-clock safety net (--max-duration-minutes) was reached",
    "budget_critical_halt": "a sprint hit a critical token/USD budget halt with no clean close-out afterward",
    "crashed": "the run crashed with an unhandled exception",
    "blocked_needs_human": "a BLOCKED story's category escalates straight to a human this scripted driver has no way to provide",
    "blocked_unresolved_across_sprint": "a BLOCKED story was still unresolved after a full sprint's own budget to fix it",
    "human_blocker_unresolved": "a high-priority 'human'-category retro finding is unresolved and this scripted driver has no human to act on it",
}


def _render_stopped_early_callout(manifest: dict) -> list:
    """
    GH issue #336 (stop-on-blocker) exists precisely so a cancelled run
    stops for a REASON, not just silently - but a real user still had to
    dig through raw GitHub Actions logs to find out why a run only
    completed 1 of 5 requested sprints (stop_reason was buried in
    manifest.json, never surfaced in the human-facing report at all).
    Rendered as the very first thing after the run header, before anything
    else - this is the single most important fact about a cancelled run.
    """
    if not manifest.get("stopped_early"):
        return []
    stop_reason = manifest.get("stop_reason", "unknown")
    explanation = _STOP_REASON_EXPLANATIONS.get(stop_reason, "see stop_reason below for the raw code")
    lines = [
        "## ⚠️ Evaluation Stopped Early",
        "",
        f"Only {len(manifest.get('sprints', []))} of {manifest.get('sprints_requested')} requested sprints "
        f"completed - **{explanation}**.",
        "",
        f"- Stop reason: `{stop_reason}`",
    ]
    if manifest.get("crash_sprint") is not None:
        lines.append(f"- Crashed during sprint: {manifest['crash_sprint']}")
    if manifest.get("crash_error"):
        lines.append(f"- Crash error: `{manifest['crash_error']}`")
    blocked_story = manifest.get("blocked_story")
    if blocked_story:
        blocked = blocked_story.get("blocked", {}) or {}
        lines.append(
            f"- Blocked story: **{blocked_story.get('id')}** - {blocked.get('category', 'unknown')}: "
            f"{blocked.get('question', '(no question recorded)')} "
            f"(raised by {blocked.get('raised_by', 'unknown')})"
        )
    general_blocker = manifest.get("general_blocker")
    if general_blocker:
        lines.append(
            f"- Human-only retro finding: {general_blocker.get('description', '(no description recorded)')} "
            f"(raised by {general_blocker.get('raised_by', 'unknown')})"
        )
    lines.append("")
    return lines


def _collect_blockers(manifest: dict) -> list:
    """
    Every story BLOCKED (raise_story_blocker, agents/scrum_team/tools/
    requirements.py) as of the LAST completed sprint's final state -
    product_backlog is the full, cumulative backlog each sprint, so the
    most recent one is the authoritative "still blocked as of run end"
    view (the same semantics create_sprint_report's own "Open Questions
    for Stakeholder (Blockers)" section already uses for a single sprint -
    this is that same view, for whichever sprint the run actually ended on).
    Returns a list of (story_id, title, blocked_dict) tuples.
    """
    sprints = manifest.get("sprints", [])
    if not sprints:
        return []
    last_sprint = sprints[-1]
    blockers = []
    seen_ids = set()
    for collection_name in ("product_backlog", "sprint_backlog"):
        for item in last_sprint.get(collection_name) or []:
            blocked = item.get("blocked")
            if not blocked:
                continue
            story_id = item.get("id") or item.get("title")
            if story_id in seen_ids:
                continue
            seen_ids.add(story_id)
            blockers.append((story_id, item.get("title", story_id), blocked))
    return blockers


def _collect_general_blockers(manifest: dict) -> list:
    """
    GH issue #342: unresolved "human"-category retro/impediment findings
    (add_retro_action/add_impediment's category param) as of the LAST
    completed sprint - not tied to any one story, unlike _collect_blockers
    above, so a general finding (e.g. "we need a decision on which auth
    provider to standardize on") still surfaces here even when nothing in
    product_backlog/sprint_backlog itself is BLOCKED. Returns the list of
    general_blockers dicts that are still unresolved.
    """
    sprints = manifest.get("sprints", [])
    if not sprints:
        return []
    return [e for e in (sprints[-1].get("general_blockers") or []) if not e.get("resolved")]


def _render_blockers_section(manifest: dict) -> list:
    blockers = _collect_blockers(manifest)
    general_blockers = _collect_general_blockers(manifest)
    lines = ["## Blockers", ""]
    if not blockers and not general_blockers:
        lines.append("No stories are blocked as of the last completed sprint.")
        lines.append("")
        return lines
    for story_id, title, blocked in blockers:
        lines.append(
            f"- **{story_id}** ({title}) - {blocked.get('category', 'unknown')}: "
            f"{blocked.get('question', '(no question recorded)')} "
            f"(raised by {blocked.get('raised_by', 'unknown')})"
        )
    for entry in general_blockers:
        lines.append(
            f"- **(not tied to a specific story)** - human, {entry.get('priority', 'normal')} priority: "
            f"{entry.get('description', '(no description recorded)')} "
            f"(raised by {entry.get('raised_by', 'unknown')})"
        )
    lines.append("")
    return lines


def _render_report(manifest: dict, judgment: dict) -> str:
    lines = [
        "# Team Performance Evaluation Report",
        "",
        f"- Run ID: {manifest.get('run_id')}",
        f"- Horseless Carriage commit: {manifest.get('hc_commit', 'unknown')}",
        f"- Branch: {manifest.get('branch')}",
        f"- Model: {manifest.get('model')}",
        f"- Sprints requested: {manifest.get('sprints_requested')}, completed: {len(manifest.get('sprints', []))}",
        f"- Started: {manifest.get('started_at')}",
        f"- Finished: {manifest.get('finished_at')}",
        "",
    ]
    lines += _render_stopped_early_callout(manifest)
    lines += _render_blockers_section(manifest)
    lines += [
        "## Methodology note",
        "This run used a scripted, unattended driver (`run_eval.py`) that pre-approves "
        "every sprint goal/backlog and auto-merges any PR that opens against the eval "
        "branch, standing in for the human review gate real usage requires. Results "
        "reflect the team's real behavior under those conditions, not under full "
        "human-in-the-loop review - a lower bar in that one specific respect.",
        "",
        "## Per-sprint metrics",
        _sprint_metrics_table(manifest),
        "",
        _render_kpi_graphs(manifest),
    ]

    total_tokens = _total_tokens_used(manifest)
    cost_range = _estimate_cost_range(total_tokens, manifest.get("model"))
    lines += ["## Token & Cost Summary", "", f"- Total tokens used: {total_tokens:,}"]
    if cost_range:
        low, high = cost_range
        lines.append(
            f"- Expected cost: ${low:.4f} - ${high:.4f} (rough estimate from litellm's static "
            f"pricing for `{MODEL_PRICING_LOOKUP.get(manifest.get('model'))}` - token_usage doesn't "
            "split prompt/completion tokens, so this is an all-input-vs-all-output bound, not an exact figure)"
        )
    else:
        lines.append(f"- Expected cost: n/a (no pricing lookup configured for model `{manifest.get('model')}`)")
    lines.append("")

    if "error" in judgment:
        lines += ["## Judge error", "", judgment["error"], "", "Raw response:", "```", judgment.get("raw_response", ""), "```"]
        return "\n".join(lines)

    for key, title in [("code_quality", "Code Quality"), ("requirements_quality", "Requirements Quality"), ("team_efficiency", "Team Efficiency")]:
        section = judgment.get(key, {})
        lines += [f"## {title}", "", f"**Score: {section.get('score', 'n/a')}/5**", "", section.get("summary", "(no assessment)"), ""]

    lines += ["## Top Problems", ""]
    for i, problem in enumerate(judgment.get("top_problems", []), start=1):
        lines += [
            f"{i}. **{problem.get('problem', '(unnamed)')}** (severity: {problem.get('severity', 'unknown')})",
            f"   - Evidence: {problem.get('evidence', 'n/a')}",
            f"   - Suggested fix: {problem.get('suggested_fix', 'n/a')}",
            "",
        ]

    return "\n".join(lines)


def _open_and_merge_report_pr(report: str, repo_path: Path, base_branch: str, run_id: str) -> dict:
    """
    Opens the report as its own small PR against base_branch, rather than
    pushing straight to it - so the report goes through the same PR
    mechanism as every other change instead of bypassing it, and shows up
    as the run's own final PR alongside the team's. Branch/title are
    tagged with run_id the same way agents/scrum_team/tools/base.py's
    _with_eval_branch_prefix/_with_eval_title_prefix tag agent-created
    branches/PRs, so it's obvious which run this belongs to.

    Merges the PR itself immediately after opening it: unlike the team's
    PRs (where run_eval.py's _merge_open_prs stands in for the "Human
    Review is mandatory" gate during the run), this one is the harness's
    own concluding action, produced after that run has already finished -
    there is no further review step left to stand in for.
    """
    import subprocess
    from agents.scrum_team.scripts._eval_git_utils import get_github_token, run_git

    github_token = get_github_token()
    report_branch = f"eval-{run_id}/eval-report"

    checkout = run_git(["checkout", "-B", report_branch], cwd=repo_path, github_token=github_token)
    (repo_path / "EVAL-REPORT.md").write_text(report, encoding="utf-8")
    run_git(["add", "EVAL-REPORT.md"], cwd=repo_path, github_token=github_token)
    commit = run_git(["commit", "-m", f"chore: add evaluation report ({run_id})"], cwd=repo_path, github_token=github_token)
    push = run_git(["push", "-u", "origin", report_branch], cwd=repo_path, github_token=github_token)
    result = {"checkout": checkout, "commit": commit, "push": push, "pr": None, "merge": None}
    if push.get("status") != "ok":
        return result

    pr = subprocess.run(
        [
            "gh", "pr", "create", "--base", base_branch, "--head", report_branch,
            "--title", f"[eval-{run_id}] Evaluation report",
            "--body", "Auto-generated evaluation report for this run - see EVAL-REPORT.md.",
        ],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    result["pr"] = {"returncode": pr.returncode, "stdout": pr.stdout.strip(), "stderr": pr.stderr.strip()}
    if pr.returncode != 0:
        return result

    pr_number = result["pr"]["stdout"].rsplit("/", 1)[-1]
    merge = subprocess.run(
        ["gh", "pr", "merge", pr_number, "--merge", "--admin"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    result["merge"] = {"returncode": merge.returncode, "stdout": merge.stdout.strip(), "stderr": merge.stderr.strip()}
    return result


def _open_overview_pr(repo_path: Path, base_branch: str, run_id: str, manifest: dict) -> dict:
    """
    Opens (but never merges) a PR from the whole eval run branch
    (base_branch - by now containing every sprint's merged work plus the
    eval-report commit from _open_and_merge_report_pr) against the eval
    repo's actual default branch, as a single human-reviewable overview of
    everything the run produced. Distinct from _open_and_merge_report_pr's
    PR, which only adds EVAL-REPORT.md to base_branch itself and is
    immediately merged - this one is the run's full diff and is
    deliberately left open, since merging an eval run into the eval repo's
    real main would defeat the point of keeping eval runs isolated (see
    module docstring / RELEASE.md "Team performance evaluation").
    """
    import subprocess

    repo_info = subprocess.run(
        ["gh", "repo", "view", "--json", "defaultBranchRef,nameWithOwner"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    if repo_info.returncode != 0:
        return {"status": "error", "message": f"gh repo view failed: {repo_info.stderr.strip()}"}
    info = json.loads(repo_info.stdout)
    default_branch = info["defaultBranchRef"]["name"]
    repo_slug = info["nameWithOwner"]

    if default_branch == base_branch:
        return {"status": "skipped", "message": "base_branch is already the repo's default branch"}

    sprint_pr_links = "\n".join(
        f"- Sprint {m['after_sprint']}: #{m['number']} ({'merged' if m.get('merged') else 'not merged'}) - "
        "carries that sprint's raw agent activity log as a PR comment"
        for m in manifest.get("pr_merges", [])
        if "number" in m
    ) or "(no sprint PRs were opened during this run)"

    body = (
        f"Full diff produced by eval run `{run_id}` against `{default_branch}` - opened for human review only.\n\n"
        "**DO NOT MERGE** - eval runs are deliberately isolated from the eval repo's real "
        f"`{default_branch}`; this PR exists purely as a single place to review the run's "
        "total output (see EVAL-REPORT.md on this branch for the judged report).\n\n"
        f"### Sprint PRs (merged into `{base_branch}` during the run)\n{sprint_pr_links}\n"
    )

    pr = subprocess.run(
        [
            "gh", "pr", "create", "--base", default_branch, "--head", base_branch,
            "--title", f"[eval-{run_id}] Evaluation run overview - DO NOT MERGE",
            "--body", body,
        ],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    return {
        "status": "ok" if pr.returncode == 0 else "error",
        "returncode": pr.returncode,
        "stdout": pr.stdout.strip(),
        "stderr": pr.stderr.strip(),
        "repo": repo_slug,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--run-id", default=None, help="Required together with --base-branch, to tag/name the report PR")
    parser.add_argument("--base-branch", default=None, help="If set (with --run-id), open+merge a PR adding the report against this branch in --repo-path")
    args = parser.parse_args()
    if bool(args.base_branch) != bool(args.run_id):
        parser.error("--base-branch and --run-id must be given together")

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    snapshot = _collect_repo_snapshot(Path(args.repo_path))
    prompt = _build_judge_prompt(manifest, snapshot)
    judgment = _call_judge(prompt, args.judge_model)
    report = _render_report(manifest, judgment)

    Path(args.report_path).write_text(report, encoding="utf-8")
    print(f"Report written to {args.report_path}")

    if args.base_branch:
        result = _open_and_merge_report_pr(report, Path(args.repo_path), args.base_branch, args.run_id)
        merged = bool(result["merge"]) and result["merge"].get("returncode") == 0
        print(f"Report PR against {args.base_branch}: {'merged' if merged else 'FAILED'} - {json.dumps(result)}")

        overview_result = _open_overview_pr(Path(args.repo_path), args.base_branch, args.run_id, manifest)
        print(f"Overview PR (not merged, for human review): {overview_result.get('status')} - {json.dumps(overview_result)}")


if __name__ == "__main__":
    main()
