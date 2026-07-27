# Horseless-Carriage

A multi-agent Scrum team at your disposal—implemented as a small set of role-focused agents (PO, SM, Dev, QA, Architect) orchestrated by a root “ScrumOrchestrator”.

## What’s in this repo

- `agents/scrum_team/`
  - `agent.py` — defines the root orchestrator plus sub-agents (Product Owner, Scrum Master, Dev Team, QA, Architect) and wires them to models via LiteLLM.
  - `prompts.py` — role prompts and routing rules for the orchestrator.
  - `tools/` — a package of lightweight “Scrum artifact” tools (backlog, github, budget, docs, etc.).
  - `__init__.py` — exports `root_agent`.

- `litellm.yaml` — model aliases used by the agents (e.g., `scrum-po`, `scrum-dev`, etc.), currently wired to Google Gemini.
- `config/model-templates/` — the same role→model mapping as standalone, swappable templates: `litellm.cloud-gemini.yaml` (a reference copy of the active `litellm.yaml`), `litellm.cloud-anthropic.yaml` (every role on Anthropic Claude), `litellm.cloud-openai.yaml` (every role on OpenAI), and `litellm.local-ollama.yaml` (every role served by one self-hosted Ollama model, no commercial API). Swap providers by copying the desired template over `litellm.yaml` (or repointing `docker-compose.yaml`'s `litellm` volume mount) - the role→alias names (`scrum-po`, `scrum-dev`, etc.) are identical across all of them, so no agent code changes are needed.
- `docker-compose.yaml` — runs a local LiteLLM proxy on port `4000` using `litellm.yaml` (the cloud/Gemini setup).
- `docker-compose.local.yaml` / `ollama.Dockerfile` / `ollama-entrypoint.sh` — a fully local alternative stack that adds a self-hosted Ollama container and points LiteLLM at `litellm.local-ollama.yaml` instead; no commercial LLM keys required. Run with `docker compose -f docker-compose.local.yaml up`.
- `.env.example` — environment variables for provider keys + LiteLLM proxy configuration.
- `.env.local.example` — the same, for the fully local Ollama stack (no provider keys needed).
- `requirements.txt` — Python dependencies.
- `setup_llm.py` — interactive script to pick an LLM provider/model (fetching the provider's *current* model list via its own API), set the human interaction level + sprint budgets, write the result into `.env` + the active `litellm.yaml`/template, and run a live end-to-end test against it. Run this before or after `setup_project.py`.
- `lib_llm_test.py` / `lib_env.py` — shared helpers (proxy liveness check, live test-request against a model alias; safe `.env` read/write) used by `setup_llm.py`, `doctor.py`, and the other scripts below.
- `setup_project.py` — setup script for the project (named to avoid colliding with the `setup.py`/setuptools convention).
- `run.py` — run script for the agent.
- `run_tests.py` — script to run tests.
- `doctor.py` — a script to validate your setup, including a live check that the configured LLM provider/model is actually reachable and responding.
- `check_state_repo.py` — a script to validate the state repository.
- `tests/` — pytest suite for the host-scripts above (no Docker required); see "Testing" below.

  All of the scripts above are stdlib-only Python (no pip install required) so they run identically on macOS/Linux/Windows via `python3 <script>.py` (`python <script>.py` on Windows - see [Setting up on Windows](#setting-up-on-windows)) - the actual agent workload always runs inside the Linux `agent` container regardless of host OS, so these host-side scripts are the only place platform mattered.
- `PREFLIGHT.md` — a pre-flight checklist to ensure your environment is correctly set up.
- `MANUAL.md` — a user manual: concepts, day-to-day usage, and common workflows.
- `RELEASE.md` — how this repo itself is versioned and released (SemVer + GitFlow), and how the team-performance evaluation harness works.
- `SECURITY.md` — secret-handling notes and how to report a vulnerability.
- `VERSION` — the current Horseless Carriage version.
- `eval/scenario/PRODUCT-VISION.md` — the fixed product vision used to automatically evaluate the agent team's own performance each release; see [RELEASE.md "Team performance evaluation"](RELEASE.md#team-performance-evaluation).

## How it works (high level)

- A **root agent** (ScrumOrchestrator) receives your request and delegates to specialist sub-agents based on intent:
  - **Product Owner**: vision/goals, backlog items, acceptance criteria, prioritization
  - **Scrum Master**: facilitation, impediments, retros/actions
  - **Dev Team**: estimates, implementation plan, risks, test approach
  - **QA**: test strategy and quality signals
  - **Architect**: architectural risks and tradeoffs

- Agents maintain a shared in-session “source of truth” of Scrum artifacts (vision, goals, backlog, sprint goal, sprint backlog, DoD, impediments, retro actions, decision log).

## Setup

> **Windows users:** install the prerequisites in [Setting up on Windows](#setting-up-on-windows)
> below first, then follow steps 1-2 the same way, using `python` instead of `python3`.

### 1. Guided LLM/project setup: `setup_llm.py`

```bash
python3 setup_llm.py
```

This interactively walks you through everything specific to *this* run of
the team:

1. **Provider**: Google Gemini, Anthropic Claude, OpenAI, or fully local via
   Ollama.
2. **API key** (cloud providers only) - reuses an existing key from `.env` if
   present, otherwise prompts for one.
3. **Model**: fetches the provider's *current* model list via its own API
   (not a hardcoded/stale list) and lets you pick a main model for all
   scrum-team roles, plus an optional cheaper/faster model for the automated
   eval harness's `scrum-eval-cheap` alias. The local/Ollama provider instead
   offers a curated pick-list (Ollama has no such API) or a manual tag.
4. **Git identity** (user name/email used for commits the agent makes on
   your behalf), **human interaction level** (`Product` / `Stakeholder` /
   `CEO` / `EVAL` - see [docs/INTERACTION-LEVELS.md](docs/INTERACTION-LEVELS.md)),
   and **sprint budgets** (token budget, USD budget, max process overhead
   percentage).
5. Writes all of the above into `.env` and the active `litellm.yaml` (or,
   for the local provider, `config/model-templates/litellm.local-ollama.yaml`).
6. **Live test**: starts the `db` + `litellm` (+ `ollama`) containers and
   sends one real, minimal request through the proxy to confirm the new
   configuration actually works end-to-end, before you invest time running
   the full team against it.

Re-run it any time to switch provider/model or adjust budgets - it reuses
whatever's already in `.env` as the default for each prompt.

### 2. Project/infrastructure setup: `setup_project.py`

```bash
python3 setup_project.py
```

This will:
1. Check for Docker and Docker Compose.
2. Guide you through GitHub CLI setup.
3. Create a `.env` file from the template (if it doesn't exist - `setup_llm.py`
   above will already have created and populated it in most cases).
4. Start the database and LiteLLM containers.

After running setup, please edit the `.env` file to add your specific API keys and configuration.

Before running the agent, it is recommended to run the doctor script to validate your setup:

```bash
python3 doctor.py
```

### Setting up on Windows

All of this project's host-side tooling (`setup_llm.py`, `setup_project.py`, `doctor.py`, `run.py`,
`run_tests.py`, `check_state_repo.py`) is plain stdlib Python - no bash, no WSL2 required. The only
part of the stack that ever needed a Unix shell was the old setup scripts, which this replaces
entirely; Docker Desktop may still use WSL2 internally as its own backend, but that's automatic and
not something you need to set up yourself.

**Prerequisites:**

1. **Python 3.9+** - install from [python.org](https://www.python.org/downloads/) (check "Add
   python.exe to PATH" during setup) or via `winget install Python.Python.3.12`. Windows installs
   register the `python` command (and the `py` launcher) - **not** `python3` - so use `python
   setup_llm.py` etc. instead of `python3 ...` everywhere in this README.
2. **Docker Desktop** - [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
   Requires virtualization enabled; Docker Desktop will offer to set up the WSL2 backend for you on
   first run (Hyper-V is the alternative backend). After installing, open a *new* terminal and verify
   with `docker --version` and `docker compose version`.
3. **Git for Windows** - [git-scm.com/download/win](https://git-scm.com/download/win/) - needed on
   the host to `git clone` your target state repository before pointing `STATE_REPO_PATH` at it; the
   agent's own commits happen using the separate git already installed inside the `agent` container.
4. **GitHub CLI (`gh`)** - [cli.github.com](https://cli.github.com/), or `winget install GitHub.cli`,
   then `gh auth login`.

**Running the scripts** - identical from PowerShell, cmd.exe, or Git Bash (pick whichever terminal
you're comfortable with):

```powershell
python setup_llm.py
python setup_project.py
python doctor.py
python run.py
```

**A few Windows-specific things to know:**

- **`STATE_REPO_PATH`**: a normal Windows path works in `.env`, e.g.
  `STATE_REPO_PATH=C:\Users\you\horseless-carriage-state` or `STATE_REPO_PATH=C:/Users/you/horseless-carriage-state`
  (Python's `pathlib` accepts either). Whichever drive it's on must be shared with Docker Desktop
  (Settings -> Resources -> File Sharing) for the `docker-compose.yaml` bind mount to work.
- **Copying `.env` templates manually** (only needed if you skip `setup_llm.py`/`setup_project.py`,
  which already do this for you): PowerShell's `copy` works like Unix `cp` - `copy .env.example .env`;
  the same command works in cmd.exe too.
- **Firewall prompts**: the first `docker compose up` may trigger a Windows Defender Firewall prompt
  for the ports LiteLLM (`4000`), the ADK web UI (`8000`), and - for the local/Ollama setup only -
  Ollama (`11434`) listen on. Allow access on your private network.
- **Long path limits**: this only affects tooling running directly on the Windows host, never inside
  the Linux `agent` container (which has no such limit). If you ever hit it anyway, enable long paths
  with `git config --system core.longpaths true`.

### Running fully local (no commercial LLM)

To run the whole team against a self-hosted [Ollama](https://ollama.com) model instead of Gemini/OpenAI/Anthropic — no provider API keys, no external network calls once the model is pulled:

```bash
cp .env.local.example .env        # Windows: copy .env.local.example .env
docker compose -f docker-compose.local.yaml up
```

This starts the same `db` + `litellm` + `agent` services as `docker-compose.yaml`, plus an `ollama` container that pulls its model on first start (see `ollama-entrypoint.sh`). Every `scrum-*` role alias is routed to that one model — see `config/model-templates/litellm.local-ollama.yaml`. The default model is `llama3.1:8b` (tool-calling support, ~16GB RAM or any modern GPU); to fit different hardware, change the `model:` lines in that file and the matching `OLLAMA_MODEL` in `.env`/`docker-compose.local.yaml` together (see comments in both files for smaller/larger alternatives). Don't run this alongside `docker-compose.yaml` — the two `litellm` service definitions target different config files.

For a more detailed guide, see the [PREFLIGHT.md](PREFLIGHT.md) checklist.

## Running the Agent

Run the agent using the `run.py` script:

```bash
python3 run.py
```

This script will:
1. Load environment variables from `.env`.
2. Check for the existence of the state repository path.
3. Build and run the agent container.
4. Wait for the LiteLLM dashboard (and, in web mode, the ADK web UI) to come up, then open them in your default browser.

`run.py` supports three modes, which can be combined:

| Command | Behavior |
|---|---|
| `python3 run.py` | **Default.** ADK web frontend, foreground, at `http://localhost:8000`. |
| `python3 run.py cli [query...]` | Interactive CLI session in your terminal instead of the web UI. |
| `python3 run.py daemon` | Add to either of the above to run detached (`python3 run.py daemon` or `python3 run.py cli daemon`). |

The LiteLLM admin dashboard (`http://localhost:4000/ui`) is opened automatically in every mode.

### Running in Daemon Mode

To run the agent in the background:

```bash
python3 run.py daemon
```

To view logs when running in daemon mode:

```bash
docker compose logs -f agent
```

## Logging & Session Management

### Logging
The system uses Docker Compose for logging, which captures both orchestrator activity and sub-agent delegations.
- **View Real-time Logs**: `docker compose logs -f agent`
- **Verbosity**: The agent runs in `--verbose` mode by default, providing detailed traces of tool calls, LLM interactions, and state transitions.

### Session Management
Sessions are managed by the ADK framework to ensure continuity across restarts:
- **Persistence**: Conversation history and agent state are stored in the `sessions/` directory as `.session.json` files.
- **Session Identification**: Each run uses a `SESSION_ID` (defined in `.env`).
- **Resuming**: The `run.py` script automatically detects existing session files and uses the `--resume` flag to pick up where the team left off.
- **Interruption**: You can stop the agent at any time (e.g., by pressing `Ctrl+C` in interactive mode). The framework will automatically save the session state to a file on exit.
- **Metadata**: A shared SQLite database (`sessions/adk_sessions.db`) tracks session lifecycle and metadata, ensuring that even if the container is removed, the session history remains accessible.

## Testing

`python3 run_tests.py` runs everything in one command - both suites below,
the host-script suite first (fails fast, no need to wait for Docker if it's
broken):

```bash
python3 run_tests.py
```

### Host-script tests (`tests/`)

Covers the setup/doctor tooling itself (`lib_env.py`, `lib_llm_test.py`,
`setup_llm.py`, `doctor.py`, `check_state_repo.py`, `run.py`) - `.env`
read/write correctness, LiteLLM model-YAML generation for all 4 providers,
each provider's live-model-list fetch/filtering logic (mocked HTTP), and
every guard-clause branch in `doctor.py`/`check_state_repo.py`. Runs
directly on the host, no Docker required (that's the point - these scripts
must work before any container exists) and no real network calls (a local
mock HTTP server stands in for the LiteLLM proxy). Requires `pytest`
(`pip install pytest` or `pip install -r requirements.txt`) - the only
place in this project where a host-side pip install is needed. Run just
this suite with:

```bash
python3 -m pytest tests/ -v
```

### Agent test suite (`agents/scrum_team/tests`, via Docker Compose)

To run the complete agent test suite (both unit and integration tests) using Docker Compose:

```bash
python3 run_tests.py
```

This script executes `pytest` inside the agent container, providing full network access to the LiteLLM and Database services. It includes coverage reporting for the `agents/` package.

### Integration Testing

The integration test suite (`test_llm_integration.py`) verifies the end-to-end connection between the agents and the LiteLLM Proxy. It ensures that:
- **Key Generation**: Virtual keys are correctly created for different agent roles.
- **Budget Association**: These keys are correctly linked to the shared `scrum-sprint-budget`.
- **Proxy Routing**: LLM calls from agents are successfully routed through the LiteLLM Proxy.

For integration tests to function, the `run_tests.py` script utilizes `docker compose run`, which automatically starts the necessary dependency containers (`litellm` and `db`) if they are not already active.

## State Repository

The **State Repository** is the team's "Source of Truth." It is a dedicated directory (ideally a Git repository) where the agents persist all project-related data.

### Concept
Unlike the session history (which is transient and internal), the State Repository contains human-readable artifacts and the official project state. This separation allows the agents to be ephemeral while the project remains permanent.

### Structure
- **`state.json`**: The internal machine-readable state of the Scrum artifacts (backlog, impediments, etc.).
- **`specs/`**: A directory containing the actual generated documents (PRDs, ADRs, Stories) based on the templates in `spec-templates/`.

### Usage
- **Configuration**: Set the `STATE_REPO_PATH` in your `.env` file to point to your target repository.
- **Persistence**: Tools used by the agents automatically commit changes to this repository (if configured) or write them directly to the filesystem.

## Diagnostics & Maintenance

### Doctor Script

The `doctor.py` script validates your local environment, ensuring Docker is running, the `.env` file is correctly configured, and the state repository path is accessible.

```bash
python3 doctor.py
```

### State Repository Check

The `check_state_repo.py` script verifies that your state repository is in the expected state for the tools to work correctly. It checks for the correct directory structure and ensures no stray template files are present in the `specs` directory.

```bash
python3 check_state_repo.py
```

## Using the Scrum team agent

This repository provides the agent implementation under `agents/scrum_team/`. The package exports:

- `agents.scrum_team.root_agent`

Exactly how you *run* the agent depends on the host app / runner you plug it into (for example, an ADK-based runner). The key point is that `root_agent` is the entrypoint and it orchestrates the rest.

## Human Interaction Levels

How much of a human is actually in the loop is configurable via `INTERACTION_LEVEL` (`.env`) - see
[docs/INTERACTION-LEVELS.md](docs/INTERACTION-LEVELS.md) for the full breakdown. Four levels:

| Level | The human's role |
|---|---|
| `Product` (default) | Product Owner stand-in: task-level priorities, developer questions. |
| `Stakeholder` | Business stakeholder: business needs, release order, feature approval, review feedback. |
| `CEO` | Approves only the sprint budget; reads the sprint report as a management summary. |
| `EVAL` | No human at all - fixed-length automated evaluation runs (see `run_eval.py`). |

This isn't just documentation - it changes which `record_human_approval` gate is mechanically
required before the team may implement stories (`advance_story_stage(..., "Implemented")`) or
release an increment (`create_release_pr`), and how much detail `create_sprint_report` actually
renders (full technical detail at Product/EVAL, business-framed at Stakeholder, budget-and-headlines
only at CEO); see the linked doc for the exact mapping.

## Notes

- If `LITELLM_PROXY_API_BASE` is set, the agents assume “proxy mode” and use LiteLLM via the proxy endpoint.
- Keep your `.env` local and never commit real API keys.

## Architecture

The following diagram describes the interaction between the user, the ADK framework, the Scrum agents, and the supporting infrastructure (LiteLLM & GitHub).

```mermaid
graph TD
    User([Human User]) -- Prompts/Feedback --> Orchestrator
    
    subgraph ADK_Framework [ADK Framework]
        Orchestrator[Scrum Orchestrator]
        
        subgraph Agents [Specialist Agents]
            PO[Product Owner]
            SM[Scrum Master]
            Dev[Dev Team]
            QA[QA Agent]
            Arch[Architect]
        end
        
        Orchestrator -- Delegates --> Agents
        
        subgraph Callbacks [Hard Guardrails]
            EnforceBudget[Enforce Budget Callback]
            InjectKey[Inject LiteLLM Key Callback]
        end
        
        subgraph Tools [Toolbox]
            GitTools[Git/GH CLI Tools]
            ScrumTools[Scrum State Tools]
            DocTools[Template/Doc Tools]
            BudgetTools[Budgeting Tools]
        end
    end
    
    Orchestrator -.-> Callbacks
    Agents -.-> Callbacks
    Agents --> Tools
    Orchestrator --> Tools
    
    subgraph Infrastructure [Supporting Infrastructure]
        LiteLLM[LiteLLM Proxy]
        DB[(PostgreSQL)]
        LiteLLM --- DB
    end
    
    subgraph External [External Systems & Storage]
        Clone[(State Repo - Local Clone)]
        GH[GitHub Repository]
    end
    
    Callbacks -- API Keys/Usage --> LiteLLM
    LiteLLM -- Routed Requests --> Models[LLM Providers: Gemini, OpenAI, etc.]
    
    Tools -- Writes Docs/Reports/State --> Clone
    Clone -- git push / gh pr create --> GH
    
    GH -- Persists State --> StateFile[.hc/state.json]
```

### Design Principle: Enforce Mandatory Process Mechanically, Not Just by Prompting

A failure mode surfaced repeatedly across real eval runs: a rule stated only in an agent's prompt
("check the DoD before marking a story Done", "call `update_roadmap` once a story completes") was
reliably *not* followed by a cheap model under budget/token pressure, even when the prompt was
clear, mandatory, and repeated. Prompting alone is advisory - the model can skip it, misremember
it, or take a shortcut, and nothing catches that.

**The principle this project follows for any future mandatory process step: enforce it in the tool
layer itself, not only in the prompt that asks for it.** A tool call should either perform the
required step as an unavoidable side effect, or refuse to proceed when a mandatory precondition
isn't met - it should never be possible to "successfully" call a tool in a way that silently skips
a required step.

Concretely, in this codebase (see "Story workflow" below for the full feature these come from):
- **Automatic side effects instead of a second, separate step.** `advance_story_stage` updates
  `specs/ROADMAP.md`'s checkboxes as part of the same call that marks a stage complete - there's no
  separate "now remember to sync the roadmap" instruction left for the agent to forget. The earlier
  design (a documented, mandatory, but separate `update_roadmap` step) didn't reliably happen.
- **Structural refusal instead of a checklist.** `_story_readiness_issues` refuses to write a story
  with an empty/placeholder title, user story, or acceptance criteria - it doesn't just ask the
  model to check `spec-templates/DOD.md`/`DOR.md` first. `check_build()` actually attempts to
  install the project's dependencies rather than asking QA to "verify the build works" by reading
  the code. `create_sprint_report` refuses to run at all unless a *new* retro action or impediment
  was logged since the last report - a real run had the Scrum Master role go uninvoked for 5
  sprints straight because "retro is mandatory" was stated but never actually enforced.
- **No bypass path left open.** Enforcing order/ownership in one tool (`advance_story_stage`) is
  only real enforcement if every other way to change the same state is closed off too -
  `upsert_story`/`upsert_epic`/`plan_sprint_backlog_item` refuse to set `status` directly to a
  pipeline stage name *or* a legacy done-synonym (`blocks_direct_status_set` in
  `agents/scrum_team/helpers.py`), so the enforced path can't be routed around through a
  less-guarded tool.
- **A tool's own success/failure must reflect the whole operation, not just the first part of it.**
  Several tools (`create_release_pr`, `upsert_backlog_item`, `plan_sprint_backlog_item`,
  `advance_story_stage`) call a second helper to finish their job (writing the story file, syncing
  the roadmap); each propagates that helper's failure into its own top-level `status` instead of
  unconditionally reporting "ok" once the first part succeeded - a caller that only checks the
  top-level status must be able to trust it.

When adding a new mandatory rule to this codebase, default to asking: can this be enforced in the
tool that performs the relevant action, rather than only stated in a prompt? If yes, do that first.
The prompt instruction is still worth keeping - it's what tells the agent to attempt the action in
the first place - but it must not be the only thing standing between "mandatory" and "optional".

## Budget Management

The system implements a **dual-layer budgeting strategy** to ensure both operational safety and financial control. This approach leverages LiteLLM's native financial enforcement while providing local, high-fidelity control over the logical "Sprint Budget" in tokens.

### 1. Token Budget (ADK Layer)
- **Unit**: Total tokens (e.g., 1,000,000).
- **Enforcement**: Hard-blocked locally, purely from session state/`SPRINT_TOKEN_BUDGET` — no
  call to LiteLLM is involved, so this guardrail applies **even if the LiteLLM proxy isn't
  running**. See step 1 of `check_cost_budget_callback` in `agents/scrum_team/agent.py`
  (usage is recorded by the separate `update_token_usage_callback`).
- **Automatic Tracking**: The system automatically tracks token usage after every LLM call and attributes it to the specific agent role.
- **Purpose**: Prevents long-running loops or runaway agent conversations. LiteLLM natively supports rate limits (tokens per minute) but does not provide a hard-stop for a *total cumulative token quota* across an entire sprint. Local enforcement provides immediate, zero-latency feedback and allows for a pure "logical" work limit.

### 2. USD Budget (LiteLLM Layer)
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
  RELEASE.md "Team performance evaluation".
- **No unscoped fallback spend**: every specialist agent's calls are blocked in code
  until it has its own `scrum-sprint-budget`-attached virtual key —
  `create_litellm_virtual_key()` must run for it first. Without this, a missing key
  would silently fall back to `LITELLM_PROXY_API_KEY`, which isn't attached to
  `scrum-sprint-budget` and so wouldn't be covered by the check above at all (this
  matters in particular right after a [LiteLLM database wipe recovery](#recovering-from-a-litellm-database-wipe),
  where that fallback key is briefly pointed at the unbounded master key). The
  Orchestrator itself is exempt from this specific check, since it needs one
  bootstrap call to create everyone else's key in the first place — see
  `check_cost_budget_callback` in `agents/scrum_team/agent.py`.
- **Tools**: `update_budgets(total_usd=0.50)`, `create_litellm_virtual_key()`.

### Monitoring & Reporting

#### Quality KPIs
The system tracks performance indicators to provide visibility into team health:
- **Say-Do Ratio**: Compares planned vs. completed stories. A ratio of 1.0 means the team delivered exactly what was promised.
- **Commitment Reliability**: Measures the accuracy of the team's estimates and delivery capability.
- **Defect Escape Rate**: Percentage of defects found after a story is marked as "Done".
- **Code Complexity**: A maintainability metric to ensure long-term velocity.
- **Test Coverage**: The percentage of the codebase exercised by automated tests.
- **Vulnerability Scan Results**: Tracks critical, high, medium, and low security findings.

#### Sprint Report
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

#### Admin UI
Log in to `http://localhost:4000/ui/` to see real-time cost tracking and budget status for the `scrum-sprint-budget`.

## Agent Identity
Whether using a personal account or a dedicated GitHub App, the system automatically distinguishes between agent roles to ensure clear ownership and traceability.

### Role Attribution
1. **Git Commits**: Every commit is attributed to the specific agent role (e.g., `Architect` or `DevTeam`) via `GIT_AUTHOR` and `GIT_COMMITTER` settings.
2. **PR Comments and Reviews**: Tools like `gh_pr_comment` and `gh_pr_review` automatically prefix messages with the agent's role (e.g., `**Architect:** ...`), ensuring clear visibility in PR discussions.
3. **LiteLLM Spend**: Each agent uses its own virtual key, allowing you to track spend per role in the LiteLLM Admin UI.

### Recovering from a LiteLLM Database Wipe

If you clear the LiteLLM database (e.g., via `docker compose down -v`), your old virtual keys will become invalid. 

1. **Temporary Auth**: Update `LITELLM_PROXY_API_KEY` in your `.env` to match your `LITELLM_MASTER_KEY`. This allows the Orchestrator to start.
2. **Run Orchestrator**: Run the `ScrumOrchestrator`. It will detect the missing keys and re-initialize the agents, creating new virtual keys in the fresh database.
3. **Update .env**: After initialization, you can generate a new general-purpose virtual key via the Admin UI or by copying one of the agent keys and update your `.env` for better tracking.

---

## GitHub Integration

The agents can interact with GitHub using either a **Personal Account** (via the `gh` CLI) or a **GitHub App** (for a dedicated "Agent" identity).

### Option 1: Personal Account
This is the simplest setup. Ensure the `gh` CLI is installed and authenticated on your host machine:
```bash
gh auth login
```

### Option 2: GitHub App (Recommended for Agents)
To have the agents act as a distinct entity in your **Workspace Repo** (e.g., Kronograf), follow this simplified setup:

1.  **Create**: Go to **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**.
    *   **Name**: `Horseless-Carriage-Agent`
    *   **Webhook**: Uncheck "Active".
2.  **Permissions**: Under **Permissions & events** → **Repository permissions**:
    *   `Contents` & `Pull requests`: **Read & write**.
    *   `Metadata`: **Read-only**.
3.  **Install**: Go to **Install App** in the sidebar and install it **ONLY** on your **Target Workspace Repository**.
4.  **Credentials**: 
    *   Copy the **App ID** from the General page.
    *   Copy the **Installation ID** from the URL after installing (e.g., `.../installations/12345678`).
    *   Click **Generate a private key**, and keep the `.pem` file content ready.
5.  **Configure**: Provide these 3 items to the `ScrumOrchestrator` Setup Wizard. It will handle the rest!

## Repository documentation structure

This project separates documentation templates from the actual specification artifacts:

### 1. Specification Templates (`spec-templates/`)
Stored in this repository, these provide the structure for Scrum artifacts.

- `spec-templates/requirements/` — Product Requirements Document (PRD) and Software Requirements Specification (SRS) templates.
- `spec-templates/architecture/` — Architecture Decision Record (ADR) templates.
- `spec-templates/stories/` — User story templates.
- `spec-templates/workflows/` — Agentic workflow and runbook templates.
- `spec-templates/DOD.md` / `spec-templates/DOR.md` — Definition of Done / Definition of Ready
  checklists, mapped onto the 5-stage story pipeline below. Unlike the templates above, these
  aren't per-item blueprints to copy - every role reads them directly (`read_doc`).

### Story workflow (Ready → Implemented → Reviewed → Tested → Accepted)

Every story passes through exactly these 5 stages, in this exact order, no skipping:

| Stage | Owner | Gate |
|---|---|---|
| READY | Product Owner (Architect supports on technical feasibility) | Real title/user story/acceptance criteria, Dev Team estimate - see `spec-templates/DOR.md` |
| IMPLEMENTED | Dev Team | Real, working code committed and pushed |
| REVIEWED | Architect | Architectural/technical review complete |
| TESTED | QA | `check_build()` passes; test strategy verified |
| ACCEPTED | Product Owner | Acceptance criteria genuinely verified met |

A stage is only ever completed via `advance_story_stage(title_or_id, stage)`
(`agents/scrum_team/tools/requirements.py`), which enforces this **in code**, not just by asking
nicely in a prompt:
- **No skipping**: rejects the call if the stages before it aren't done yet.
- **Stage ownership**: rejects the call if the calling agent isn't that stage's owner.
- **One story at a time**: `product_backlog` list order is priority order - a story can't advance
  past READY until the story immediately above it has reached ACCEPTED.
- **Content quality**: rejects marking READY (or the legacy "Done"/"Accepted") if the title/user
  story/acceptance criteria are missing or still placeholder text
  (`_story_readiness_issues` in `agents/scrum_team/tools/requirements.py`).
- **No bypass**: `upsert_story`/`upsert_epic`/`plan_sprint_backlog_item` refuse to set `status`
  directly to any of the 5 stage names - only `advance_story_stage` can.
- It also updates `specs/ROADMAP.md`'s per-stage checkboxes for that story automatically, in the
  same call - see below.

This was added after real eval runs repeatedly shipped stories as "Done" with an empty/placeholder
user story, a `requirements.txt` pinning a package version that doesn't exist, and a roadmap that
never reflected any of it - asking the agents nicely to follow a written checklist wasn't enough on
its own.

### 2. Specification Artifacts (`specs/`)
Stored in your **target state repository** (configured via `STATE_REPO_PATH`), these are the actual documents generated and updated by the agents.

- `specs/requirements/` — Active PRDs and SRS documents.
- `specs/architecture/` — Architecture Decision Records (ADRs).
- `specs/stories/` — Refined User Stories.
- `specs/workflows/` — Agentic workflows and runbooks.
- `specs/reports/` — Sprint review reports and budget status.
- `specs/ROADMAP.md` — Product roadmap tracking releases and stories. Each story gets its own 5
  checkboxes, one per stage of the story workflow above:
  ```
  - [US-0001] Create a to-do list
    - [x] READY
    - [x] IMPLEMENTED
    - [ ] REVIEWED
    - [ ] TESTED
    - [ ] ACCEPTED
  ```
  These update automatically as a side effect of `advance_story_stage` - see "Story workflow" above
  - not via a separate manual roadmap-editing step. Status is resolved from whichever of
  `sprint_backlog` (Dev Team's working record) or `product_backlog` (PO's) has the more complete
  stage history for a given story, since the two aren't otherwise kept in sync with each other.

Contribution rules
- One artifact per file; keep them small and link related docs together
- Update docs in the same PR as the related code when possible
- Never commit real secrets — use placeholders, keep real values in your local `.env`

## This repo's own GitHub scaffolding

Separate from the GitHub *integration* above (how the agents authenticate against
your target repo), this repo also ships static scaffolding for maintaining
Horseless Carriage's own GitHub presence. These files are **not read by the agent
runtime** — they're plain repo hygiene you can adopt/edit like any other OSS
project:

- `.github/workflows/ci.yml` — runs the test suite on every push/PR.
- `.github/workflows/release.yml` — publishes a GitHub Release on `v*.*.*` tag push; see [RELEASE.md](RELEASE.md).
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates.
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist emphasizing documentation updates.
- `.github/CODEOWNERS` — edit to your team.
- `config/github_config.yaml` — declarative notes on intended repo policy (branch protection, etc.) for humans setting up the GitHub repo's settings by hand; nothing in the codebase applies it automatically.

For how the *agents themselves* authenticate to GitHub, see
[GitHub Integration](#github-integration) above — that's the real, live
configuration (`GITHUB_TOKEN` or `GITHUB_APP_*` in `.env`).

## Security

See [SECURITY.md](SECURITY.md) for secret-handling notes and how to report a
vulnerability.