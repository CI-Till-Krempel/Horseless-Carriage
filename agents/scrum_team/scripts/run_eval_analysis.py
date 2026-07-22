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
        --run-id <id> --base-branch eval/<id>
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
    token_usage.total is cumulative for the whole run, never resets between
    sprints (see check_cost_budget_callback) - so the run's total is the
    last sprint's figure, NOT the sum of every sprint row (that would
    double/triple-count earlier sprints' tokens).
    """
    totals = [(s.get("token_usage") or {}).get("total", 0) for s in manifest.get("sprints", [])]
    return max(totals, default=0)


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
    rows = ["| Sprint | Tokens Used | Stories Planned | Sprint Report? | PR Merges (after) |",
            "|---|---|---|---|---|"]
    for sprint in manifest.get("sprints", []):
        n = sprint.get("sprint_number")
        tokens = (sprint.get("token_usage") or {}).get("total", "n/a")
        planned = len(sprint.get("sprint_backlog") or [])
        has_report = "yes" if sprint.get("sprint_report") else "no"
        merges = [m for m in manifest.get("pr_merges", []) if m.get("after_sprint") == n]
        merged_count = sum(1 for m in merges if m.get("merged"))
        rows.append(f"| {n} | {tokens} | {planned} | {has_report} | {merged_count}/{len(merges)} |")
    return "\n".join(rows)


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


def _render_report(manifest: dict, judgment: dict) -> str:
    lines = [
        "# Team Performance Evaluation Report",
        "",
        f"- Run ID: {manifest.get('run_id')}",
        f"- Branch: {manifest.get('branch')}",
        f"- Model: {manifest.get('model')}",
        f"- Sprints requested: {manifest.get('sprints_requested')}, completed: {len(manifest.get('sprints', []))}",
        f"- Started: {manifest.get('started_at')}",
        f"- Finished: {manifest.get('finished_at')}",
        "",
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
