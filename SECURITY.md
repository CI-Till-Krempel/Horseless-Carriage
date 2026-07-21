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
  credential into a prompt, it can end up in `ScrumState.messages`/`transcript`,
  which *are* persisted to the state repo (`REPO_STATE_KEYS`). Don't paste real
  secrets into agent conversations — use `.env` for all credentials the agents
  need.
- **The target/state repo itself may be a real git repo that gets pushed to
  GitHub.** Treat anything written under `REPO_STATE_KEYS` as eventually public
  within your team/org, even if the repo is private.

## Known limitations (being upfront, not exhaustive)

- No automated secret-scanning (e.g. gitleaks) runs in CI yet.
- No signed commits / branch protection is enforced by default — see
  `config/github_config.yaml` for policy you can apply manually in GitHub repo
  settings.
- This is a young project; the above reflects a basic review ahead of the first
  public release (`v0.1.0`), not a formal security audit.
