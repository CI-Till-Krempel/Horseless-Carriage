[← Back to README](../README.md)

# Budget Management

The system implements a **dual-layer budgeting strategy** to ensure both operational safety and financial control. This approach leverages LiteLLM's native financial enforcement while providing local, high-fidelity control over the logical "Sprint Budget" in tokens.

## 1. Token Budget (ADK Layer)
- **Unit**: Total tokens (e.g., 1,000,000).
- **Enforcement**: Hard-blocked locally, purely from session state/`SPRINT_TOKEN_BUDGET` — no
  call to LiteLLM is involved, so this guardrail applies **even if the LiteLLM proxy isn't
  running**. See step 1 of `check_cost_budget_callback` in `agents/scrum_team/agent.py`
  (usage is recorded by the separate `update_token_usage_callback`).
- **Automatic Tracking**: The system automatically tracks token usage after every LLM call and attributes it to the specific agent role.
- **Purpose**: Prevents long-running loops or runaway agent conversations. LiteLLM natively supports rate limits (tokens per minute) but does not provide a hard-stop for a *total cumulative token quota* across an entire sprint. Local enforcement provides immediate, zero-latency feedback and allows for a pure "logical" work limit.

## 2. USD Budget (LiteLLM Layer)
- **Unit**: US Dollars (e.g., $0.50).
- **Enforcement**: Hard-blocked by the LiteLLM Proxy, plus a real-time pre-call check
  against current spend on the shared `scrum-sprint-budget` object.
- **Purpose**: Provides financial guardrails and visibility in the LiteLLM Admin UI via
  the `scrum-sprint-budget` object. LiteLLM is the authority on costs and
  provider-level pricing. By setting a `max_budget` on the `scrum-sprint-budget`
  object, we ensure that the team never exceeds a hard financial limit, regardless of
  the token count.
