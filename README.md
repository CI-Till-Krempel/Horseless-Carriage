# Horseless-Carriage

<p align="center">
  <a title="William Felton, Public domain, via Wikimedia Commons" href="https://commons.wikimedia.org/wiki/File:Trevithicks_Dampfwagen.jpg">
    <img width="330" alt="Trevithicks Dampfwagen" src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/16/Trevithicks_Dampfwagen.jpg/330px-Trevithicks_Dampfwagen.jpg">
  </a>
  <br>
  <em>Richard Trevithick's 1801 steam road carriage — early automobiles were called "horseless carriages" because, at first, the only way to describe them was by what they replaced. This project is the same kind of early, still-finding-its-shape step, applied to running a software team.</em>
</p>

A multi-agent Scrum team at your disposal—implemented as a small set of role-focused agents (PO, SM, Dev, QA, Architect) orchestrated by a root "ScrumOrchestrator".

## What is this, and why would I use it?

Most ways of using an LLM to help build software still put you in the loop for
every step: you write a prompt, read the output, decide what's next, write
another prompt. That works, but it doesn't scale past "assistant" — you're
still the one running the project.

Horseless Carriage instead gives the LLM(s) a small **simulated Scrum team** —
a Product Owner, Scrum Master, Dev Team, QA, and Architect, each with their own
role, tools, and a shared source of truth — and lets *them* run an actual
sprint-based delivery process against a **real target repository**: writing
specs, estimating and implementing stories, committing and pushing real code,
opening real pull requests, and reporting back on progress and budget. You
supply a product goal and (optionally) review points; the team runs the
process end to end.

What makes this practical rather than a runaway-cost demo:

- **You choose how much of a human stays in the loop** — from "approve every
  story" down to fully hands-off — see [Human Interaction Levels](docs/SETUP.md#human-interaction-levels).
- **Hard spend/token caps, not just polite requests** — a sprint cannot
  silently blow through its budget; see [Budget Management](docs/BUDGET.md).
- **Process is enforced in code, not just in the prompt** — the team can't
  mark a story "Done" with placeholder content, skip a review stage, or forget
  to update the roadmap, because the tools themselves refuse to allow it; see
  [Architecture](docs/ARCHITECTURE.md).
- **It evaluates its own performance release over release** against a fixed
  scenario, so regressions in team behavior are caught mechanically instead of
  anecdotally; see [Evaluation](docs/EVALUATION.md).
- **Bring your own model** — Google Gemini, Anthropic Claude, OpenAI, or a
  fully local/offline setup via Ollama, picked interactively from each
  provider's *current* model list; see [Setup](docs/SETUP.md).

Who this is for: a solo founder or small team with more product ideas than
engineering bandwidth, anyone evaluating how well a given model handles a real
multi-step engineering process autonomously, or a team that wants to prototype
quickly with real guardrails against runaway cost and low-quality output.

## Quick start

```bash
python3 setup_all.py       # guided setup: every step below, in order, ending with the option to start
```

Or run each step yourself, if you'd rather control the pace (`setup_all.py` just chains these):

```bash
python3 setup_llm.py       # pick a provider/model, configure the state repo, set budgets
python3 setup_project.py   # Docker/GitHub CLI checks, bring up the containers
python3 doctor.py          # validate everything before you run a real sprint - see below
python3 run.py             # start the team (add "dev" for developer mode - see below)
```

`doctor.py` acts as a gate in both paths: `run.py` refuses to start at all while any ERROR-level item
remains (missing `.env`, no state repo, etc.), showing the full punch list of everything that needs
fixing in one pass rather than one problem per re-run.

`run.py dev` (or answering "yes" to developer mode in `setup_all.py`) rebuilds the `agent`/`ollama`
images fresh before starting and runs with verbose (`debug`) logging for that invocation - useful
while iterating on the agent's own code without needing to remember `rebuild_images.py` separately.

