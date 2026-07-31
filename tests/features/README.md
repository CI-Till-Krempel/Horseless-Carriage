# Acceptance-test feature files (Gherkin)

**Status: documentation-grade, not yet wired to a runner.** `pytest-bdd` is
**not** installed or configured in this project (see `requirements*.txt` /
`pyproject.toml`) - these `.feature` files are not executable today. They
exist to document what "correct" looks like for the *already-implemented*
v0.1.0 feature set, precisely enough that a future pass can wire up real
step definitions without having to re-derive the behavior from scratch.

This is consistent with two things this project already does:

- **docs/ARCHITECTURE.md's stated principle** - "process is enforced in code,
  not just in the prompt." These files are the acceptance-criteria mirror of
  that principle for the *test* side: a scenario here documents a mechanical
  gate the code enforces, with a `file:line` citation into the actual
  enforcing code, so a regression in that gate has a written acceptance
  criterion to fail against.
- **`qa/0.1.0-testplan.md`'s existing manual-test-case style** - a human
  walkthrough of exactly the things that need a live Docker Desktop, a real
  LLM, or a real GitHub push/PR to verify. The `@manual-qa` tag below is the
  same category of scenario, just expressed as Gherkin instead of a markdown
  table row.

## Directory purpose

One `.feature` file per feature area (see the file list below). Each file
has a `Feature:` description, a `Background:` when there's shared setup, and
several `Scenario:` (or `Scenario Outline:`) blocks. Every scenario has a `#`
comment directly above it citing the exact file(s) and line range(s) in this
repository that the scenario's behavior comes from - use that to find the
real implementation (and any existing unit test already covering it) before
writing a step definition.

## Tagging convention

- **`@automatable`** - pure host-side logic: a pytest function, a pure
  Python function/callback, or a tool call against a mocked `tool_context`/
  `MagicMock()` - no Docker, no live LLM, no real GitHub API call needed.
  These are the scenarios a future step-definition pass could realistically
  implement using the exact same mocking patterns already used by:
  - `tests/test_doctor.py`, `tests/test_check_state_repo.py`,
    `tests/test_setup_all.py`, `tests/test_run.py` (host-script suite -
    `monkeypatch.setattr` over `shutil.which`/`subprocess.run`, a `tmp_path`
    fixture standing in for a repo root)
  - `agents/scrum_team/tests/test_budget.py`,
    `agents/scrum_team/tests/test_requirements.py`,
    `agents/scrum_team/tests/test_notifications.py`,
    `agents/scrum_team/tests/test_scrum.py`,
    `agents/scrum_team/tests/test_github.py` (agent-tools suite -
    `tool_context = MagicMock(); tool_context.state = ScrumState().model_dump()`,
    then call the tool function directly and assert on the returned dict /
    mutated state)
- **`@manual-qa`** - needs a live Docker daemon, a real LLM behind the
  LiteLLM proxy, a real GitHub push/PR, or real GPU hardware - the same
  category of scenario `qa/0.1.0-testplan.md` already covers as a manual
  test-plan row. These are not good candidates for pytest-bdd + mocks; they
  stay manual (or become an integration-test-with-real-services suite later,
  which is a separate, bigger decision than this pass makes).

A handful of scenarios are genuinely borderline (e.g. "GPU acceleration
confirmed" is `@automatable` when the check is just parsing a canned
`docker compose logs` string, but the underlying "does the GPU actually get
used" question is `@manual-qa` since it needs real hardware) - each such
scenario says so explicitly in its own citation comment.

## Files in this directory

| File | Feature area |
|---|---|
| `guided-setup-wizard.feature` | `setup_llm.py`/`setup_all.py`/`setup_project.py` |
| `doctor-gate.feature` | `doctor.py`'s punch-list gate |
| `run-modes.feature` | `run.py`'s web/cli/daemon/dev modes |
| `human-interaction-levels.feature` | `INTERACTION_LEVEL` (Product/Stakeholder/CEO/EVAL) |
| `budget-enforcement.feature` | `check_cost_budget_callback` (token + USD) |
| `state-repository-recovery.feature` | state.json corruption detection/recovery |
| `notifications.feature` | `record_blocking_interaction` + `Notifier` plugins |
| `github-integration.feature` | auth, protected-branch guard, access checks |
| `sprint-story-workflow.feature` | `advance_story_stage`'s 5-stage pipeline |
| `team-performance-eval-harness.feature` | `run_eval.py` |
| `gpu-support-toggle.feature` | `OLLAMA_GPU_ENABLED` detection/toggle |
| `ctrl-c-stop-handling.feature` | Ctrl-C handling across `run.py`'s modes |
| `transcript-and-report-detail.feature` | transcript capture + report detail tiers |

## What this pass deliberately did NOT do

- Did not add `pytest-bdd` (or any other dependency) to the project - the
  constraint for this pass was documentation-only.
- Did not invent behavior for anything unclear in the code - every scenario
  was written after reading the cited source (and, where one exists, the
  matching unit test) directly; a couple of places where the code disagreed
  with an initial assumption are called out as explicit "Deviation" notes
  inside the relevant `.feature` file (see `sprint-story-workflow.feature`'s
  header note on the pipeline actually having 5 stages, not 6, and
  `human-interaction-levels.feature`'s closing note on `INTERACTION_LEVEL`
  having no session-state mirror).
- Did not attempt one-to-one parity with `qa/0.1.0-testplan.md`'s manual
  test IDs (TS1-01 etc.) - some `@manual-qa` scenarios here overlap with a
  testplan row; that's intentional (the same real-world check, expressed as
  a Gherkin scenario with a code citation instead of a markdown table row),
  not a second, competing source of truth.

## Wiring this up for real, later

If/when a future pass wants these executable: add `pytest-bdd` to the dev
dependencies, add step definitions under (e.g.) `tests/features/steps/` that
reuse the mocking fixtures already in `tests/test_doctor.py` /
`agents/scrum_team/tests/conftest.py`, and point `pytest-bdd`'s feature-file
discovery at this directory. The `@automatable` tag on each scenario is
exactly the filter such a pass would use to decide what's in scope first.
