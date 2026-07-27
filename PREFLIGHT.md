# Pre-flight Checklist

This document provides a checklist to ensure your environment is correctly set up before running the Horseless Carriage agent.

> **On Windows?** See README.md's [Setting up on Windows](README.md#setting-up-on-windows) for
> prerequisites first (Python, Docker Desktop, Git, `gh`) - everything below then works the same
> way, using `python` instead of `python3`.

## 1. Environment Setup

- [ ] **Docker is installed:**
  - Run `docker --version` to verify.
- [ ] **Docker Compose is installed:**
  - Run `docker compose version` to verify.

## 2. Configuration

- [ ] **`.env` file exists:**
  - If not, run `python3 setup_project.py` to create it from `.env.example` (or `python3 setup_llm.py`, which also picks a provider/model interactively).
- [ ] **API keys are set:**
  - Open the `.env` file and ensure that you have set at least one provider API key (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`).
- [ ] **`LITELLM_MASTER_KEY` is set:**
  - This is required for the LiteLLM proxy.
- [ ] **`STATE_REPO_PATH` is set and the directory exists:**
  - This is your team's "source of truth" repo (see README.md "State Repository"). `run.py` and `doctor.py` hard-fail without it. Create the directory if it doesn't exist yet: `mkdir -p <path>`.
- [ ] **A GitHub authentication method is configured:**
  - Either `GITHUB_TOKEN` (personal access token), or all three of `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID` (GitHub App). See README.md "GitHub Integration" for how to choose.

## 3. Services

- [ ] **Docker is running:**
  - Run `docker ps` to verify.
- [ ] **The `litellm` container is running:**
  - Run `docker compose ps litellm` to verify. If it's not running, run `docker compose up -d`.

## 4. Authentication

- [ ] **`gh` CLI is installed:**
  - Run `gh --version` to verify.
- [ ] **`gh` CLI is authenticated:**
  - Run `gh auth status` to verify. If you're not logged in, run `gh auth login`.
  - Only needed if you're using the Personal Account auth method above; skip if you're using a GitHub App.

## 5. Validation

- [ ] **Run the doctor script:**
  - Run `python3 doctor.py` to validate your setup. Address any errors before proceeding.

Once all these checks have passed, you are ready to run the agent:

```bash
python3 run.py          # Web mode (default) - opens the ADK web UI and LiteLLM dashboard
python3 run.py cli      # Interactive CLI session instead
python3 run.py daemon   # Add to either of the above to run detached
```

See README.md "Running the Agent" for details on each mode.
