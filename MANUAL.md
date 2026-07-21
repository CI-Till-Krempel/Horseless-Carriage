# User Manual (Draft, v0.1.0)

> **Status: First draft**, written ahead of the v0.1.0 release. Corrections and
> expansions welcome — this covers the concepts and the happy path, not every edge
> case.

This is a task-oriented guide to actually using Horseless Carriage. For "what's in
this repo" and installation mechanics, see [README.md](README.md) and
[PREFLIGHT.md](PREFLIGHT.md) — this manual assumes setup is done and focuses on how
to work with the team day to day.

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
   "Agent" identity). See README.md ["GitHub Integration"](README.md#github-integration)
   for how to set either up in `.env`.
4. It seeds your target repo (README, `specs/` structure, templates) and creates
   per-agent LiteLLM virtual keys so spend is tracked and budget-capped per role.
5. It initializes and persists `ScrumState` to your target repo.

You can pre-answer most of this non-interactively by filling in `.env` before your
first run (`GITHUB_REPO_URL`, `STATE_REPO_PATH`, `GITHUB_TOKEN` or the `GITHUB_APP_*`
trio, `SPRINT_TOKEN_BUDGET`, `SPRINT_USD_BUDGET`) — the wizard will detect what's
already set and skip asking for it.

**If a `.hc/state.json` already exists** in your target repo (a second session, or a
teammate's prior run), the Orchestrator loads it and aligns with existing artifacts
instead of starting over.

## 4. Running a sprint

A sprint always follows the same shape:

1. **Planning.** Tell the Product Owner what you want built (or point it at existing
   `specs/stories/*.md`). It writes/refines epics and stories (`upsert_epic`,
   `upsert_story`) and keeps `specs/ROADMAP.md` in sync (`update_roadmap`).
2. **Human approval.** A sprint cannot start without you explicitly approving the
   sprint goal and sprint backlog — this is a hard rule, not a suggestion the
   Orchestrator can skip.
3. **Delivery.** The Dev Team breaks stories into tasks, estimates them in tokens,
   implements on a feature branch, and opens a PR (`git_push` → `gh_pr_create` →
   `gh_pr_checks`) — **`main` is protected; agents cannot push to it directly.**
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

Two independent limits, enforced two different ways:

- **Token budget** (`SPRINT_TOKEN_BUDGET`, default 1,000,000) — a logical sprint
  quota enforced locally, zero-latency, by the ADK framework. Prevents a runaway
  agent conversation regardless of dollar cost.
- **USD budget** (`SPRINT_USD_BUDGET`, default $10.00 if unset — but the Scrum
  Master's hard guardrail requires an explicit non-zero value before a sprint
  starts) — enforced by your LiteLLM proxy against the `scrum-sprint-budget` object.
  This is the authority on real spend.

Check live status any time with `get_budget_status`, or the LiteLLM admin UI at
`http://localhost:4000/ui`. Per-agent spend is trackable there too, since each
agent role gets its own virtual key.

**If you wipe the LiteLLM database** (`docker compose down -v`), old virtual keys
break. Recovery: temporarily set `LITELLM_PROXY_API_KEY` to your
`LITELLM_MASTER_KEY` in `.env` so the Orchestrator can start, run it once to
re-generate fresh virtual keys, then switch `.env` back to a real per-purpose key.

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

- **`./doctor.sh`** — checks Docker, `.env` completeness, `STATE_REPO_PATH`
  existence, and GitHub auth configuration. Run this first.
- **`./check_state_repo.sh`** — validates your target repo's `specs/` structure and
  `.hc/state.json`, and flags stray template files that shouldn't be there.
- **"GitHub tools may fail" warning on startup** — no `GITHUB_TOKEN` or complete
  `GITHUB_APP_*` trio configured. The container still starts (other tools work
  fine); fix `.env` and restart.
- **429 / rate-limit errors mid-sprint** — the Scrum Master should catch this and
  trigger a review, but if it doesn't, it usually means the model or the
  per-agent quota is too aggressive for your provider tier; lower it or switch
  `LOG_LEVEL=debug` temporarily to see request volume.
- **Old virtual keys stopped working** — see [§5](#5-budgets) recovery steps.

## 9. Where to go next

- [README.md](README.md) — architecture diagram, full env var reference, GitHub
  setup options.
- [RELEASE.md](RELEASE.md) — how Horseless Carriage itself is versioned/released.
- [SECURITY.md](SECURITY.md) — secret handling, what's never persisted, how to
  report a vulnerability.
- `spec-templates/` — the actual templates the agents fill in (PRD, SRS, ADR, user
  story, roadmap, announcement, user guide).
- `specs/ROADMAP.md` (in your state repo) — what's shipped and what's planned.
