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

## Recovering From A Stuck Budget Halt
If a sprint is stuck repeating `🚫 [TOKEN BUDGET EXCEEDED]` or `🚫 [USD BUDGET EXCEEDED]` (SPEC-ISSUE-0092),
there are two ways to recover:

- **Permanent fix**: raise `SPRINT_TOKEN_BUDGET`/`TOTAL_USD_BUDGET` in `.env` and restart the agent
  container (`python3 run.py`) - the new ceiling applies from then on, every sprint.
- **Immediate, no-restart override**: `python3 run.py override-budget --tokens N [--usd N] --reason
  "..."` raises the ceiling live on the already-running `agent` container's current ADK session (via
  `agents/scrum_team/scripts/override_budget.py`, exec'd inside it) - the very next turn sees it,
  with no `.env` edit or restart. This is a **human-triggered escape hatch**, not a change to
  `check_cost_budget_callback`'s own enforcement: it's deliberately scoped to the sprint it's applied
  during only, and is cleared automatically the moment the next sprint starts
  (`sprint_budget_reset_state_delta`, `agents/scrum_team/tools/budget.py`) - it can never linger and
  mask a later, real `.env` fix the way a standing override would. It's also audited: it appends a
  `budget_override` entry to `blocking_interactions` (the same list `list_blocking_interactions()`
  and every other human-facing incident already use) carrying the reason given via `--reason`.
  Targets whichever session was updated most recently by default; pass `--session-id`/`--app-name`/
  `--user-id` to target a specific one (e.g. if more than one session exists in
  `sessions/adk_sessions.db`).

## 3. Recovering a sprint stuck on its budget

When a budget trips, every non-grace agent gets `🚫 [TOKEN BUDGET EXCEEDED]` or `🚫 [USD BUDGET
EXCEEDED]`. Scrum Master's ritual budget and Product Owner's close-out grace normally let the sprint
close out on its own: that produces the report and release PR, and the next `start_sprint` resets the
token budget. You only need to step in when you want **this** sprint to keep working, or when close-out
itself is stuck.

Two different things can be "stuck", and they need different fixes:

| Situation | What actually blocks | Fix |
|---|---|---|
| The per-sprint **limit** is too small | `SPRINT_TOKEN_BUDGET` | Raise it in `.env`, then restart. `sync_budgets_from_env` (`agents/scrum_team/tools/scrum.py`) copies it into `budgets.total` on every turn, so a restart is enough. |
| The limit is fine, but this sprint's **usage** is used up | `token_usage.total` in state | Reset the usage counters. Only `start_sprint` does this automatically, and it isn't reachable mid-sprint. |
| The whole-engagement **USD ceiling** is reached | `TOTAL_USD_BUDGET` + LiteLLM's `scrum-sprint-budget` | Raise the ceiling in `.env` **and** in LiteLLM. The LiteLLM side is only re-synced when a role's key is created. |

`reset_budget.py` does all three on the host, with no container or Python dependencies needed:

```bash
python3 reset_budget.py                          # read-only: limits, usage, open budget halts
docker compose stop agent                        # add your usual -f/-p flags; the script prints the exact command
python3 reset_budget.py --reset-usage            # same sprint, fresh token allowance
python3 reset_budget.py --token-budget 8000000   # raise SPRINT_TOKEN_BUDGET
python3 reset_budget.py --usd-budget 25          # raise TOTAL_USD_BUDGET + LiteLLM's scrum-sprint-budget
python3 run.py                                   # restart; `docker compose up` picks up the .env change
```

The flags can be combined. Each mutating run shows what it will change and asks first (`--yes` skips
the prompt). It refuses while the agent container is running (`--force` overrides that).

**Why a script and not just a JSON edit:** usage is persisted in **two** places, and both have to agree:

- `<STATE_REPO_PATH>/.hc/state.json` is loaded on a **new** session's first turn.
- `sessions/adk_sessions.db` (ADK's sqlite session store) holds a **resumed** session's own copy. That
  copy overwrites `state.json` again on its next save, so editing only `state.json` doesn't stick.

`--reset-usage` clears the same budget keys `start_sprint`'s own reset clears
(`sprint_budget_reset_state_delta`, `agents/scrum_team/tools/budget.py`): `token_usage`,
`budget_exhaustion_synced`, `critical_halt_notified` and `_budget_warning_pct_fired`. It also marks the
stale `[TOKEN BUDGET EXCEEDED]` blocking interactions resolved (they're kept as history). It does this
in `state.json`, committed as a local checkpoint in the state repo so `git revert` undoes it, and in
the most recent ADK session. Use `--session-id ID` or `--all-sessions` to target other sessions.
Sprint progress (backlog, stages, reports) is untouched.

What it deliberately does **not** do:

- **Reset LiteLLM's recorded USD spend.** `TOTAL_USD_BUDGET` is a whole-engagement ceiling. Spending
  more is an explicit decision to raise the ceiling, not to erase what was already spent.
- **Change any guardrail or default in code.** It only acts on an explicit human command, with the same
  knobs this page already documents.

### Doing it by hand

If you can't run the script, the equivalent manual steps are:

1. Stop the agent container.
2. Edit `SPRINT_TOKEN_BUDGET` / `TOTAL_USD_BUDGET` in `.env`.
3. For a usage reset, set `"token_usage": {"total": 0, "agents": {}}` in `.hc/state.json`, then start a
   **new** session instead of resuming the old one. A new ADK web UI tab does this by default. In CLI
   mode, use a new `SESSION_ID` or move `sessions/<SESSION_ID>.session.json` aside.
4. For a higher USD ceiling, also raise `scrum-sprint-budget` in the LiteLLM UI
   (`http://localhost:4000/ui`), or call its API directly:
   ```bash
   curl -X POST http://localhost:4000/budget/update \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
     -d '{"budget_id": "scrum-sprint-budget", "max_budget": 25}'
   ```
5. Restart with `python3 run.py`.

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
- **Per-Agent Prompt Module Breakdown** (GH #513): breaks that same combined total down per
  individual `prompt_modules/*.md` file (and `spec-templates/DOD.md`/`DOR.md` where applicable),
  with each piece's own line count and token count - so a role whose prompt is surprisingly large
  shows *which file* is actually responsible, not just the combined number. See
  `tools/quality.py::calculate_prompt_module_breakdown`. `doctor.py` also mechanically flags any
  `prompt_modules/*.md` file over 250 lines (this project's own steering-document guideline,
  stated directly in GH #513) as a non-blocking warning - trimming a file that trips it is
  deliberately left as separate, carefully-reviewed work, never auto-applied.

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

### Per-Agent Prompt Module Breakdown
  - ProductOwner:
    - ProductOwner-guardrails.md: 55 lines, 980 tokens
    - ProductOwner-workflow.md: 192 lines, 3,420 tokens
    - spec-templates/DOD.md: 38 lines, 918 tokens
```

The "Sprint Length Feedback" section is advisory only - see `_sprint_length_feedback` in
`agents/scrum_team/tools/budget.py`. It only appears with a budget-increase suggestion when the
sprint actually looks budget-starved (near/at its token cap **and** stories left unfinished); if
there's unused budget headroom left over, it says so instead and points at process/quality issues
rather than the budget. Nothing here ever changes `SPRINT_TOKEN_BUDGET`/`budgets.total` itself - a
human has to act on the suggestion deliberately.

Unlike every other section, "Retrospective Actions"/"Impediments" aren't just rendered - the whole
report generation is gated on them. `create_sprint_report` refuses to run at all unless a *new*
retro action or impediment has been logged since the last successful report (see
announcements/0.1.0-changelog.md "Sprint retrospective enforcement"), so if you see a report at
all, at least one of these two sections is
guaranteed to have real, new content - never both saying "none" at once.

### Admin UI
Log in to `http://localhost:4000/ui/` to see real-time cost tracking and budget status for the `scrum-sprint-budget`.
The multi-project dashboard (`dashboard.py`, GH issue #310) also shows each project's cumulative
actual spend alongside its budget ceiling, without needing to open LiteLLM's UI per project.