- **Requires the LiteLLM proxy to actually be running.** Step 2 of
  `check_cost_budget_callback` only runs this check `if master_key and proxy_base` (both
  `LITELLM_MASTER_KEY` and `LITELLM_PROXY_API_BASE` set) — if either is unset, the USD
  check is **skipped outright** (not failed closed), and only the token budget above still
  applies. If the proxy *is* configured but unreachable (e.g. the container isn't up), the
  check does fail closed with a `[BUDGET ERROR]` instead. In short: no USD guardrail at all
  without proxy config; a hard stop instead of silent bypass if it's configured but down.
  `agents/scrum_team/scripts/run_eval.py` checks proxy reachability itself before a local
  (non-CI) run and refuses to proceed without an explicit `--dev-mode` flag — see
  [Evaluation](EVALUATION.md).
- **No unscoped fallback spend**: every specialist agent's calls are blocked in code
  until it has its own `scrum-sprint-budget`-attached virtual key —
  `create_litellm_virtual_key()` must run for it first. Without this, a missing key
  would silently fall back to `LITELLM_PROXY_API_KEY`, which isn't attached to
  `scrum-sprint-budget` and so wouldn't be covered by the check above at all (this
  matters in particular right after a
  [LiteLLM database wipe recovery](GITHUB-INTEGRATION.md#recovering-from-a-litellm-database-wipe),
  where that fallback key is briefly pointed at the unbounded master key). The
  Orchestrator itself is exempt from this specific check, since it needs one
  bootstrap call to create everyone else's key in the first place — see
  `check_cost_budget_callback` in `agents/scrum_team/agent.py`.
- **Not meaningful for a local/Ollama setup**: self-hosted models have no real per-token
  price, so LiteLLM's cost map has no pricing entry for them and `spend` on
  `scrum-sprint-budget` stays at (or effectively) $0.00 regardless of actual usage — the
  USD check would otherwise pass trivially forever, giving false confidence that a budget
  is actually being enforced. `docker-compose.local.yaml` sets `LLM_LOCAL_PROVIDER=true` on
  the `agent` service specifically so `check_cost_budget_callback` can detect this and skip
  the USD check outright (rather than run a check that can never meaningfully fail). **The
  token budget above is the only guardrail that applies to a local/Ollama sprint** — set
  `SPRINT_TOKEN_BUDGET` accordingly.
- **Tools**: `update_budgets(total_usd=0.50)`, `create_litellm_virtual_key()`.

## Monitoring & Reporting

### Quality KPIs
The system tracks performance indicators to provide visibility into team health:
- **Say-Do Ratio**: Compares planned vs. completed stories. A ratio of 1.0 means the team delivered exactly what was promised.
- **Commitment Reliability**: Measures the accuracy of the team's estimates and delivery capability.
- **Defect Escape Rate**: Percentage of defects found after a story is marked as "Done".
- **Code Complexity**: A maintainability metric to ensure long-term velocity.
- **Test Coverage**: The percentage of the codebase exercised by automated tests.
- **Vulnerability Scan Results**: Tracks critical, high, medium, and low security findings.

### Sprint Report
At the end of each sprint, the Product Owner generates a report via `create_sprint_report`, which
includes a detailed breakdown of token usage per agent, total USD spend, and quality metrics.
Every sprint's report is kept — written to a sequentially numbered
`specs/reports/SPRINT-REPORT-NNN.md` (`001`, `002`, ...; the number is derived by scanning what's
already there, the same way story/ADR IDs are generated, so there's no separate counter to drift
out of sync) — and `specs/reports/SPRINT-REPORT-LATEST.md` is also kept up to date as a convenience
pointer to the most recent one. Earlier versions of this only ever kept `SPRINT-REPORT-LATEST.md`,
so every sprint but the last got silently overwritten.

Example report content:
```markdown
# Sprint Review Report

## Summary
Completed the core implementation of the GitHub integration and established the CI pipeline.

## Accomplishments
- Implemented `gh_pr_comment` and `gh_pr_review` tools.
- Set up Docker-based test runner.
- Integrated Quality KPI calculations into the workflow.

## Budget and Usage
- USD Budget (LiteLLM): $0.50
- Process Overhead: 15%

### Per-Agent Token Usage
  - ProductOwner: 45,200
  - DevTeam: 120,500
  - ScrumMaster: 12,300

## Sprint Length Feedback
- Tokens used: 950,000 / 1,000,000 (95%)
- Stories: 3/6 completed this sprint
- This sprint used 95% of its token budget and left 3/6 stories unfinished - the per-sprint token
  budget looks too small for the amount of work planned, not necessarily a quality problem.
- **Suggested new per-sprint token budget: ~3,800,000 tokens** (extrapolated from ~316,667
  tokens/completed story x 6 planned stories, +20% headroom).
- **This is a recommendation only - it is NOT applied automatically.** A human must approve it and
  set it manually (`SPRINT_TOKEN_BUDGET` / `EVAL_SPRINT_TOKEN_BUDGET`; see "Budget Management" above).

## Retrospective Actions (including efficiency improvements)
- Tag Architect on any story touching the data model before marking it Ready (Owner: ProductOwner, Status: open)

## Impediments
No impediments logged.

## Story Estimates vs Actual Tokens
- US-0012: estimate=50000, actual=62345

## Quality Dashboard
- Say-Do Ratio: 0.9
- Test Coverage: 85%
- Defect Escape Rate: 2%
```

The "Sprint Length Feedback" section is advisory only - see `_sprint_length_feedback` in
`agents/scrum_team/tools/budget.py`. It only appears with a budget-increase suggestion when the
sprint actually looks budget-starved (near/at its token cap **and** stories left unfinished); if
there's unused budget headroom left over, it says so instead and points at process/quality issues
rather than the budget. Nothing here ever changes `SPRINT_TOKEN_BUDGET`/`budgets.total` itself - a
human has to act on the suggestion deliberately.

Unlike every other section, "Retrospective Actions"/"Impediments" aren't just rendered - the whole
report generation is gated on them. `create_sprint_report` refuses to run at all unless a *new*
retro action or impediment has been logged since the last successful report (see RELEASE.md "Sprint
retrospective enforcement"), so if you see a report at all, at least one of these two sections is
guaranteed to have real, new content - never both saying "none" at once.

### Admin UI
Log in to `http://localhost:4000/ui/` to see real-time cost tracking and budget status for the `scrum-sprint-budget`.
