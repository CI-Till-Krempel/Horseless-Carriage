# AGENTS.md

Instructions for any AI coding agent (Claude Code, Codex, Copilot, etc.) working on **this**
repository - Horseless Carriage itself, the tool/framework. This is *not* about the simulated
Scrum team's own workflow for the *target* repos it manages (that's
[docs/DEVELOPMENT-WORKFLOW.md](docs/DEVELOPMENT-WORKFLOW.md)) - though, fittingly, this repo
develops itself the same way it asks its agents to develop other projects: specs before code,
mechanical gates over prompt-only discipline, PRs over direct pushes.

Read [README.md](README.md) and [RELEASE.md](RELEASE.md) first for the project shape and release
process; this file is the operational checklist for making a change.

## 1. Specify the change before coding it

Every non-trivial change traces back to one of:
- An existing **GitHub issue** (`gh issue list`/`gh issue view`).
- A **spec-issue**: `specs/requirements/SPEC-ISSUE-NNNN-Short-Slug.md`, started from
  [spec-templates/requirements/TEMPLATE-ISSUE.md](spec-templates/requirements/TEMPLATE-ISSUE.md) -
  never edit the template in place. Find the next free ID with
  `ls specs/requirements/ | grep -oE 'SPEC-ISSUE-[0-9]+' | sort -u | tail -1`. Use this for a
  concrete, code-level gap (a process rule nobody actually enforces, a bug with a confirmed
  mechanism) - see `specs/requirements/SPEC-ISSUE-0060`-`0068` for the expected evidence density:
  quoted error strings, `file:line` citations, not "the model seemed confused."
- A **user story**: `specs/stories/US-NNNN-*.md` for new capability, same template pattern
  (`spec-templates/stories/`).

**`SPEC-ISSUE-NNNN` and GitHub's `#NNN` are two independent numbering systems** - they never line
up by coincidence, and conflating them is exactly the confusion `SPEC-ISSUE-` (vs the old bare
`ISSUE-`) was renamed to avoid (PR #463). When a spec-issue and a GitHub issue both exist for the
same gap, cross-link them explicitly in both directions:
- In the spec-issue doc's `## Notes`, reference the GitHub issue by number (`GH issue #NNN`) - this
  codebase already does this constantly (`GH #220`, `GH #395`, `GH issue #246`, ...).
- In the GitHub issue, comment/link the spec-issue's file path once it exists.

If you're investigating a Team Performance Evaluation run, use the
`investigate-eval-run` skill/process (`.claude/skills/investigate-eval-run/`) - it produces exactly
this kind of spec-issue and a matching `RELEASE.md` entry.

## 2. Branch and PR — never push directly to `main`

- Branch from `main` (`feature/<slug>`, `fix/<slug>`, `fix-NNN-<slug>`, `investigate-eval-runNN-...`
  are all patterns already in use - match the existing style for the branch's purpose).
- `main` is not GitHub-branch-protected in this repo today - that makes it a **convention you must
  enforce yourself**, not a safety net that catches you. Never `git push origin main` directly,
  even for a one-line fix.
- `develop` exists in this repo's history (`RELEASE.md` "Branching model — GitFlow") as the
  eventual integration branch once a second release is underway, but it has been stale since
  v0.1.0 shipped - every PR since has still targeted `main` directly. Match current practice: open
  PRs against `main` unless you've confirmed `develop` is active again (check
  `git log origin/develop -1` against `git log origin/main -1`).
- Every PR must link back to what it implements (issue, spec-issue, or story) - see
  [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)'s "Related Issues" section.
  Reference the spec-issue/story path in the PR body even though GitHub won't auto-link it; use a
  GitHub closing keyword for any GitHub issue it resolves (next section).

## 3. Link and close issues properly

If a PR fixes an **open GitHub issue**, use a real closing keyword in the PR description
(`Closes #NNN`, `Fixes #NNN`, `Resolves #NNN`) so merging the PR auto-closes it - don't just mention
the number in prose. If it only *relates to* an issue without fully resolving it, say so explicitly
("Part of #NNN", "Related to #NNN") rather than using a closing keyword.

## 4. Keep cross-references intact

This repo's specs, `RELEASE.md`, source comments, and tests form a dense cross-reference web
(`SPEC-ISSUE-0060's resolution note anticipated...`, `GH #395: absorbed from...`). When you rename,
renumber, or move anything with an ID:
- Grep for every occurrence of the old ID across `*.md` and `*.py` before considering the rename
  done - `grep -rn "SPEC-ISSUE-0066" --include="*.py" --include="*.md" .` (adjust the pattern).
  A merge/rebase conflict is the most common way this slips: resolving a conflict by picking "the
  newer file's version" can silently drop a reference the other side added.
- A GitHub issue number is permanent once assigned - never reuse it, even if the issue is closed as
  wontfix. A `SPEC-ISSUE-NNNN` ID is likewise permanent once a doc exists at that path - if the fix
  turns out to need a different shape, add a new entry (`## Resolution`) to the existing doc; don't
  delete and renumber.
- When rebasing a branch whose own spec-issue ID collides with one that landed on `main` in the
  meantime (two people picked the same next-free number), rename yours to the next free ID *and*
  fix every internal cross-reference inside that doc and every file it touches - don't just rename
  the filename.

## 5. Never commit API keys or secrets

- All credentials live in `.env` (git-ignored - see `.gitignore`), never in code, YAML, or specs.
  `.env.example`/`.env.local.example` document every variable name with placeholder values only.
- See [SECURITY.md](SECURITY.md) "Secret handling" for exactly what's stored where and why (LLM
  provider keys, `GITHUB_TOKEN`/`GITHUB_APP_PRIVATE_KEY`, `LITELLM_MASTER_KEY`,
  `POSTGRES_PASSWORD`) - none of these are ever persisted to a target repo's own state.
