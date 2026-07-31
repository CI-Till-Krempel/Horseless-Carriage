# Security

Horseless Carriage runs LLM agents with real credentials (LLM provider keys, GitHub
auth) against a real target repository. This document covers how secrets are
handled, what's deliberately *not* persisted, and how to report a problem.

## Reporting a vulnerability

This is a small, early-stage project — there is no dedicated security team or
bug-bounty program. If you find a security issue, please open a private report via
GitHub's "Report a vulnerability" flow (the repo's **Security** tab →
**Advisories** → **New draft security advisory**) rather than a public issue, so
it can be fixed before details are public.

## Secret handling — what's stored where

| Secret | Where it lives | Ever persisted to the state repo? |
|---|---|---|
| LLM provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) | `.env` only, read by the LiteLLM proxy at startup | No |
| `GITHUB_TOKEN` / `GITHUB_APP_PRIVATE_KEY` | `.env`, loaded into `ScrumState.github_token` at runtime (session-only) | No — deliberately excluded from `REPO_STATE_KEYS` (`agents/scrum_team/tools/scrum.py`) |
| Per-agent LiteLLM virtual keys (`ScrumState.litellm_keys`) | Generated at runtime via the LiteLLM proxy, kept in session state | No — deliberately excluded from `REPO_STATE_KEYS` |
| `LITELLM_MASTER_KEY` | `.env` only | No |

`REPO_STATE_KEYS` is an explicit allowlist, not a full dump of session state — see
`test_save_state_to_repo_excludes_keys_outside_allowlist` and
`test_litellm_keys_are_never_persisted_to_the_state_repo` in
`agents/scrum_team/tests/test_state_persistence.py` for the tests that pin this
down. If you add a new secret-bearing field to `ScrumState`, it must **not** be added
to `REPO_STATE_KEYS`.

## Other secret-handling notes

- **`.env` is git-ignored** (see `.gitignore`). `.env.test` is intentionally
  committed — it contains only fake placeholder values used by CI, never real
  credentials.
- **Git command results are redacted before being returned.** `_run()`
  (`agents/scrum_team/tools/base.py`) injects a base64-encoded GitHub token into
  `git`'s `AUTHORIZATION` header for HTTPS auth. Base64 is trivially reversible,
  not encryption — so the token is redacted out of the `cmd` field in `_run()`'s
  return value before it ever reaches a tool result, the conversation transcript,
  or a sprint report. See `_redact_cmd` and its tests in `test_base.py`.
- **ADK session files (`sessions/*.session.json`) are git-ignored.** They can
  contain raw conversation content — tool call arguments/results, or anything a
  user typed — so they're never tracked in this repo.
- **Debug logging** (`LOG_LEVEL=debug`) writes verbose traces to `sessions/*.log`,
  which are also git-ignored. Don't set `LOG_LEVEL=debug` in a shared/CI
  environment if you're not sure what upstream libraries (LiteLLM, ADK) include in
  their debug output.
- **Conversation content is not scanned for secrets.** If a user pastes a real
  credential into a prompt, it can end up in `ScrumState.messages` (persisted to
  the state repo, `REPO_STATE_KEYS`) or `ScrumState.transcript` (in-memory only,
  but rendered into `specs/reports/TRANSCRIPT-LATEST.md` and the per-run log at
  `sessions/transcript-<session-id>.log` - GH issue #127: `transcript` itself is
  no longer written into `.hc/state.json`). Don't paste real secrets into agent
  conversations — use `.env` for all credentials the agents need.
- **The target/state repo itself may be a real git repo that gets pushed to
  GitHub.** Treat anything written under `REPO_STATE_KEYS` as eventually public
  within your team/org, even if the repo is private.

## Financial guardrails (not a secret, but a real spend-control review)

`inject_litellm_key_callback` (`agents/scrum_team/agent.py`) falls back to
`LITELLM_PROXY_API_KEY` when an agent has no per-agent virtual key yet — a key
that isn't attached to the shared `scrum-sprint-budget` object and may not be
budget-capped at all (the documented DB-wipe recovery flow briefly points it at
the unbounded `LITELLM_MASTER_KEY`). Without a check, that would let a sub-agent
spend real money with the USD budget check blind to it.

`check_cost_budget_callback` closes this: every specialist agent is hard-blocked
from making any LLM call at all until it has its own budget-attached virtual key.
Only the Orchestrator is exempt, since it needs one bootstrap call to create
everyone else's key in the first place — see the tests in `test_agent.py`
(`test_check_cost_budget_callback_blocks_agent_without_virtual_key`,
`test_check_cost_budget_callback_exempts_orchestrator_bootstrap`) for the exact
boundary. See [Budget Management](docs/BUDGET.md) and
MANUAL.md §5 for user-facing detail.

## Secrets used by CI (`.github/workflows/eval.yml`)

The team-performance evaluation workflow (see RELEASE.md "Team performance
evaluation") needs real credentials as GitHub Actions repository secrets:
`GOOGLE_API_KEY`, `LITELLM_MASTER_KEY`, and a GitHub App
(`EVAL_GITHUB_APP_ID`/`_PRIVATE_KEY`/`_INSTALLATION_ID`) installed on the eval
repo specifically. These are written to a local `.env.eval` file inside the
runner's ephemeral workspace for the duration of the job only — never logged,
never committed. Scope that GitHub App's permissions to the eval repo alone
(`Contents` + `Pull requests: Read & write`); it does not need — and should not
be given — access to any other repo.

## Known limitations (being upfront, not exhaustive)

- No automated secret-scanning (e.g. gitleaks) runs in CI yet.
- No signed commits / branch protection is enforced by default — see
  `config/github_config.yaml` for policy you can apply manually in GitHub repo
  settings.
- If no LiteLLM proxy is configured at all (`LITELLM_MASTER_KEY`/
  `LITELLM_PROXY_API_BASE` unset), the USD budget check and the virtual-key
  guardrail both skip entirely — only the local token-count budget applies in
  that mode. This isn't the documented/supported deployment (`docker-compose.yaml`
  always runs LiteLLM), but it's a real gap if someone runs outside that setup.
- The Orchestrator's own bootstrap call (needed to create the very first virtual
  key) is inherently unscoped from LiteLLM's budget system — there's no way
  around at least one initial call happening before any key exists. It's
  protected only by the local token budget, not a USD cap, until its own key
  (if any) is created.
- This is a young project; the above reflects a basic review ahead of the first
  public release (`v0.1.0`), not a formal security audit.
