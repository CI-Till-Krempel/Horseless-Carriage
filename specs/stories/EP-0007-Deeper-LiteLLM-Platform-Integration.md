# Epic

- Epic ID: EP-0007
- Title: Deeper LiteLLM Platform Integration
- Status: Draft
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #61): "LiteLLM offers a whole lot of tooling for the use of agents, skills,
MCPs and so forth. Used primarily as a monitoring tool as of now, integrating the features offered
by LiteLLM could really improve the product. Investigate the LiteLLM API and create an epic for
improving the integration."

**Current usage** (`litellm.yaml`, `docker-compose.yaml`, `agents/scrum_team/tools/budget.py`): one
model deployment per role (`scrum-po`, `scrum-sm`, ... - swapped wholesale via
`config/model-templates/litellm.cloud-*.yaml`/`litellm.local-ollama.yaml`), a per-agent virtual key
minted via `POST /key/generate` with a hard budget cap, `general_settings.num_retries: 3`, and spend
logs (`store_prompts_in_spend_logs: true`) viewed manually in the Admin UI. That's it - no fallbacks
across providers, no guardrails, no caching, no MCP integration, no alerting. The proxy is genuinely
"primarily a monitoring tool" today, exactly as the issue says.

**What LiteLLM actually offers beyond that** (verified against current docs, not assumed from
memory - see Notes for sources), that's directly relevant to this system's own gaps:

- **Fallbacks/retries/cooldowns** (`docs.litellm.ai/docs/proxy/reliability`): the router can fail
  over to a different model/provider automatically on error/rate-limit, instead of the request just
  failing. Directly relevant to `check_cost_budget_callback`'s `[BUDGET ERROR]` halt (ISSUE-0025 /
  GH issue #53) and general provider flakiness - a transient Gemini outage currently halts the whole
  sprint for every agent; a configured fallback to Anthropic/OpenAI could keep it running.
- **Guardrails** (18+ built-in providers - Presidio for PII redaction, Aporia/Lakera for prompt-
  injection detection, Azure Content Safety for moderation - configurable `pre_call`/`during_call`/
  `post_call`/`logging_only`, per-key/team or `default_on`): this system's agents write files, push
  code, and open PRs based on LLM output, and read back GitHub issue/PR bodies and file contents into
  that same context - a real prompt-injection surface (a malicious issue body instructing the agent
  to do something harmful) that nothing currently defends against at the gateway level.
- **Response/semantic caching** (Redis/S3/GCS-backed, vector-similarity matching for semantically
  equivalent prompts): could cut token spend on repeated/similar planning queries within a sprint -
  directly relevant to this system's own token/USD budget guardrails (`docs/BUDGET.md`).
- **Alerting/webhooks** (`docs.litellm.ai/docs/proxy/alerting` - Slack/email on spend thresholds and
  hung requests, soft vs. hard budget alerts): overlaps directly with the blocking-interactions
  notification system just added (ISSUE-0025 / GH issue #53,
  `agents/scrum_team/tools/notifications.py`) - LiteLLM's own alerting could be a second, complementary
  signal path (proxy-level spend/hang detection) rather than something to duplicate.
- **MCP Gateway** (native Model Context Protocol support - a single `/mcp` endpoint, per-key/team/org
  tool-access permissioning, OAuth 2.0): a real architectural alternative to this repo's current
  per-role, hard-coded Python `tools=[...]` lists in `agents/scrum_team/agent.py` - centrally
  registering/permissioning tools through the proxy instead. A bigger shift, not a small integration.
- **A2A (Agent-to-Agent) protocol support**: agents built on other frameworks (LangGraph, CrewAI,
  Vertex AI Agent Engine) could route through the same LiteLLM proxy and inherit its cost
  tracking/fallbacks/guardrails - relevant if this system ever needs to interoperate with agents
  built outside its own ADK-based stack.

The proxy image is pinned to `docker.litellm.ai/berriai/litellm:main-stable` (a rolling tag, not a
fixed version) - all of the above should already be available without an image-version bump, though
each was verified against current docs, not this specific deployed build.

## User Stories / Features
Proposed, not yet filed as individual stories (that's this epic's own backlog, to be broken down and
prioritized when picked up) - each below is scoped small enough to be one story on its own:

