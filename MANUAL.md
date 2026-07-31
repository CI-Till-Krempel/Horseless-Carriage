# User Manual (Draft, v0.1.0)

> **Status: First draft**, written ahead of the v0.1.0 release. Corrections and
> expansions welcome — this covers the concepts and the happy path, not every edge
> case.

This is a task-oriented guide to actually using Horseless Carriage. For "what's in
this repo" see [README.md](README.md); for installation mechanics see
[docs/SETUP.md](docs/SETUP.md) and [PREFLIGHT.md](PREFLIGHT.md) — this manual
assumes setup is done and focuses on how to work with the team day to day.

## 1. What you're running

Horseless Carriage is a root "Scrum Orchestrator" agent that delegates to five
specialist agents — **Product Owner**, **Scrum Master**, **Dev Team**, **QA**, and
**Architect** — to run real Scrum sprints against a real target repository on your
behalf. You talk to the Orchestrator; it routes your request to whichever
specialist owns that concern (see [Who owns what](#2-who-owns-what) below).

Two repos are involved, and it's worth keeping them distinct from the start:

- **This repo** (`Horseless-Carriage`) — the tool itself. You don't edit this unless
  you're contributing to the tool.
- **Your state repo** (`STATE_REPO_PATH` in `.env`) — your team's actual "source of
  truth": product vision, backlog, specs, ADRs, sprint reports, and the machine
  state file (`.hc/state.json`). This is the repo the agents write to, commit to,
  and open release PRs against.

## 2. Who owns what

| Agent | Owns | Will not do |
|---|---|---|
| **Product Owner** | Product vision/goals, backlog ordering, acceptance criteria, sprint review + release PR | Prescribe implementation details, implement code |
| **Scrum Master** | Facilitation, impediment log, retro actions, budget tracking/process optimization | Decide product scope, decide technical solutions |
| **Dev Team** | Implementation plan, estimates, sprint backlog delivery, PRs for the work itself | Reorder the backlog, accept work that fails the Definition of Done |
| **QA** | Test strategy, PR quality review | Become a blocking bottleneck — quality is shared |
| **Architect** | Architectural risk/tradeoff calls, ADRs | (advisory role) |

Route your message by intent: "should we do X before Y" → Product Owner. "the team
keeps getting interrupted" → Scrum Master. "how should we implement X" → Dev Team.
You can also just talk to the Orchestrator in plain language — it does the routing.

## 3. First run: the Setup Wizard

The first time you talk to the Orchestrator in a fresh session, it runs a setup
conversation rather than jumping into sprint work:

1. It checks `repo_status` — has a target repo, GitHub identity, and budget already
   been configured (via `.env` or a prior session)?
2. If not, it asks for: your target repo URL, a local clone path, and the default
   branch.
3. It asks how the agents should authenticate to GitHub — a **personal account**
   (commits/PRs show as you) or a **GitHub App** (commits/PRs show as a distinct
   "Agent" identity). See [GitHub Integration](docs/GITHUB-INTEGRATION.md#github-integration)
   for how to set either up in `.env`.
4. It seeds your target repo (README, `specs/` structure, templates) and creates
   per-agent LiteLLM virtual keys so spend is tracked and budget-capped per role.
5. It initializes and persists `ScrumState` to your target repo.

You can pre-answer most of this non-interactively by filling in `.env` before your
first run (`GITHUB_REPO_URL`, `STATE_REPO_PATH`, `GITHUB_TOKEN` or the `GITHUB_APP_*`
trio, `SPRINT_TOKEN_BUDGET`, `TOTAL_USD_BUDGET`) — the wizard will detect what's
already set and skip asking for it.

**If a `.hc/state.json` already exists** in your target repo (a second session, or a
teammate's prior run), the Orchestrator loads it and aligns with existing artifacts
instead of starting over.

## 4. Running a sprint

**On your first message of a fresh (or resumed) session** — a real user turn (even a bare "Hi") is
still required; ADK itself has no mechanism to invoke the agent before you send anything at all, in
either the web UI or `run.py cli` (GH issue #92) - the Orchestrator's reply is a rich, state-aware
greeting rather than a generic response. That greeting includes a status recap (sprint/budget,
product vision, sprint report, open impediments/retro actions, stories ready for the next pipeline
stage) and, once setup is complete, 2-5 concrete next-action options picked from what's actually
relevant right now - not a generic checklist every time. Typical options: resume an interrupted
sprint, discuss an open impediment, implement a logged retro action, refine stories that are ready
for the next stage, start a new sprint, or work on the product vision/roadmap. Pick one (or say
something else entirely) and the Orchestrator hands off to the right role immediately.

A sprint always follows the same shape:

1. **Planning.** Tell the Product Owner what you want built (or point it at existing
   `specs/stories/*.md`). It writes/refines epics and stories (`upsert_epic`,
   `upsert_story`) and keeps `specs/ROADMAP.md` in sync (`update_roadmap`).
2. **Human approval.** A sprint cannot start without you explicitly approving the
   sprint goal and sprint backlog — this is a hard rule, not a suggestion the
   Orchestrator can skip.
3. **Delivery.** The Dev Team breaks stories into tasks, estimates them in tokens,
   implements on a feature branch, and opens a PR (`git_push` → `gh_pr_create` →
   `gh_pr_checks`) — **your configured default branch (`main` unless you set otherwise)
   is protected; agents cannot push to it directly.**
4. **Review.** QA and the Architect comment on / review the PR
   (`gh_pr_comment`/`gh_pr_review`); their comments are auto-prefixed with their
   role so you can tell who said what.
5. **Sprint review.** The Product Owner generates `SPRINT-REPORT-LATEST.md`
   (`create_sprint_report`) — summary, accomplishments, budget/usage, and real
   quality KPIs (test coverage, complexity, vulnerability scan — see
   [§6](#6-trusting-the-numbers-real-kpis)).
6. **Release.** The Product Owner opens the release PR (`create_release_pr`) for the
   whole sprint's increment. This is checked against what the sprint actually
   touched before it opens (see [§7](#7-releasing-your-product)) — human review is
   still required before merge.
7. **Retrospective.** The Scrum Master proposes up to 3 retro actions, each with an
   owner and a success metric, logged for the next sprint.

The sprint automatically stops for review if the token or USD budget is exhausted,
or if you hit a persistent LLM provider rate limit — that's a deliberate guardrail,
not a bug.

## 5. Budgets

Two independent limits, different scopes, enforced two different ways (see docs/BUDGET.md):

- **Token budget** (`SPRINT_TOKEN_BUDGET`, default 1,000,000) — **per sprint**, resets
  automatically at the start of every new sprint. A logical sprint quota enforced
  locally, zero-latency, by the ADK framework. Prevents a runaway agent conversation
  within one sprint regardless of dollar cost, but does not by itself cap what a whole
  multi-sprint engagement could spend.
- **USD budget** (`TOTAL_USD_BUDGET`, default $10.00 if unset — but the Scrum
  Master's hard guardrail requires an explicit non-zero value before a sprint
  starts) — **whole engagement**, never resets on its own. Enforced by your LiteLLM
  proxy against the `scrum-sprint-budget` object. This is the authority on real spend,
  and the real safety net against unexpected cloud costs across many sprints (older
  name `SPRINT_USD_BUDGET` is still honored if you haven't renamed it yet).

Check live status any time with `get_budget_status`, or the LiteLLM admin UI at
`http://localhost:4000/ui`. Per-agent spend is trackable there too, since each
agent role gets its own virtual key.

**No agent can spend on an unscoped key.** Every specialist agent is blocked from
making any LLM call at all until it has its own `scrum-sprint-budget`-attached
virtual key (`create_litellm_virtual_key`) — this is enforced in code
(`check_cost_budget_callback`), not just a prompt instruction the model could skip.
Only the Orchestrator is exempt, since it needs one bootstrap call to create
everyone else's key in the first place. If you ever see a `🚫 [NO BUDGET-CAPPED
KEY]` response, the fix is exactly what it says: create that agent's virtual key
before delegating to it again.

**If you wipe the LiteLLM database** (`docker compose down -v`), old virtual keys
break. Recovery: temporarily set `LITELLM_PROXY_API_KEY` to your
`LITELLM_MASTER_KEY` in `.env` so the Orchestrator can start, run it once to
re-generate fresh virtual keys, then switch `.env` back to a real per-purpose key.
The guardrail above means every *other* agent stays blocked during this window —
only the Orchestrator's bootstrap call runs on the temporarily-unbounded key, and
only for as long as it takes to recreate everyone's keys.

**Running fully local (Ollama)?** The USD budget doesn't apply — a self-hosted
model has no real per-token price, so LiteLLM's spend for it is always ~$0
regardless of actual usage. `docker-compose.local.yaml` sets `LLM_LOCAL_PROVIDER=true`
so the system skips that check automatically rather than let it "pass" with false
confidence. The **token budget** (`SPRINT_TOKEN_BUDGET`) is your only real guardrail
in this mode — set it to whatever cap makes sense for your hardware/patience.

## 6. Trusting the numbers: real KPIs

`calculate_kpis()` reports on:
- **Test coverage** — from actually running your test suite (`pytest --cov`).
- **Code complexity** — from a real static-analysis pass (`radon`, Python only
  today).
- **Vulnerability scan results** — from a real scan (`bandit`, Python only today).

If a metric can't be computed for your repo (unsupported language, tool not
installed, etc.), it's reported as explicitly unavailable with a note — never
silently replaced with a fabricated number. That's a deliberate design decision
carried through the whole KPI pipeline (see `specs/stories/US-0005`–`US-0008`).

## 7. Releasing *your* product

Don't confuse this with [RELEASE.md](RELEASE.md), which is how *Horseless
Carriage itself* is released — this section is how the agents release the product
*they're building for you*.

- Every file the team writes during a sprint (via `upsert_prd`, `upsert_story`,
  `update_roadmap`, etc.) is tracked in `ScrumState.sprint_files_touched`.
- `create_release_pr()` diffs that tracked list against the real
  `git status` output before pushing: files tracked but missing from the diff, or
  present in the diff but untracked, are both surfaced as warnings.
- Only files that are both dirty *and* tracked get auto-staged. Anything else
  dirty in the working tree — a stray local edit unrelated to the sprint — is left
  unstaged and flagged for you to look at, rather than swept into the release.
- `gh_release_create(tag, ...)` cuts the actual GitHub Release once the PR is
  merged.

## 8. Troubleshooting

Troubleshooting content now lives in its own dedicated reference:
**[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — a symptom → cause → fix guide covering setup/first-run
problems (`doctor.py`, `setup_all.py`, `check_state_repo.py`, GitHub auth warnings), Docker/container
problems, budget/cost problems (exhausted budgets, LiteLLM database wipes, rate limits), mid-session
agent-behavior problems (the stall banner, the known raw-JSON-shaped-reply issue), and GitHub
integration problems. Start with `python3 doctor.py` for almost anything that looks wrong — see
[§3](#3-first-run-the-setup-wizard) above and
[TROUBLESHOOTING.md §1](TROUBLESHOOTING.md#1-setup--first-run-problems).

## 9. Team performance evaluation

Separate from using Horseless Carriage on your own project, the tool also
evaluates *itself*: how well the agent team performs against a fixed, known
scenario (a small to-do-list web app), so regressions or improvements in team
behavior surface release over release instead of only being noticed
anecdotally when something feels off. Full mechanics are in
[RELEASE.md "Team performance evaluation"](RELEASE.md#team-performance-evaluation)
— this section is the practical "how do I actually use this" version.

### Reading a result

Each run produces a report (`EVAL-REPORT.md`, committed to a branch on the
[eval repo](https://github.com/CI-Till-Krempel/horseless-carriage-eval-todo-app)
and uploaded as a CI artifact) with three scored dimensions (code quality,
requirements quality, team efficiency, each 1–5) and a ranked list of top
problems with suggested fixes. Read the **methodology note** at the top first
— the harness auto-merges PRs and pre-approves sprint goals to run unattended,
so a report reflects the team's behavior under those simplified conditions,
not full human-reviewed production usage.

### Triggering a real run

- **Automatically**: every `v*.*.*` tag push runs it alongside the real
  release.
- **Manually**: `workflow_dispatch` on `.github/workflows/eval.yml` (Actions
  tab → "Team Performance Evaluation" → "Run workflow"), for any branch — use
  this to check a feature branch's effect on team behavior before merging it.
- Either way, the run pauses for a maintainer's explicit approval before
  anything executes (real LLM spend) — see the `eval-approval` environment
  under repo Settings.

### Running it locally

Useful for iterating on the harness itself, or sanity-checking a change
before pushing it. This reuses your existing local setup — no extra tooling
needed beyond what `docker-compose.yaml` already provides.

1. Make sure your `.env` has real `GOOGLE_API_KEY`/`LITELLM_MASTER_KEY`, and a
   GitHub auth method with push/PR access to the eval repo specifically (see
   RELEASE.md's "Required secrets" note about GitHub App installations not
   automatically covering new repos).
2. Bring up the dependency services:
   ```bash
   docker compose --env-file .env up -d db litellm
   ```
3. Run a small, cheap sprint (start with 1 sprint and a modest budget, not the
   full 5 — a full run is meant for CI, not rapid local iteration):
   ```bash
   docker compose --env-file .env run --rm agent python3 -m agents.scrum_team.scripts.run_eval \
     --sprints 1 --run-id local-test-1 \
     --token-budget 300000 --usd-budget 3.0 --max-duration-minutes 10 \
     --local-path /app/eval-output/clone --report-path /app/eval-output/manifest.json
   ```
   Results land in `./eval-output/` on your host (bind-mounted in
   `docker-compose.yaml`) — `clone/` is the eval repo's working copy,
   `manifest.json` is the raw run data.
4. Run the analysis to get a human-readable report:
   ```bash
   docker compose --env-file .env run --rm agent python3 -m agents.scrum_team.scripts.run_eval_analysis \
     --manifest /app/eval-output/manifest.json --repo-path /app/eval-output/clone \
     --report-path /app/eval-output/report.md
   ```
   Add `--run-id local-test-1 --base-branch eval/local-test-1` to also open
   (and self-merge) a PR adding the report to the eval repo, matching what CI
   does — skip this for quick local iteration unless you want it recorded
   there.
5. Clean up afterward — a local run creates a real branch (and possibly PRs)
   on the public eval repo:
   ```bash
   gh pr list --repo CI-Till-Krempel/horseless-carriage-eval-todo-app --state open
   # close/delete-branch any PRs your run opened, then:
   gh api -X DELETE repos/CI-Till-Krempel/horseless-carriage-eval-todo-app/git/refs/heads/eval/local-test-1
   docker compose --env-file .env down db litellm -v
   rm -rf eval-output
   ```

## 10. Where to go next

- [README.md](README.md) — project overview and links to every doc below.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture diagram, story-workflow pipeline.
- [docs/GITHUB-INTEGRATION.md](docs/GITHUB-INTEGRATION.md) — GitHub setup options, agent identity.
- [RELEASE.md](RELEASE.md) — how Horseless Carriage itself is versioned/released,
  and how its own team-performance evaluation harness works.
- [SECURITY.md](SECURITY.md) — secret handling, what's never persisted, how to
  report a vulnerability.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — symptom → cause → fix reference for setup, Docker,
  budget, mid-session agent-behavior, and GitHub integration problems.
- [FAQ.md](FAQ.md) — short answers to the questions a prospective or new user is likely to have.
- `spec-templates/` — the actual templates the agents fill in (PRD, SRS, ADR, user
  story, roadmap, announcement, user guide).
- `specs/ROADMAP.md` (in your state repo) — what's shipped and what's planned.
