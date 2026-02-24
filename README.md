# Horseless-Carriage

A multi-agent Scrum team at your disposal—implemented as a small set of role-focused agents (PO, SM, Dev, QA, Architect) orchestrated by a root “ScrumOrchestrator”.

## What’s in this repo

- `agents/scrum_team/`
  - `agent.py` — defines the root orchestrator plus sub-agents (Product Owner, Scrum Master, Dev Team, QA, Architect) and wires them to models via LiteLLM.
  - `prompts.py` — role prompts and routing rules for the orchestrator.
  - `tools.py` — lightweight “Scrum artifact” tools that read/write shared state (backlog, sprint backlog, impediments, retro actions, decision log, etc.).
  - `__init__.py` — exports `root_agent`.

- `litellm.yaml` — model aliases used by the agents (e.g., `scrum-po`, `scrum-dev`, etc.).
- `docker-compose.yaml` — runs a local LiteLLM proxy on port `4000` using `litellm.yaml`.
- `.env.example` — environment variables for provider keys + LiteLLM proxy configuration.
- `requirements.txt` — Python dependencies.

## How it works (high level)

- A **root agent** (ScrumOrchestrator) receives your request and delegates to specialist sub-agents based on intent:
  - **Product Owner**: vision/goals, backlog items, acceptance criteria, prioritization
  - **Scrum Master**: facilitation, impediments, retros/actions
  - **Dev Team**: estimates, implementation plan, risks, test approach
  - **QA**: test strategy and quality signals
  - **Architect**: architectural risks and tradeoffs

- Agents maintain a shared in-session “source of truth” of Scrum artifacts (vision, goals, backlog, sprint goal, sprint backlog, DoD, impediments, retro actions, decision log).

## Setup

### 1) Create and activate a virtualenv

bash python -m venv .venv source .venv/bin/activate

### 2) Install dependencies

bash pip install -r requirements.txt

### 3) Configure environment variables

Copy `.env.example` to `.env` and fill in at least one provider key that matches the models you intend to use:

Use placeholders (don’t commit real secrets):

env OPENAI_API_KEY="<your_openai_key>" 
ANTHROPIC_API_KEY="<your_anthropic_key>" 
GOOGLE_API_KEY="<your_google_key>"
LITELLM_PROXY_API_BASE="http://localhost:4000" 
LITELLM_PROXY_API_KEY="<any_value_or_master_key>"


## Running the LiteLLM proxy (recommended)

The repo includes a Docker Compose setup for a local LiteLLM proxy that exposes a single endpoint and routes to different providers/models via aliases in `litellm.yaml`.

Start the proxy:

docker compose up

- Proxy listens on: `http://localhost:4000`
- Model aliases are defined in `litellm.yaml` (e.g. `scrum-orchestrator`, `scrum-po`, `scrum-dev`, ...)

## Using the Scrum team agent

This repository provides the agent implementation under `agents/scrum_team/`. The package exports:

- `agents.scrum_team.root_agent`

Exactly how you *run* the agent depends on the host app / runner you plug it into (for example, an ADK-based runner). The key point is that `root_agent` is the entrypoint and it orchestrates the rest.

## Notes

- If `LITELLM_PROXY_API_BASE` is set, the agents assume “proxy mode” and use LiteLLM via the proxy endpoint.
- Keep your `.env` local and never commit real API keys.

## Repository documentation structure

This repo includes a first-class docs workspace under `docs/` to keep requirements, architecture, stories, and agentic workflows versioned with code:

- `docs/requirements/`
  - `README.md` — guidance and index
  - `TEMPLATE-PRD.md` — Product Requirements Document template
  - `TEMPLATE-SRS.md` — Software Requirements Specification template
- `docs/architecture/`
  - `README.md` — guidance and index
  - `TEMPLATE-ADR.md` — Architecture Decision Record template
- `docs/stories/`
  - `README.md` — guidance and index
  - `TEMPLATE-USER-STORY.md` — user story template
- `docs/workflows/`
  - `README.md` — guidance and index
  - `TEMPLATE-AGENT-WORKFLOW.md` — agentic workflow/runbook template

Contribution rules
- One artifact per file; keep them small and link related docs together
- Update docs in the same PR as the related code when possible
- Never commit real secrets — use placeholders, keep real values in your local `.env`

## GitHub integration and repository configuration

This project ships with a minimal GitHub setup you can adopt:

- `.github/workflows/ci.yml` — basic CI installing dependencies
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist emphasizing documentation updates
- `.github/CODEOWNERS` — placeholder ownership (edit to your team)
- `config/github_config.yaml` — declarative repository policy placeholders (e.g., branch protection)

Authentication and configuration
1) Copy `.env.example` to `.env` and fill placeholders (do not commit real secrets):

- `GITHUB_OWNER` — org or username that owns the repo
- `GITHUB_REPO` — repository name
- `GITHUB_TOKEN` — a GitHub Personal Access Token with `repo` scope (for automation or local scripts)
- `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` — used by scripts/commits as needed
- Optional policy toggles: `DEFAULT_BRANCH`, `ENABLE_BRANCH_PROTECTION`, `REQUIRE_SIGNED_COMMITS`

2) Create or connect a GitHub repository:

- Using CLI (example):
  - `git init`
  - `git remote add origin git@github.com:<GITHUB_OWNER>/<GITHUB_REPO>.git`
  - `git add . && git commit -m "chore: init repo with docs + CI"`
  - `git push -u origin main`

3) Adjust CODEOWNERS, issue templates, and CI as you see fit. For Python lint/test, extend `ci.yml` with your tools (e.g., `ruff`, `pytest`).

Security notes
- Store tokens only locally in `.env` or a secure secret manager
- For GitHub Actions secrets, add them in the repository settings under Secrets and variables → Actions (e.g., `OPENAI_API_KEY` if runners need it)

