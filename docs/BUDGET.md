[← Back to README](../README.md)

# Budget Management

The system implements a **dual-layer budgeting strategy** to ensure both operational safety and financial control. This approach leverages LiteLLM's native financial enforcement while providing local, high-fidelity control over the logical "Sprint Budget" in tokens.

**Naming**: these two limits have different *scopes*, and their env var names say so explicitly -
`SPRINT_TOKEN_BUDGET` is **per-sprint** (resets automatically every sprint), `TOTAL_USD_BUDGET` is a
**whole-engagement** ceiling (never resets on its own). The older name `SPRINT_USD_BUDGET` is still
honored as a deprecated fallback (`get_env_with_deprecated_fallback` in `agents/scrum_team/helpers.py`)
if you haven't renamed it in your own `.env` yet.

## 1. Token Budget (ADK Layer)
- **Unit**: Total tokens (e.g., 5,000,000 - the default as of GH issue #220; a real "product mode"
  test-drive showed planning alone, before Dev/QA ever ran, can use just over 1,000,000, the
  previous default).
- **Scope**: **Per sprint.** Resets automatically whenever `start_sprint` actually starts a new one
  (mechanical, GH #413 - no separate tool call or harness-side reset involved) - a sprint that used
  most of its budget doesn't starve every later sprint.
- **Enforcement**: Hard-blocked locally, purely from session state/`SPRINT_TOKEN_BUDGET` — no
  call to LiteLLM is involved, so this guardrail applies **even if the LiteLLM proxy isn't
  running**. See step 1 of `check_cost_budget_callback` in `agents/scrum_team/agent.py`
  (usage is recorded by the separate `update_token_usage_callback`).
- **Advance warning (GH issue #220)**: a one-time system-context message is injected once usage
  crosses 75%, and again at 90%, of `SPRINT_TOKEN_BUDGET` - see `_maybe_inject_budget_warning` in
  `agents/scrum_team/agent.py`. Previously the hard-halt itself was the only signal anyone got.
- **Reserved floor for DevTeam/QA/Architect (GH issue #220)**: a verbose planning phase could
  previously burn the entire sprint budget before Dev/QA/Architect ever got a single turn. Each of
  those three roles is now guaranteed at least one real call this sprint even if the total is
  already exhausted when it's first invoked - a **one-time** floor per role
  (`NON_GRACE_FLOOR_ROLES`, `agents/scrum_team/helpers.py`), not a standing exemption: the instant
  a role logs any real usage, it hard-halts on the next exhausted call exactly as before.
- **Scaled close-out grace (GH issue #220)**: `SPRINT_CLOSEOUT_GRACE_PERCENT`'s ceiling
  (`closeout_grace_percent`, `agents/scrum_team/helpers.py`) now scales down as less of the SPRINT
  CLOSE SEQUENCE remains outstanding, instead of always granting the full configured percentage
  regardless of how much work is actually left. GH #395: this grace now only covers Product Owner's
  `create_release_pr` call (and ScrumOrchestrator's routing) after the main budget trips - it no
  longer applies to Scrum Master, which has its own separate ritual budget instead (see below).
- **Scrum Master's separate ritual budget (GH #395)**: facilitating events, the retrospective, KPI
  calculation, and authoring the sprint report are process overhead, not feature/implementation
  work - they're checked against Scrum Master's own token usage and a ceiling sized at
  `PROCESS_OVERHEAD_PERCENTAGE` percent of `SPRINT_TOKEN_BUDGET` (`ritual_token_budget`,
  `agents/scrum_team/helpers.py`), entirely independent of whether the shared budget below has
  tripped for DevTeam/QA/Architect/Product Owner. Previously `PROCESS_OVERHEAD_PERCENTAGE` was
  purely cosmetic (only ever printed in the sprint report); a verbose main sprint could exhaust the
  shared budget before Scrum Master ever got a turn to run the retrospective or KPIs at all, which is
  exactly why `create_sprint_report` kept failing across real eval runs. Once a fresh retro action
  and a fresh KPI update both exist this sprint, the one remaining step (`create_sprint_report`
  itself) is mechanically uncapped - and simultaneously tool-gated (`agent.py`'s
  `before_tool_callback`) to refuse anything that isn't the report call sequence - so a sprint can
  never fail to produce its required KPIs/report purely for lack of remaining ritual budget.
- **Automatic Tracking**: The system automatically tracks token usage after every LLM call and attributes it to the specific agent role.
- **Purpose**: Prevents long-running loops or runaway agent conversations within a single sprint.
  LiteLLM natively supports rate limits (tokens per minute) but does not provide a hard-stop for a
  *total cumulative token quota* across an entire sprint. Local enforcement provides immediate,
  zero-latency feedback and allows for a pure "logical" work limit.
- **Does not, by itself, cap a whole multi-sprint engagement** - it resets every sprint by design.
  For a cloud setup, `TOTAL_USD_BUDGET` below is the real whole-engagement ceiling. For a local/Ollama
  setup, where that USD ceiling doesn't apply at all (see §2's local-provider note), there is
  currently **no cumulative cap across many sprints** - only each individual sprint's token spend is
  bounded. See [EP-0009](../specs/stories/EP-0009-Time-And-Throughput-Based-Sprint-Limits-For-Local-Setups.md)
  for a proposed wall-clock (`SPRINT_TIME_BUDGET_HOURS`) or cumulative-token follow-up to close this
  gap - not yet implemented.

## 2. USD Budget (LiteLLM Layer)
- **Unit**: US Dollars (e.g., $0.50).
- **Scope**: **Whole engagement.** Never resets automatically, unlike the token budget above -
  `start_sprint`'s own mechanical per-sprint reset (`sprint_budget_reset_state_delta`,
  `agents/scrum_team/tools/budget.py`) deliberately does not touch it.
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
- **Depends on LiteLLM's bundled pricing table being current** (GH issue #298): every USD
  figure above - `scrum-sprint-budget` spend, and the "Actual USD Spend" line in the sprint
  report - is only as accurate as the per-token price LiteLLM's own bundled table has on
  file for each configured model id. A newly-released or retired/renamed model id can
  silently resolve to **zero cost** instead of an error - the chat completion still
  succeeds normally, so nothing about a running sprint looks wrong; only the derived USD
  figure is. Two defenses against this, both from GH issue #298:
  - The `litellm` image is pinned to a digest (not the floating `main-stable` tag) in all
    three `docker-compose*.yaml` files, so picking up LiteLLM's latest pricing-table
    updates is a deliberate, visible re-pin rather than whatever happened to be latest on
    a given pull. Re-pin with `docker pull docker.litellm.ai/berriai/litellm:main-stable
    && docker inspect docker.litellm.ai/berriai/litellm:main-stable --format
    '{{json .RepoDigests}}'`.
  - `doctor.py` queries the live proxy's `/model/info` endpoint and warns about any
    non-local (`ollama/`-prefixed aliases are expected to be free) configured alias whose
    resolved input/output cost-per-token both come back zero or missing. The sprint report
    itself (`create_sprint_report`/`render_fallback_sprint_report` in
    `agents/scrum_team/tools/budget.py`) also carries a runtime version of the same check -
    a ⚠️ SAFETY WARNING line if "Actual USD Spend" reports $0.00 alongside a substantial
    amount of real token usage this session (and the sprint isn't a local/Ollama one),
    since that combination is almost always this pricing gap rather than genuinely free
    usage.

## Monitoring & Reporting

### Quality KPIs
The system tracks performance indicators to provide visibility into team health:
- **Say-Do Ratio**: Compares planned vs. completed stories. A ratio of 1.0 means the team delivered exactly what was promised.
- **Commitment Reliability**: Measures the accuracy of the team's estimates and delivery capability.
- **Defect Escape Rate**: Percentage of defects found after a story is marked as "Done".
- **Code Complexity**: A maintainability metric to ensure long-term velocity.
- **Test Coverage**: The percentage of the codebase exercised by automated tests.
- **Vulnerability Scan Results**: Tracks critical, high, medium, and low security findings.
- **Per-Agent Prompt Context Usage**: What percentage of the configured model's context window each
  role's own concatenated, static system prompt (guardrails + workflow + Definition of Done/Ready -
  see [Agent Prompt Composition](AGENT-PROMPTS.md)) occupies before a single turn of real
  conversation happens. Tracked per role, not just in aggregate, because different roles can be
  configured with different models (`SCRUM_<ROLE>_MODEL` env vars), each with its own context
  window size - a role's own number can only be interpreted against its own model's limit, not a
  shared one. Best-effort: unavailable for a role whose model's context window can't be determined
  (the LiteLLM proxy is unreachable, or the model isn't in litellm's known context-window map) -
  reported as such rather than a guessed number.

### Sprint Report
At the end of each sprint, the Scrum Master generates a report via `create_sprint_report`
(GH #395 - previously Product Owner's tool, with KPI calculation split off to a separate
QualityGuardian role; both now consolidated into Scrum Master), which includes a detailed
breakdown of token usage per agent, total USD spend, and quality metrics.
Every sprint's report is kept — written to a sequentially numbered
`specs/reports/SPRINT-REPORT-NNN.md` (`001`, `002`, ...; the number is derived by scanning what's
already there, the same way story/ADR IDs are generated, so there's no separate counter to drift
out of sync) — and `specs/reports/SPRINT-REPORT-LATEST.md` is also kept up to date as a convenience
pointer to the most recent one.

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

### Per-Agent Prompt Context Usage
  - ProductOwner (scrum-po): 5,318 / 1,048,576 tokens (0.51%)
  - DevTeam (scrum-dev): 3,598 / 1,048,576 tokens (0.34%)
  - ScrumMaster (scrum-sm): not available (Could not determine this model's context window)
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
