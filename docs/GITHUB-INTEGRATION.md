[← Back to README](../README.md)

# Agent Identity

Whether using a personal account or a dedicated GitHub App, the system automatically distinguishes between agent roles to ensure clear ownership and traceability.

## Role Attribution
1. **Git Commits**: Every commit is attributed to the specific agent role (e.g., `Architect` or `DevTeam`) via `GIT_AUTHOR` and `GIT_COMMITTER` settings.
2. **PR Comments and Reviews**: Tools like `gh_pr_comment` and `gh_pr_review` automatically prefix messages with the agent's role (e.g., `**Architect:** ...`), ensuring clear visibility in PR discussions.
3. **LiteLLM Spend**: Each agent uses its own virtual key, allowing you to track spend per role in the LiteLLM Admin UI.

## Recovering from a LiteLLM Database Wipe

If you clear the LiteLLM database (e.g., via `docker compose down -v`), your old virtual keys will become invalid. 

1. **Temporary Auth**: Update `LITELLM_PROXY_API_KEY` in your `.env` to match your `LITELLM_MASTER_KEY`. This allows the Orchestrator to start.
2. **Run Orchestrator**: Run the `ScrumOrchestrator`. It will detect the missing keys and re-initialize the agents, creating new virtual keys in the fresh database.
3. **Update .env**: After initialization, you can generate a new general-purpose virtual key via the Admin UI or by copying one of the agent keys and update your `.env` for better tracking.

---

# GitHub Integration

The agents can interact with GitHub using either a **Personal Account** (via the `gh` CLI) or a **GitHub App** (for a dedicated "Agent" identity).

## Option 1: Personal Account
This is the simplest setup. Ensure the `gh` CLI is installed and authenticated on your host machine:
```bash
gh auth login
```

## Option 2: GitHub App (Recommended for Agents)
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
5.  **Configure**: Provide these 3 items to the `ScrumOrchestrator` Setup Wizard (or to `setup_llm.py`'s Git identity / state repository prompts - see [Setup](SETUP.md)). It will handle the rest!

# This repo's own GitHub scaffolding

Separate from the GitHub *integration* above (how the agents authenticate against
your target repo), this repo also ships static scaffolding for maintaining
Horseless Carriage's own GitHub presence. These files are **not read by the agent
runtime** — they're plain repo hygiene you can adopt/edit like any other OSS
project:

- `.github/workflows/ci.yml` — runs the test suite on every push/PR.
- `.github/workflows/release.yml` — publishes a GitHub Release on `v*.*.*` tag push; see [RELEASE.md](../RELEASE.md).
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates.
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist emphasizing documentation updates.
- `.github/CODEOWNERS` — edit to your team.
- `config/github_config.yaml` — declarative notes on intended repo policy (branch protection, etc.) for humans setting up the GitHub repo's settings by hand; nothing in the codebase applies it automatically.

For how the *agents themselves* authenticate to GitHub, see
[GitHub Integration](#github-integration) above — that's the real, live
configuration (`GITHUB_TOKEN` or `GITHUB_APP_*` in `.env`).
