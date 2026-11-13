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
To have the agents act as a distinct entity in your **Workspace Repo**, with `GITHUB_REPO_URL` already
set in `.env` (see [Setup](SETUP.md)):

```bash
python3 setup_github_app.py
```

This registers a new GitHub App via GitHub's own App Manifest flow and installs it on that repo,
writing `GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/`GITHUB_APP_INSTALLATION_ID` into `.env` for you -
two browser confirmations are unavoidable (GitHub requires a human click for both creating an App and
installing one; there's no API around either), but nothing needs to be copy-pasted by hand. Pass
`--owner-repo owner/repo` to target a different repo than `GITHUB_REPO_URL`, or `--timeout SECONDS`
if your connection to GitHub is slow enough that the default 10-minute wait per step isn't enough.

Prefer doing this by hand, or the script's two local browser round-trips don't work in your
environment (e.g. a headless remote box with no SSH port-forwarding set up)? The manual steps it
automates:

1.  **Create**: Go to **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**.
    *   **Name**: anything unique (App names are unique across all of GitHub, not just your account).
    *   **Webhook**: Uncheck "Active".
2.  **Permissions**: Under **Permissions & events** → **Repository permissions**:
    *   `Contents` & `Pull requests`: **Read & write**.
    *   `Metadata`: **Read-only**.
3.  **Install**: Go to **Install App** in the sidebar and install it **ONLY** on your **Target Workspace Repository**.
4.  **Credentials**:
    *   Copy the **App ID** from the General page.
    *   Copy the **Installation ID** from the URL after installing (e.g., `.../installations/12345678`).
    *   Click **Generate a private key**, and keep the `.pem` file content ready.
5.  **Configure**: Paste these 3 values into `.env`'s `GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/
    `GITHUB_APP_INSTALLATION_ID` yourself (see `.env.example` for the exact format the private key
    needs - a single line with real newlines escaped as `\n`).

## Verifying access actually works

Whichever method you use, a credential merely being *present* in `.env` doesn't guarantee it can actually read or write the target repo's issues and pull requests - a fine-grained personal access token or a GitHub App installation can restrict those independently of one another. Given `GITHUB_REPO_URL` and a resolvable credential, `doctor.py` performs a live, read-only check: it reads the repo itself, lists its issues, and lists its pull requests, and reports the repo-level `permissions.push` flag as a proxy for write access. It stops short of attempting an actual write (there's no safe, side-effect-free way to test that), so a clean result here is encouraging but not an absolute guarantee every write will succeed.

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
3. **Architect** reviews it and calls `advance_story_stage(..., "Reviewed", review_comment=...)` -
   the review text is posted as a real `gh_pr_review` on the PR as part of this same call. **QA**
   then verifies it (`check_build()`) and calls
   `advance_story_stage(..., "Tested", review_comment=...)` the same way.
4. That same Tested call also merges the story's feature branch into `develop` once its state push
   succeeds (GH issue #391) - no separate `merge_story_pr()` call needed - this is what actually
   makes the story part of the integration branch the next sprint PR will pick up. See
   [Architecture](ARCHITECTURE.md) for the full 6-stage story pipeline this attaches to.

The underlying `merge_story_pr()` respects real branch-protection/required-checks by default (no
forced `--admin` merge) - a story-level merge is expected to behave like any other reviewed PR
merge. It remains available to call directly to retry a merge that failed as part of the Tested
transition.

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

Before that, at those same levels, QA must also have called `run_release_audit()` fresh this
cycle - a whole-increment regression/gap pass (full test suite, build, security scan,
stubbed-test sweep) distinct from each story's own per-story Tested-stage check. A real defect
it finds (`blocking_defects`) refuses `create_release_pr` unconditionally, at **every**
interaction level including CEO/EVAL - a genuinely broken build or failing test suite is never
shipped "clean" regardless of who's driving the release. A judgment-call finding
(`advisory_findings` - e.g. a security-scan hit, a stubbed test) is never hard-blocked, but
`create_release_pr` appends every one of these to the PR body itself under "Known Issues
(Release Audit)", and each is filed as a tracked Issue (`specs/requirements/`, the backlog) and
best-effort as a real GitHub issue too (`gh_issue_create`) - see `run_release_audit`
(`agents/scrum_team/tools/quality.py`).

# This repo's own GitHub scaffolding

Separate from the GitHub *integration* above (how the agents authenticate against
your target repo), this repo also ships static scaffolding for maintaining
Horseless Carriage's own GitHub presence. These files are **not read by the agent
runtime** — they're plain repo hygiene you can adopt/edit like any other OSS
project:

- `.github/workflows/ci.yml` — runs the test suite on every push/PR.
- `.github/workflows/release.yml` — publishes a GitHub Release on `v*.*.*` tag push; see [RELEASE.md](../RELEASE.md).
- `.github/workflows/eval.yml` — the multi-sprint team-performance evaluation harness (real LLM
  spend across 5 sprints, maintainer-approval-gated via the `eval-approval` environment) -
  deliberately not repo-hygiene-only like the rest of this list; see [Evaluation](EVALUATION.md)
  instead.
- `.github/workflows/adk-eval.yml` — the small, cheap ADK gate-enforcement eval set; runs
  automatically as a required check on every pull request and release tag, no manual approval step
  (see `eval/adk/README.md`).
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates.
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist emphasizing documentation updates.
- `.github/CODEOWNERS` (GH-316) — designates `@CI-Till-Krempel` as required reviewer for
  sensitive paths: `.github/workflows/` (CI/release automation), the CODEOWNERS file itself,
  `config/github_config.yaml` (repo-policy notes), `config/net-proxy/` (sandboxed dev/eval
  network allowlist), the `.env*.example`/`.env.test` templates, and `RELEASE.md`/`SECURITY.md`
  (process docs). Each entry carries a one-line comment in the file explaining why that path
  needs a human look.
- `config/github_config.yaml` — declarative notes on intended repo policy (branch protection, etc.) for humans setting up the GitHub repo's settings by hand; nothing in the codebase applies it automatically.

**CODEOWNERS is inert until a human flips one more switch.** Having a `.github/CODEOWNERS` file
does **not**, by itself, block anything — GitHub only *enforces* the listed reviewers once
"Require review from Code Owners" is **also** turned on as a branch protection rule for `main`
(GitHub web UI: **Settings → Branches → branch protection rule for `main` → check "Require
review from Code Owners"**). That is a one-time, repo-admin-only, UI/API action with repo-wide
blast radius — flipping it immediately changes merge requirements for *every* currently open PR
against `main`, not just future ones. For that reason, nothing in this codebase (and no
automation shipped by GH-316) ever calls the GitHub branch-protection API to turn it on; that
toggle is deliberately left as a manual step for whoever administers the real repo to decide when
to flip. Until it's flipped, `.github/CODEOWNERS` only documents *intended* reviewer assignments
(useful on its own as living documentation of "who should look at this"), with no merge-blocking
effect.

For how the *agents themselves* authenticate to GitHub, see
[GitHub Integration](#github-integration) above — that's the real, live
configuration (`GITHUB_TOKEN` or `GITHUB_APP_*` in `.env`).
