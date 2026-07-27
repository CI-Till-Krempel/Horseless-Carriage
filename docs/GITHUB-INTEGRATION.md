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

# Branching Model (GitFlow)

Once configured, the team never pushes straight to a single branch of your Workspace Repo -
it works through a full GitFlow model with two protected integration branches and a
feature branch per story.

## The two integration branches

- **`main`** - the configured default branch (`repo.default_branch` in state, or the
  `GITHUB_REPO_BRANCH` env var; defaults to `main`).
- **`develop`** - the configured integration branch for ongoing work (`repo.develop_branch`
  in state, or the `GITHUB_DEVELOP_BRANCH` env var; defaults to `develop`).

Both are protected: `git_push` refuses a direct push to either one outright, regardless of
which agent calls it. `configure_github_repo` bootstraps both when setting up a fresh repo -
if only one exists yet (or the repo is entirely empty), it creates whichever is missing from
the other's current commit, so `develop` and `main` always start out pointing at the same
history. `seed_repository`'s initial scaffolding commit (README, `spec-templates/`) then lands
on `develop`, not `main` - `main` stays at the pre-seed state until the first sprint PR merges
into it.

## Story-level: feature branches

Every story is implemented on its own branch, never directly on `develop`:

1. **Dev Team** calls `start_feature_branch(story_id, slug)` before writing any code - this
   branches `feature/<story_id>-<slug>` off the latest `develop` and opens it immediately as a
   **draft** PR back into `develop`.
2. Dev Team implements the story on that branch (`write_file` + `git_push`), and once CI is
   green calls `mark_pr_ready_for_review()` to drop the draft status.
3. **Architect** reviews it (`advance_story_stage(..., "Reviewed")`), then **QA** verifies it
   (`check_build()`, `advance_story_stage(..., "Tested")`).
4. Once a story is marked Tested, **QA** calls `merge_story_pr()` to merge that story's feature
   branch into `develop` - this is what actually makes the story part of the integration branch
   the next sprint PR will pick up. See [Architecture](ARCHITECTURE.md) for the full 5-stage
   story pipeline this attaches to.

`merge_story_pr()` respects real branch-protection/required-checks by default (no forced
`--admin` merge) - a story-level merge is expected to behave like any other reviewed PR merge.

## Sprint-level: the "sprint PR" (`develop` -> `main`)

Once a sprint's planned stories have progressed as far through the pipeline as the sprint
allows, **Product Owner** opens the sprint PR from `develop` into `main` via
`create_release_pr(title, body)` - by then, every story that's ready has already been merged
into `develop` individually, so this PR is the integration point for the whole sprint, not a
fresh diff to assemble.

Whether that PR merges automatically or waits for a human depends on the configured
`INTERACTION_LEVEL` (see [Interaction Levels](INTERACTION-LEVELS.md)) - `create_release_pr`
itself refuses to run without a fresh `record_human_approval("release", ...)` at levels that
require one. In the team-performance evaluation harness (`INTERACTION_LEVEL=EVAL`, no human in
the loop at all), the harness auto-merges this one PR itself, standing in for that approval gate
- see [RELEASE.md](../RELEASE.md) "Team performance evaluation" for exactly how that harness
isolates each run's own `main`/`develop` pair so runs never contaminate each other or your real
branches.

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
