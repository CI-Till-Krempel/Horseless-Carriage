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

bash docker compose up

- Proxy listens on: `http://localhost:4000`
- Model aliases are defined in `litellm.yaml` (e.g. `scrum-orchestrator`, `scrum-po`, `scrum-dev`, ...)

## Using the Scrum team agent

This repository provides the agent implementation under `agents/scrum_team/`. The package exports:

- `agents.scrum_team.root_agent`

Exactly how you *run* the agent depends on the host app / runner you plug it into (for example, an ADK-based runner). The key point is that `root_agent` is the entrypoint and it orchestrates the rest.

## Notes

- If `LITELLM_PROXY_API_BASE` is set, the agents assume “proxy mode” and use LiteLLM via the proxy endpoint.
- Keep your `.env` local and never commit real API keys.