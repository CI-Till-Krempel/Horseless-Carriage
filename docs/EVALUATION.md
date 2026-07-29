[← Back to README](../README.md)

# Evaluation

Separate from using Horseless Carriage to run *your own* project, the tool also
evaluates *itself*: how well the agent team performs against a fixed, known
scenario (a small to-do-list web app), so regressions or improvements in team
behavior surface release over release instead of only being noticed
anecdotally when something feels off.

This page is a short overview; the full guides live in two places depending on
what you need:

- **["Team performance evaluation" in MANUAL.md](../MANUAL.md#9-team-performance-evaluation)**
  — the practical "how do I actually use this" guide: reading a result,
  triggering a run (CI or manual), and running it locally to sanity-check a
  change before pushing it.
- **["Team performance evaluation" in RELEASE.md](../RELEASE.md#team-performance-evaluation)**
  — the full technical mechanics: the isolated eval repo, the `run_eval.py`
  driver, budget/timeout guardrails, auto-merge behavior, transcript capture,
  and the judge-LLM analysis/report step.

## The short version

- **Fixed scenario**: `eval/scenario/PRODUCT-VISION.md` — a deliberately
  narrow to-do-list-web-app product vision, byte-identical across runs so
  results are comparable across versions.
- **No human in the loop**: the harness sets [`INTERACTION_LEVEL=EVAL`](SETUP.md#human-interaction-levels)
  and runs the team through 5 sprints headlessly against an isolated public
  eval repo, using the cheap `scrum-eval-cheap` model alias (see the active
  `litellm.yaml` / [Setup](SETUP.md)) and its own [budget guardrails](BUDGET.md)
  (`EVAL_SPRINT_TOKEN_BUDGET`/`EVAL_USD_BUDGET_PER_SPRINT`).
- **Local (non-CI) runs** check the LiteLLM proxy is actually reachable before
  spending anything, and refuse to proceed without an explicit `--dev-mode`
  flag if it isn't (since the USD guardrail lives entirely in the proxy).
- **Output**: a scored report (`EVAL-REPORT.md`, 1-5 on code quality,
  requirements quality, and team efficiency) plus a ranked list of top
  problems with suggested fixes, committed to the eval repo and uploaded as a
  CI artifact.
- **Triggers**: automatically on every `v*.*.*` release tag, or manually via
  `workflow_dispatch` on `.github/workflows/eval.yml` for any branch - useful
  for checking a feature branch's effect on team behavior before merging it.
  Either way, a maintainer must explicitly approve the run before anything
  executes (real LLM spend).

For the exact commands to run this locally, see
[MANUAL.md § Running it locally](../MANUAL.md#running-it-locally).