- **Provider fallback on budget/proxy error** - configure `litellm_settings.fallbacks` for at least
  the Gemini deployment; extend `check_cost_budget_callback`'s `[BUDGET ERROR]` path so a fallback
  succeeding doesn't halt the sprint at all, only a fallback *also* failing does.
- **Prompt-injection guardrail on GitHub-sourced content** - a `pre_call` guardrail (e.g. Lakera or
  Aporia) scoped to whichever tools read external content (`gh_pr_comment`/issue bodies/file reads)
  back into an agent's context, `logging_only` first to observe false-positive rate before blocking.
- **PII redaction guardrail as a default-on safety net** - Presidio, `default_on: true`, low-risk to
  add since it only redacts, never blocks.
- **Semantic response caching for planning-heavy roles** (ProductOwner/ScrumMaster) - measure actual
  token/cost savings against a real sprint before deciding whether to extend it further.
- **LiteLLM alerting as a second notification channel** - wire `general_settings.alerting: ["slack"]`
  (or email) as a proxy-level signal *in addition to* `blocking_interactions`' own notifiers, for the
  spend/hung-request cases that happen inside the proxy where this repo's own code has no visibility.
- **MCP Gateway adoption spike** - a time-boxed investigation (not a commitment) into whether
  centralizing tool registration/permissioning through LiteLLM's MCP Gateway is worth the migration
  cost versus the current per-role Python tool lists, given issue #48 (parallel/event-driven agents)
  and #58 (coordinator delegation) may independently push toward more decoupled tool boundaries
  anyway - this spike's output is a documented adopt/defer decision, not code.

## Acceptance Criteria
- At least one reliability story (fallbacks) and one safety story (a guardrail) are broken out as
  real stories in `specs/stories/` and picked up through the standard 5-stage pipeline.
- The relationship between LiteLLM's own alerting and this system's `blocking_interactions`/
  `Notifier` plugin system (ISSUE-0025) is explicitly decided - integrate, or deliberately keep
  separate - rather than the two silently overlapping or duplicating.
- The MCP Gateway adoption spike produces a documented decision (adopt now / adopt later / defer
  indefinitely) with reasoning, before any tool-registration migration work is scoped as its own epic.
- Every new integration is additive to the existing model-routing/virtual-key/budget setup - nothing
  here should require re-architecting how providers are configured today
  (`config/model-templates/litellm.*.yaml`).

## Notes
- **Sources** (fetched 2026-07-29, not relied on from training-data memory given how fast this space
  moves): `docs.litellm.ai/docs/mcp` (MCP Gateway), `docs.litellm.ai/docs/proxy/guardrails/quick_start`
  (guardrails), `docs.litellm.ai/docs/proxy/reliability` (fallbacks), `docs.litellm.ai/docs/proxy/alerting`
  (Slack/email alerting), plus general 2026 feature-overview coverage (futureagi.com, mintmcp.com,
  seaflux.tech) cross-checked against the official docs above rather than taken at face value alone.
- **Risk - guardrail providers are mostly hosted third-party services** (Presidio can self-host;
  Aporia/Lakera/Azure Content Safety are external SaaS) - adds an API key + a new external dependency
  + latency to every guarded call. Start with `logging_only` mode to measure impact before blocking
  anything in production.
- **Risk - MCP Gateway adoption is a real architectural decision**, not a drop-in config change - the
  spike above exists specifically to avoid committing to a migration before its cost/benefit is
  understood, given this repo's tools are presently plain Python functions tightly coupled to
  `tool_context.state` (see `agents/scrum_team/tools/base.py`), not natively MCP-shaped.
- **Not proposed**: A2A protocol adoption as an actual story - noted above for completeness (it's a
  real LiteLLM capability relevant to "agents... and so forth" from the issue), but this repo has no
  current need to interoperate with non-ADK agent frameworks; revisit if/when that changes.
- Depends on / relates to: ISSUE-0025 (blocking-interactions notification system - the alerting
  overlap above), GH issue #48 (parallel/event-driven agents) and #58 (coordinator delegation) - both
  independently relevant to whether centralizing tool access via MCP Gateway is worth it.

## Roadmap
- Not yet targeted at a specific version - this epic's own backlog (the stories above) should be
  prioritized against the current roadmap (`specs/ROADMAP.md`) once filed as real stories, starting
  with the two called out in Acceptance Criteria (fallbacks, one guardrail) as the lowest-risk,
  highest-value first slice.