Works the same way on macOS, Linux, and Windows (`python` instead of `python3`
on Windows) — see [Setup](docs/SETUP.md) for the full guided walkthrough,
including [Setting up on Windows](docs/SETUP.md#setting-up-on-windows) and
[running fully local with no commercial LLM](docs/SETUP.md#running-fully-local-no-commercial-llm).

## Documentation

| Topic | What's there |
|---|---|
| [Setup](docs/SETUP.md) | Guided LLM/project setup, Windows-specific instructions, running fully local (no commercial LLM), human interaction levels |
| [State Repository](docs/STATE-REPOSITORY.md) | What the "source of truth" directory is, its structure, and the health-check script |
| [Running the Agent](docs/RUNNING.md) | `run.py` modes, logging & session management, diagnostics (`doctor.py`) |
| [Testing](docs/TESTING.md) | The host-script test suite, the agent test suite, manual QA test plans |
| [Architecture](docs/ARCHITECTURE.md) | How the team is structured, the architecture diagram, the "enforce in code" design principle, the story-workflow pipeline |
| [Budget Management](docs/BUDGET.md) | Token/USD dual-layer budgeting, sprint reports, quality KPIs |
| [Evaluation](docs/EVALUATION.md) | How the team's own performance is evaluated release over release |
| [GitHub Integration](docs/GITHUB-INTEGRATION.md) | Agent identity/attribution, Personal Account vs. GitHub App auth, this repo's own GitHub scaffolding |
| [Notifications](docs/NOTIFICATIONS.md) | The blocking-interactions task list, and the pluggable notifier interface for surfacing them |
| [PREFLIGHT.md](PREFLIGHT.md) | Pre-flight checklist before your first run |
| [MANUAL.md](MANUAL.md) | User manual: concepts, day-to-day usage, common workflows |
| [RELEASE.md](RELEASE.md) | How this repo itself is versioned/released, and the evaluation harness's technical mechanics |
| [SECURITY.md](SECURITY.md) | Secret-handling notes and how to report a vulnerability |
| [qa/](qa/) | Manual test plans run before each release |

## What's in this repo

- `agents/scrum_team/`
  - `agent.py` — defines the root orchestrator plus sub-agents (Product Owner, Scrum Master, Dev Team, QA, Architect) and wires them to models via LiteLLM.
  - `prompts.py` — role prompts and routing rules for the orchestrator.
  - `tools/` — a package of lightweight "Scrum artifact" tools (backlog, github, budget, docs, etc.).
  - `__init__.py` — exports `root_agent`.

- `litellm.yaml` — model aliases used by the agents (e.g., `scrum-po`, `scrum-dev`, etc.), currently wired to Google Gemini.
- `config/model-templates/` — the same role→model mapping as standalone, swappable templates: `litellm.cloud-gemini.yaml` (a reference copy of the active `litellm.yaml`), `litellm.cloud-anthropic.yaml` (every role on Anthropic Claude), `litellm.cloud-openai.yaml` (every role on OpenAI), and `litellm.local-ollama.yaml` (every role served by one self-hosted Ollama model, no commercial API). Swap providers by copying the desired template over `litellm.yaml` (or repointing `docker-compose.yaml`'s `litellm` volume mount) - the role→alias names (`scrum-po`, `scrum-dev`, etc.) are identical across all of them, so no agent code changes are needed.
- `docker-compose.yaml` — runs a local LiteLLM proxy on port `4000` using `litellm.yaml` (the cloud/Gemini setup).
- `docker-compose.local.yaml` / `ollama.Dockerfile` / `ollama-entrypoint.sh` — a fully local alternative stack that adds a self-hosted Ollama container and points LiteLLM at `litellm.local-ollama.yaml` instead; no commercial LLM keys required. Run with `docker compose -f docker-compose.local.yaml up`.
- `.env.example` — environment variables for provider keys + LiteLLM proxy configuration.
- `.env.local.example` — the same, for the fully local Ollama stack (no provider keys needed).
- `requirements.txt` — Python dependencies.
- `setup_all.py` — guided, orchestrated setup: runs `setup_llm.py`, then `setup_project.py`, then gates on `doctor.py` (looping fix→retry until there are no more ERROR-level items), then offers to start the agent via `run.py` (including developer mode). Each step below is still a fully standalone script this just chains for a first-time/new-machine setup.
- `setup_llm.py` — interactive script to pick an LLM provider/model, configure the state repository, set the human interaction level + sprint budgets, write the result into `.env` + the active `litellm.yaml`/template, and run a live end-to-end test against it. For a Local/Ollama setup, also detects a usable NVIDIA GPU and offers to enable GPU acceleration. Re-running it prefills whatever's already configured as the default for every prompt. Run this before or after `setup_project.py`.
- `lib_llm_test.py` / `lib_env.py` — shared helpers (proxy liveness check, live test-request against a model alias; safe `.env` read/write) used by `setup_llm.py`, `doctor.py`, and the other scripts below.
- `lib_docker.py` — shared Docker Compose helpers (which compose file(s) are active, stopping a leftover stack before starting a new one) used by `run.py` and `rebuild_images.py`.
- `setup_project.py` — setup script for the project (named to avoid colliding with the `setup.py`/setuptools convention).
- `run.py` — run script for the agent. Gated by `doctor.py` (refuses to start with any ERROR-level item outstanding); `python3 run.py dev` enables developer mode (rebuilds `agent`/`ollama` images fresh before starting, runs with verbose/`debug` logging for that invocation).
- `run_tests.py` — script to run tests.
- `doctor.py` — validates your setup (Docker, `.env`, state repo, GitHub auth, live LLM connectivity) and collects every problem into a punch list of actionable items instead of stopping at the first one - `check()` returns the full structured list (used by `run.py`'s gate and `setup_all.py`); `run()`/the CLI stay a simple pass/fail wrapper around it.
- `check_state_repo.py` — a script to validate the state repository.
- `rebuild_images.py` — rebuilds the `agent` image (plus `ollama` for a Local/Ollama setup) from scratch, pulling fresh base images. `run.py`'s own `--build` only rebuilds layers Docker's cache considers stale, which never re-pulls a mutable base tag on its own; use this after a base-image update or a Dockerfile change the cache wouldn't otherwise catch (or use `python3 run.py dev` to do this automatically every run).
- `watch_roadmap.py` — optional, opt-in: polls the state repository for new commits on the develop branch or a story ready for the next pipeline stage, and notifies (doesn't start anything itself) - see [Running the Agent § Watch Mode](docs/RUNNING.md#watch-mode-get-notified-of-new-work).
- `tests/` — pytest suite for the host-scripts above (no Docker required); see [Testing](docs/TESTING.md).
- `qa/` — manual QA test plans, one per release, run before cutting a release; see [Testing](docs/TESTING.md).

  All of the scripts above are stdlib-only Python (no pip install required) so they run identically on macOS/Linux/Windows via `python3 <script>.py` (`python <script>.py` on Windows - see [Setting up on Windows](docs/SETUP.md#setting-up-on-windows)) - the actual agent workload always runs inside the Linux `agent` container regardless of host OS, so these host-side scripts are the only place platform mattered.

- `PREFLIGHT.md` — a pre-flight checklist to ensure your environment is correctly set up.
- `MANUAL.md` — a user manual: concepts, day-to-day usage, and common workflows.
- `RELEASE.md` — how this repo itself is versioned and released (SemVer + GitFlow), and how the team-performance evaluation harness works.
- `SECURITY.md` — secret-handling notes and how to report a vulnerability.
- `VERSION` — the current Horseless Carriage version.
- `eval/scenario/PRODUCT-VISION.md` — the fixed product vision used to automatically evaluate the agent team's own performance each release; see [Evaluation](docs/EVALUATION.md).

## Security

See [SECURITY.md](SECURITY.md) for secret-handling notes and how to report a
vulnerability.
