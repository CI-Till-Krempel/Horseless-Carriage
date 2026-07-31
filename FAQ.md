# FAQ

Quick answers to the questions people ask before (or right after) their first run. For step-by-step
instructions see [MANUAL.md](MANUAL.md); for a specific error message see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

**What is Horseless Carriage, in one paragraph?**
It gives an LLM a simulated Scrum team — Product Owner, Scrum Master, Dev Team, QA, and Architect,
orchestrated by a root "ScrumOrchestrator" — that runs an actual sprint-based delivery process
against a real target repository: writing specs, implementing stories, committing and pushing code,
opening pull requests, and reporting progress and budget back to you. You supply a product goal and
(optionally) review points; the team runs the process end to end. See [README.md](README.md) for the
full pitch.

**Which LLM providers are supported?**
Google Gemini, Anthropic Claude, and OpenAI (cloud, your own API key), or a fully local/offline setup
via [Ollama](https://ollama.com) — no commercial API key at all. `setup_llm.py` walks you through
picking one. See [Configuration Reference](docs/CONFIGURATION.md) for how the provider choice
interacts with everything else.

**Can I run it fully offline/free?**
Yes — pick the Local/Ollama provider during setup (`cp .env.local.example .env`, then
`docker compose -f docker-compose.local.yaml up`). No provider API keys, no external network
calls once the model is pulled. See [Setup § Running fully local](docs/SETUP.md#running-fully-local-no-commercial-llm).

**How much does a sprint actually cost, and what stops it from running away?**
A dual-layer budget: a hard **token budget** enforced locally (zero network latency, resets every
sprint) and a real **USD budget** enforced by your LiteLLM proxy (whole-engagement, never resets).
Neither is a suggestion — an agent with no budget-capped key is refused outright, and an exhausted
budget halts the sprint mechanically. See [Budget Management](docs/BUDGET.md).

**How much human oversight is required — can I really leave it unattended?**
You choose: from "approve every story" down to fully hands-off, via `INTERACTION_LEVEL`
(`Product` / `Stakeholder` / `CEO` / `EVAL`). This isn't just documentation — it mechanically gates
which approvals are required before the team may implement a story or release an increment. See
[Interaction Levels](docs/INTERACTION-LEVELS.md).

**Does it actually write code to my real repo, or just plan?**
It writes real code, commits it, and opens real pull requests against the repository you configure
(`GITHUB_REPO_URL`) — using either your personal GitHub access or a dedicated GitHub App identity, so
commits/PR comments are attributed to the specific agent role that made them. Direct pushes to your
protected branches (`main`/`develop`) are refused; changes only land via a reviewed PR. See
[GitHub Integration](docs/GITHUB-INTEGRATION.md).

**What happens if it crashes, or my state file gets corrupted?**
The team's working state (`.hc/state.json` in your [state repository](docs/STATE-REPOSITORY.md)) is
checkpointed to git on every save. If it's ever unreadable, the team auto-recovers from the newest
good git checkpoint; failing that, both `check_state_repo.py` (host-side) and the Orchestrator itself
(mid-session, in chat) offer a repair/reset/delete menu — nothing is silently lost or silently wiped.

**How is this different from just chatting with an LLM myself?**
A single chat session leaves every "did this actually get done correctly?" check to you. Here, the
core rules (a story can't be marked Tested without a passing build, a release PR is checked against
what the sprint actually touched, a human approval can't be reused across sprints, etc.) are enforced
by the tools themselves, not just requested in a prompt — see
[Architecture § enforce in code](docs/ARCHITECTURE.md). The team also evaluates its own performance
release over release against a fixed scenario, so behavior regressions are caught mechanically. See
[Evaluation](docs/EVALUATION.md).

**Is my code/data sent anywhere besides my chosen LLM provider?**
No third-party analytics or telemetry. Requests go to your configured provider (or nowhere, for
Local/Ollama) via your own LiteLLM proxy instance, which runs in your own Docker Compose stack. See
[SECURITY.md](SECURITY.md) for secret-handling notes.

**What's NOT ready yet in this release?**
Changelog generation, customer-facing announcement drafting, and end-user product-doc tooling are
planned for v0.2 (see [specs/ROADMAP.md](specs/ROADMAP.md)). This is an early release: no automated
secret-scanning in CI yet, no formal security audit performed, and GitFlow branching
(`develop`/`release/*`/`hotfix/*`) is documented but not yet in effect. See
[ANNOUNCEMENT-v0.1.0.md](ANNOUNCEMENT-v0.1.0.md)'s "Known Issues" for the current list.

**Where do I report a bug or ask for help?**
[Open a GitHub issue](https://github.com/CI-Till-Krempel/Horseless-Carriage/issues) for bugs and
questions. For a security vulnerability specifically, follow the reporting process in
[SECURITY.md](SECURITY.md) instead of a public issue.
