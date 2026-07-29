# Epic

- Epic ID: EP-0009
- Title: Time- and Throughput-Based Sprint Limits for Local/Ollama Setups
- Status: Draft
- Priority: Could
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Follow-up to GH issue #75 and ISSUE-0033 (`fix/local-provider-usd-budget-noop`), which fixed the
immediate bug: the USD budget guardrail was a silent no-op for local/Ollama setups, since a
self-hosted model has no real per-token price for LiteLLM to track. That fix only *skips* the
meaningless check - it doesn't give a local sprint any replacement signal of its own for "is this
still healthy / should it stop."

User feedback while that fix was in review: for the local case, a virtual USD budget is the wrong
thing to be measuring at all. What's actually interesting there is **runtime and token throughput**,
and **how much of that time the LLM spends idle** (queued/loading/thinking vs. actually generating) -
local inference speed varies enormously by hardware, so a token-count-only budget (the one guardrail
ISSUE-0033 left in place) can mean a 10-minute sprint on a GPU box or an hours-long one on CPU-only,
with no visibility into which. Alternative proposal: let a sprint be time-boxed directly, in hours,
rather than only by a token count that doesn't map to wall-clock time in any predictable way for a
local model.

## User Stories / Features
Proposed, not yet filed as individual stories:
- **Runtime/throughput metrics** - track wall-clock time and tokens/sec per model call (start/end
  timestamp around each LLM invocation, similar to where `update_token_usage_callback` already
  captures `usage_metadata` in `agents/scrum_team/agent.py`), surfaced via `get_budget_status` and/or
  the sprint report - independent of and complementary to the existing token budget.
- **LLM idle-time tracking** - distinguish "the model is actively generating" from "the request is
  queued/loading" (relevant for Ollama specifically - `OLLAMA_KEEP_ALIVE` unloading, cold model loads,
  a busy single-GPU box serializing requests) so a slow sprint's cause (agent is genuinely idle/waiting
  on a human vs. the local LLM itself is just slow) is visible rather than conflated.
- **Sprint-length-in-hours as a first-class limit** - a new, optional wall-clock budget, the natural
  local-setup analogue to the USD budget's role for cloud setups (where "cost" is the thing worth
  capping directly, rather than a token count that's only a rough proxy for it). Sketch of the
  feature:
  - **Config**: a new `SPRINT_TIME_BUDGET_HOURS` env var (optional - unset means "no time limit",
    same convention as the other budgets defaulting to sane values when unset). Set alongside
    `SPRINT_TOKEN_BUDGET` in `.env`/`.env.local.example`, surfaced through `update_budgets`/
    `get_budget_status` the same way the token/USD budgets already are (`state.budgets` in
    `agents/scrum_team/state.py` gains a `total_hours`/`elapsed_hours` pair, mirroring `total`/
    `token_usage.total` for tokens).
  - **Start-of-sprint anchor**: `start_sprint` (`agents/scrum_team/tools/scrum.py`) records a sprint
    start timestamp in state, the same moment token/USD budgets are reset for the new sprint - this
    is the "clock zero" the hour budget counts from, not process-start or container-start (a session
    can be stopped and resumed mid-sprint - see EP-0008's resume-after-interruption story - so the
    elapsed-hours figure must be computed from that stored anchor each time, not accumulated only
    while a process happens to be running).
  - **Check**: a third step in `check_cost_budget_callback`, alongside the existing token check (step
    1) and USD/local-provider-skip check (step 2) - compares wall-clock time elapsed since the sprint
    start anchor against `SPRINT_TIME_BUDGET_HOURS`. Naturally complements
    `LLM_LOCAL_PROVIDER=true` sprints (ISSUE-0033), where the USD check is already skipped and the
    token budget alone doesn't map predictably to wall-clock time on slow hardware - but not
    exclusive to local mode; a cloud sprint that's technically under budget in tokens/dollars but has
    been running for days is arguably just as worth flagging.
  - **Halt vs. warn**: per the Notes section below, likely advisory (a visible warning, not a hard
    stop) rather than a hard halt like the token/USD budgets - "this sprint has been running a long
    time" doesn't carry the same "keep going and you'll definitely overspend" certainty that token/USD
    exhaustion does, and a hard stop mid-story over wall-clock time alone could kill legitimately
    slow-but-progressing work (e.g. a large diff review taking longer than expected).
  - Pairs naturally with the runtime/throughput metrics story above: once per-call wall-clock time is
    already being tracked for the throughput metric, the sprint-level "hours elapsed" figure for this
    budget is a straightforward sum/anchor-diff of the same underlying data, not a second independent
    mechanism.

## Acceptance Criteria
- A local/Ollama sprint has at least one meaningful, non-token-based signal for "this sprint has been
  running unexpectedly long" - not just the pre-existing token budget.
- Whatever metric is added is visible the same places the existing budgets are (`get_budget_status`,
  sprint report), not a side-channel a human has to go look for separately.
- No change to cloud/proxy-mode behavior - this epic is scoped to what a local setup needs in place of
  (or in addition to) the USD budget ISSUE-0033 already established doesn't apply there.

## Notes
- Directly follows from ISSUE-0033/GH issue #75 - that fix is the minimal correctness fix (stop a
  no-op check from giving false confidence); this epic is the follow-up "what should a local sprint
  actually be bounded by instead" design work the user flagged during that fix's review.
- Worth deciding, when this is scoped into real stories, whether the time budget is a hard halt (like
  the existing token/USD budgets) or advisory/warning-only - unlike token/USD spend, "sprint ran long"
  isn't necessarily something to abort mid-story over.

## Roadmap
- Not yet targeted at a specific version - like EP-0007/EP-0008, prioritize against
  `specs/ROADMAP.md` once filed as real stories.