- Before committing, re-check `git status`/`git diff` for anything that looks like a real key,
  token, or connection string - especially in test fixtures, `.env.test`-like files, or copy-pasted
  CI log output. If you ever do commit one, treat it as already compromised: rotate it, don't just
  delete the commit.
- The PR template's checklist includes "No secrets included" - don't check it reflexively, actually
  look.

## 6. Update documentation alongside code

A code change without the matching doc update is incomplete, not "a follow-up." Depending on what
changed, update:
- [RELEASE.md](RELEASE.md) - add a new `### <heading>` entry when the change came from a real eval
  run or fixes a user-visible behavior (match the style/evidence density of existing entries).
- The relevant page under [docs/](docs/) (`ARCHITECTURE.md`, `DEVELOPMENT-WORKFLOW.md`,
  `BUDGET.md`, `CONFIGURATION.md`, `GITHUB-INTEGRATION.md`, `TESTING.md`, etc.) - these are the
  source of truth referenced from `README.md`'s doc table; keep that table and its links valid.
- `specs/ROADMAP.md`, the story/epic markdown under `specs/stories/`, or the spec-issue's own
  `## Resolution` section, if the change closes out tracked work.
- `README.md`/`MANUAL.md`/`FAQ.md`/`TROUBLESHOOTING.md` for anything user-facing (new env var, new
  CLI flag, new failure mode and its fix).

## 7. Tests: keep them green, extend them

- `python3 run_tests.py` runs everything: the host-script suite (`tests/`, no Docker needed) then
  the full agent suite (`agents/scrum_team/tests/`, via `docker compose run` - requires Docker
  running). See [docs/TESTING.md](docs/TESTING.md).
- CI (`.github/workflows/ci.yml`) runs on every push and PR - don't open a PR you haven't run
  `run_tests.py` against locally first.
- A behavior change with no new/updated test is not done. If you fixed a bug, there should be a
  test that failed before the fix and passes after (the spec-issue template literally asks for
  this under "Acceptance Criteria" / "Test Approach").
- `agents/scrum_team/tests/test_story_pipeline_state_machine.py` and
  `test_sprint_and_approval_gates.py` read `docs/DEVELOPMENT-WORKFLOW.md`'s diagrams as a literal
  state machine - if you change a gate's behavior, these are very likely the tests to extend.

### Docker hygiene when running the containerized suite

- **Always run the agent test suite through `python3 run_tests.py`**, not a bare
  `docker compose run --rm agent ...`. `run_tests.py` already scopes itself to a distinct Compose
  project name (`lib_docker.compose_project_args("test")` → `-p horseless-carriage-test`, GH issue
  #169) and `--env-file .env.test` - with no explicit project name, Compose derives one from the
  checkout directory alone, which collides with the real dev stack's project name and therefore its
  container names (`horseless-carriage-db-1`, etc.) and **its persistent `postgres_data` volume**. A
  bare `docker compose run` for "just running tests" can silently restart/attach to containers that
  hold real project data, not a disposable test sandbox.
- If you need to invoke `docker compose`/pytest manually inside the container for faster iteration
  (not the full `run_tests.py`), pass the same project-scoping flag yourself
  (`docker compose -p horseless-carriage-test ...`, or reuse `lib_docker.compose_project_args("test")`
  from a script) - never the bare default project name.
- **Clean up what you started, every time.** After a manual/ad-hoc test run, bring down only the
  test-scoped project you created: `docker compose -p horseless-carriage-test down` (no `-v`). Don't
  leave dangling test containers running past the task that needed them.
- **Never remove another project's containers, volumes, or images without the user's explicit
  approval** - the default/dev-stack project, the eval harness's project (`-eval`), or anything you
  didn't start this session may hold real project data (a real dev Postgres volume, an in-progress
  session). In particular: never pass `-v`/`--volumes` to `docker compose down` for any project
  unless the user has explicitly asked for that data to be deleted - a volume predating your session
  is a strong signal it isn't yours to remove.

## 8. Run the ADK eval regularly

`run_adk_eval.py` runs a small, cheap set of scripted gate-enforcement conversations
(`eval/adk/scrum_team.evalset.json`) against a live model - protected-branch push refusal, stage
skipping, missing-approval blocks, etc. - inside the real agent container. It's cheap enough to run
locally before opening any PR that touches `agents/scrum_team/` (tools, prompts, gates, agent.py):

```bash
python3 run_adk_eval.py          # uses your own .env-configured provider
```

It also runs automatically on every `v*.*.*` release tag and is available via `workflow_dispatch`
on `.github/workflows/adk-eval.yml` for any branch - use the manual trigger to sanity-check a
feature branch's effect on gate-enforcement behavior before merging, not just at release time. See
[docs/EVALUATION.md](docs/EVALUATION.md) and `eval/adk/README.md`.

## 9. The Team Performance Evaluation is maintainer-only

`.github/workflows/eval.yml` ("Team Performance Evaluation") runs the full 5-sprint simulated team
against a throwaway target repo - real LLM spend, up to an hour of runtime. It is gated behind the
`eval-approval` GitHub Environment with required reviewers: **whether triggered by a release tag or
by `workflow_dispatch`, the job pauses until a maintainer explicitly approves it** (repo Settings →
Environments → `eval-approval`). Do not attempt to bypass, remove, or weaken this approval gate.
Do not trigger it yourself as an agent unless the maintainer has explicitly asked for this specific
run - this is different from the ADK eval above, which is cheap and self-service. See RELEASE.md
"Team performance evaluation" for the full mechanics.

## 10. Breaking changes need an automated migration

Per `RELEASE.md`'s versioning rules, a **MAJOR**-class change (breaking agent tool contracts,
`ScrumState` shape, or CLI/env interface) must not leave an existing installation's state repo
broken. Use the migration scaffold that already exists for exactly this:
- `agents/scrum_team/tools/migrations.py`'s `MIGRATIONS` list - add an entry
  `(target_version, migrate_fn)` that transforms the old state shape into the new one.
- This is wired into `load_state_from_repo()` (`agents/scrum_team/tools/scrum.py`), which compares
  a loaded state's recorded `hc_version` against the running version and applies every migration
  newer than it, in order, before merging into session state.
- Write a test that constructs the *old* state shape, runs it through `migrate_state`, and asserts
  the result is valid under the *new* code - this is the test that would fail today (pre-migration)
  and pass once the migration is added.
- Do not ship a breaking `ScrumState`/tool-contract change without this - "users can just re-init"
  is not an acceptable migration path; existing installations' state repos must keep working.

## 11. Guardrails and budget mechanisms require human confirmation

Two categories of code are deliberately load-bearing for safety/cost control, not ordinary
refactor targets:
- **Guardrails**: `<Role>-guardrails.md` in `agents/scrum_team/prompt_modules/` (non-negotiable,
  never a customization target for any tool), plus the mechanical gates they back -
  `advance_story_stage`'s order/ownership/content checks, `blocks_direct_status_set`,
  `_git_push_impl`'s `protected_branches`, the transfer/repeated-call loop breakers in `agent.py`,
  `raise_story_blocker`/`resolve_story_blocker`'s evidence requirements, and anything else listed
  in [docs/DEVELOPMENT-WORKFLOW.md](docs/DEVELOPMENT-WORKFLOW.md) "Customization points."
- **Budget mechanisms**: everything in [docs/BUDGET.md](docs/BUDGET.md) -
  `check_cost_budget_callback`, `SPRINT_TOKEN_BUDGET`/`TOTAL_USD_BUDGET` enforcement,
  `NON_GRACE_FLOOR_ROLES`, `SPRINT_CLOSEOUT_GRACE_PERCENT`, ScrumMaster's separate ritual budget,
  and the LiteLLM-layer USD ceiling.

You may **propose** a change here (open a spec-issue describing the gap, draft the PR), but do not
land a change that loosens, removes, or silently reinterprets a guardrail or budget limit without
explicit human sign-off in the PR itself - these exist because a prompt-only version of the same
rule was already shown not to hold under a cheap model's real behavior (that's the recurring
pattern across `SPEC-ISSUE-0050` through `0068`). A one-line "approved by <name>" comment from the
human reviewer on the specific gate/limit change is the expected form of that confirmation; don't
infer silent approval from a PR simply being merged.

## Quick reference

| Doing this... | Read first |
|---|---|
| Filing a new gap/bug for this repo itself | `spec-templates/requirements/TEMPLATE-ISSUE.md`, recent `specs/requirements/SPEC-ISSUE-006*.md` |
| Understanding the simulated team's own process (not this repo's) | [docs/DEVELOPMENT-WORKFLOW.md](docs/DEVELOPMENT-WORKFLOW.md) |
| Releasing a new version of this repo | [RELEASE.md](RELEASE.md) |
| Running tests | [docs/TESTING.md](docs/TESTING.md) |
| Running either eval | [docs/EVALUATION.md](docs/EVALUATION.md) |
| Budget/guardrail mechanics | [docs/BUDGET.md](docs/BUDGET.md), [docs/DEVELOPMENT-WORKFLOW.md](docs/DEVELOPMENT-WORKFLOW.md) "Customization points" |
| Secret handling | [SECURITY.md](SECURITY.md) |
