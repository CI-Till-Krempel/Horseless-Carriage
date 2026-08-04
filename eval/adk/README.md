# ADK-native gate-enforcement eval set

## What this is

`scrum_team.evalset.json` is a [Google ADK](https://github.com/google/adk-python)
native `EvalSet` (`google.adk.evaluation.eval_set.EvalSet`) containing 10
`EvalCase` entries. Each case is a single scripted human instruction that,
if the live agent team behaves as the code mechanically enforces, should
produce a specific tool-call trajectory - most often "the agent attempts the
forbidden/ungated action, and the tool itself refuses it" (a protected-branch
push, skipping a story-pipeline stage, setting status directly, advancing
without a fresh human approval, running with no budget-capped virtual key,
closing a sprint report with no new retrospective signal).

`test_config.json` is the matching `EvalConfig`
(`google.adk.evaluation.eval_config.EvalConfig`), configuring one metric:
`tool_trajectory_avg_score` with `match_type: IN_ORDER` (see "A real,
load-bearing limitation" below for why `IN_ORDER`, not the default `EXACT`).

Both files were validated directly against the real, installed pydantic
models in this environment - see "Validation performed" below for the exact
commands and output.

## How this complements the existing scenario-based harness

`docs/EVALUATION.md` / `eval/scenario/PRODUCT-VISION.md` / `agents/
scrum_team/scripts/run_eval.py` already evaluate Horseless Carriage
end-to-end: 5 real sprints against a fixed to-do-app product vision, judged
by an LLM (`EVAL-REPORT.md`, 1-5 scores on code quality/requirements
quality/team efficiency) - a holistic, multi-sprint, real-Docker/real-LLM/
real-GitHub-push judgment of overall product/team quality.

This ADK evalset is deliberately much narrower and does NOT replace that:

| | `run_eval.py` (existing) | This ADK evalset (new) |
|---|---|---|
| Scope | Whole product across 5 sprints | One scripted instruction each |
| Judge | An LLM judge scoring 1-5 on quality | Exact tool-call trajectory match |
| What it catches | "Is the team's overall output/process good?" | "Did this ONE mechanically-enforced gate actually fire?" |
| Needs a live multi-sprint run? | Yes | No - one/few-turn conversations |
| Needs Docker/a real GitHub repo? | Yes | No (see "Known limitations" - can run against `InMemoryRunner`/a mocked session, in principle) |

Put simply: `run_eval.py` asks "is the team doing a good job building a
product end-to-end?" This evalset asks "when the team is nudged toward a
specific forbidden action, does the exact code-level gate we already unit-
test (see the file:line citations in `tests/features/*.feature`) actually
get exercised by a live model in a live conversation?" - a much smaller,
much cheaper, much faster check, meant to run far more often than a full
5-sprint eval.

## The command to run it for real

```bash
python3 run_adk_eval.py             # local: pinned Ollama model, dockerized (eval/adk/litellm.local.yaml)
python3 run_adk_eval.py --host-ollama  # same, but a native Ollama on this host (GPU-accelerated on macOS)
python3 run_adk_eval.py --ci        # cheap cloud model (eval/adk/litellm.ci.yaml) - see adk-eval.yml
python3 run_adk_eval.py --debug     # force LOG_LEVEL=debug for this run (off by default - floods the shell otherwise)
python3 run_adk_eval.py --dry-run   # print the underlying commands without running them
```

`run_adk_eval.py` is a thin wrapper (see the repo root) that brings up `db`+`litellm`+(`ollama` in
local mode) and runs the equivalent of:

```bash
adk eval eval/adk/agent/scrum_team eval/adk/scrum_team.evalset.json --config_file_path eval/adk/test_config.json
```

This was verified against this environment's actually-installed `adk` CLI
(`adk eval --help`, and directly exercising
`google.adk.cli.cli_eval.get_root_agent`) - see "Deviation: a loader shim was
required" below for why the path is `eval/adk/agent/scrum_team`, not
`agents/scrum_team` or `agents` as an initial reading of this task might
suggest.

## Reproducible model config - dedicated, not the dev stack's

A real run once failed outright with every call returning a canned
`[CONNECTION ERROR]` (0/10 passed): LiteLLM was relaying a genuine Ollama 404
because `config/model-templates/litellm.local-ollama.yaml` - freely rewritten
by `setup_llm.py` for whatever provider/model a developer's own dev stack is
currently configured for - had drifted out of sync with `.env`'s
`OLLAMA_MODEL`. Results from this eval set need to be comparable across
machines and runs, so it never depends on that shared, driftable config:

- **Local (default)**: `eval/adk/litellm.local.yaml`, every role pinned to
  Ollama's `llama3.1:8b` (tool-calling capable, runs on most machines) -
  `run_adk_eval.py` overrides `OLLAMA_MODEL` to the same tag when bringing
  the stack up, so `ollama` always pulls/serves exactly what this config
  expects, regardless of whatever model this developer's own `.env` last
  configured for day-to-day dev use. Runs against `docker-compose.local.yaml`
  (dockerized Ollama - CPU-only on Docker Desktop, see `--host-ollama`).
- **`--host-ollama`**: `eval/adk/litellm.local-hostollama.yaml`, same
  pinned `llama3.1:8b`, but talks to Ollama running natively on this host
  (`http://host.docker.internal:11434`) instead of a dockerized `ollama`
  service. Docker Desktop (macOS/Windows) has no GPU passthrough at all (GH
  issue #93), so a dockerized Ollama always runs CPU-only there, even on
  Apple Silicon - this is the only way a local eval run actually uses the
  GPU (Metal on macOS). Requires `ollama serve` already running on this
  host (same prerequisite as `docker-compose.local-hostollama.yaml`);
  `run_adk_eval.py` pulls the pinned model itself via the host's own
  `ollama` CLI if it isn't already present. Runs against
  `docker-compose.local-hostollama.yaml` (still dockerized db/litellm -
  only Ollama itself is native).
- **`--ci`**: `eval/adk/litellm.ci.yaml`, every role pointed at the same
  cheap Gemini model (`gemini-flash-lite-latest`) - `GOOGLE_API_KEY` is
  injected as a GitHub Actions repository secret (the same one `eval.yml`
  already uses - see `RELEASE.md`'s "Required secrets"). Runs against the
  cloud `docker-compose.yaml` (no Ollama). Used by
  `.github/workflows/adk-eval.yml` on every release tag.

None of these three files are ever touched by `setup_llm.py`. All three
compose files' `litellm` service now mount
`${LITELLM_CONFIG_PATH:-<previous default>}` instead of a hardcoded path,
so `run_adk_eval.py` can redirect the mount without changing anything about
the normal dev stack (`run.py`)'s behavior.

`run_adk_eval.py` also tears its stack down (`docker compose ... down`,
`-v` in `--ci` mode) once the eval finishes - success or failure - so a run
doesn't leave containers running indefinitely (`restart: unless-stopped`)
the way it previously did.

### A second failure mode: waiting for the model to actually be ready

Fixing the config mismatch above wasn't enough - the exact same
`[CONNECTION ERROR]`/"model not found" symptom persisted afterward, but now
as a **race**, not a mismatch: `ollama-entrypoint.sh` backgrounds `ollama
serve` (so the container starts accepting connections, and `docker compose
up -d` returns success) and only pulls the model as a separate step
*afterward* - which can take several minutes on a first run. Every request
sent during that pull window fails with the identical "model ... not found"
error, indistinguishable from a genuine connection problem; a real run saw
most of the 10 eval cases fail this way, with only the last one or two
succeeding once the pull happened to finish partway through.

`run_adk_eval.py` now polls two things before running the eval, in order:
1. LiteLLM's own `/health/readiness` (published on `localhost:4000`) -
   `up -d` returning success only means the container process started, not
   that LiteLLM has finished its own DB connection setup. Applies to all
   three modes - LiteLLM itself is always dockerized.
2. Local (default) mode only: `docker compose exec ollama ollama list`,
   until the pinned model actually appears - no host port is published for
   `ollama` (nothing needs to reach it from the host otherwise), so this
   execs into the container directly rather than hitting an API from the
   host. Skipped entirely in `--ci` mode (cloud model, no pull step, no
   `ollama` container at all) and in `--host-ollama` mode (see below - the
   model is already guaranteed present before this point, so there's
   nothing left to poll for).

`--host-ollama` avoids this race a different way: `ensure_host_ollama_ready`
checks the `ollama` CLI is present, a native Ollama instance is reachable at
`http://localhost:11434`, and the pinned model is present - pulling it
itself (foreground `ollama pull`, blocking until done) if not - all on the
host, *before* any docker compose command runs at all. Since nothing else
has started yet, there's nothing for that pull to race against, unlike the
dockerized case above.

## Deviation: a loader shim was required

The task brief for this pass described the root agent as
`agents.scrum_team.agent.root_agent`. That import path is correct - but `adk
eval`'s own agent-loading code
(`google.adk.cli.cli_eval._get_agent_module`, confirmed by reading the
installed package directly) is stricter than `adk web`/`adk run`:

- `adk web`/`adk run` use `google.adk.cli.utils.agent_loader.AgentLoader`,
  which does a real `importlib.import_module(agent_name)` (falling back to
  `importlib.import_module(f"{agent_name}.agent")`) - flexible enough that
  this project's real entrypoint, `agents/agent.py` (`from
  agents.scrum_team.agent import root_agent`) + `agents/__init__.py`, works
  fine as-is (this is exactly what `agents/scrum_team/scripts/run_agent.sh`
  invokes as `adk web ... agents` / `adk run ... agents`).
- `adk eval`'s loader (`_get_agent_module`) instead does
  `importlib.util.spec_from_file_location("agent", "<path>/__init__.py")`
  and then accesses `<that module>.agent.root_agent` - i.e. it requires
  `<path>/__init__.py` to itself do `from . import agent` (the standard ADK
  quickstart package shape). `agents/__init__.py` does not do this (it's just
  a docstring), and `agents/scrum_team/` has no `__init__.py` at all.

Verified directly in this environment:

```
>>> from google.adk.cli.cli_eval import get_root_agent
>>> get_root_agent('agents')
AttributeError: module 'agent' has no attribute 'agent'
```

Per this task's constraint ("do not modify any existing file outside
creating the new `tests/features/` and `eval/adk/` directories"),
`agents/__init__.py` could not be fixed directly. Instead, `eval/adk/agent/
scrum_team/` is a tiny, additive shim package (an `__init__.py` doing `from
. import agent`, and an `agent.py` that adds the repo root to `sys.path` and
re-exports the real `root_agent` unchanged) shaped exactly the way `adk
eval`'s loader expects - verified to resolve correctly:

```
>>> get_root_agent('eval/adk/agent/scrum_team')
# ... proceeds past module loading, fails only on the pre-existing
# agent.py:16 hardcoded "/app/sessions" log path (see below) - confirms the
# shim itself works.
```

Naming the shim directory `scrum_team` (not e.g. `agent_module`) also means
`adk eval`'s `app_name = os.path.basename(agent_module_file_path)` resolves
to `"scrum_team"` - matching every `session_input.app_name` in
`scrum_team.evalset.json`.

## Known limitation: `agent.py`'s hardcoded `/app/sessions` log path

`agents/scrum_team/agent.py`'s own `_setup_logging()` hardcodes
`/app/sessions` as its log directory (line 16) - correct inside the
project's Docker container, but not writable on a bare host. Running `adk
eval` (or the shim above) directly on a host machine outside the container
fails with `PermissionError: [Errno 1] Operation not permitted: '/app'`
before the agent even finishes importing - confirmed directly in this
environment. This is a pre-existing property of `agent.py`, out of scope to
fix in this pass (modifying it isn't part of this task's additive-only
constraint). Run the command above **inside the same container image the
project already builds** (e.g. `docker compose run --rm agent adk eval
eval/adk/agent/scrum_team ...`, or with `SESSION_ID`/a writable `/app`
bind-mounted), not directly on a bare host.

## Known limitation: `INTERACTION_LEVEL` has no session-state mirror

`get_interaction_level()` (`agents/scrum_team/helpers.py:82-93`) always reads
`INTERACTION_LEVEL` from the **process environment**, never from session
state, by explicit design (see that function's own docstring: "there's no
state field for this... so it can't drift from what's actually configured
for the running process"). This evalset's `session_input.state` therefore
cannot script which interaction level is active - the three cases that
depend on a specific level (`advance_story_stage_rejects_implemented_
without_sprint_approval`, `create_release_pr_rejects_without_release_
approval` - both assume the default "Product" level) require the process
`INTERACTION_LEVEL` to be unset or `"Product"` when `adk eval` runs. This is
noted here rather than faked as a state key, per the task's own instruction
to prefer an honest limitation over fabricating a state key that the code
doesn't actually read.

## A real, load-bearing limitation: exact tool-call argument matching

`tool_trajectory_avg_score` compares the *actual* tool-call trajectory a
live model produces against each case's scripted `intermediate_data.
tool_uses`, using **full dict equality on `args`** per call (confirmed by
reading `google.adk.evaluation.trajectory_evaluator.TrajectoryEvaluator`
directly) - even `IN_ORDER`/`ANY_ORDER` still require each matched call's
args to be byte-for-byte equal, not a subset match. This is fine for tools
whose only meaningful arguments are structured and deterministic given the
scenario (`git_push(branch=...)`, `advance_story_stage(title_or_id, stage)`)
- a live model calling these with exactly the scripted single/couple of
required arguments is realistic. It is much less reliable for tools whose
entire argument surface is free text the model composes itself
(`create_release_pr(title, body)`, `create_sprint_report(summary,
accomplishments)`, `upsert_story({"story": {...}})`) - a live run's real
`title`/`body`/`summary` text will essentially never match our scripted
placeholder text word-for-word, so those three cases
(`create_release_pr_rejects_without_release_approval`,
`create_sprint_report_rejects_without_new_retro_or_impediment`,
`upsert_story_blocks_direct_status_set`) should be expected to score 0 on
`tool_trajectory_avg_score` even when the underlying gate behaves perfectly
correctly. They are still included because they are precise, schema-valid
documentation of the expected trajectory (reusable if a future pass adds a
custom metric, or a rubric-based/LLM-judge criterion, that only checks the
tool *name* and the gate's `status` in the response rather than exact args) -
and because `--print_detailed_results` still surfaces the real vs. expected
trajectory side by side for manual review even when the automatic score is
0. The project's existing mocked unit tests
(`agents/scrum_team/tests/test_requirements.py`,
`agents/scrum_team/tests/test_github.py`,
`agents/scrum_team/tests/test_budget.py`) are what actually exact-match-test
these gates' Python-level enforcement logic today; this evalset checks
whether a *live model*, given a scripted instruction, exhibits the
gate-relevant tool-call *behavior* at all.

## These `EvalCase`s were hand-authored, not captured from a live run

No live LLM/Docker was available to record a real trace in this
environment, so every `tool_uses`/`tool_responses`/`final_response` value
here was written by hand from reading the enforcing code directly (see the
matching scenario/citation in `tests/features/*.feature`), not captured from
an actual `adk eval --print_detailed_results` run. Before relying on these
for real, run them once for real and adjust anything where the live shape
differs from what's scripted here (most likely: `transfer_to_agent`'s exact
response shape, and whether a sub-agent's blocked-key message bubbles back
through the Orchestrator as its own additional turn rather than being the
invocation's `final_response` directly).

## Validation performed

Both JSON files parse cleanly against the real, installed pydantic models
(not just directory-structure guesses) - run from the repo root:

```
$ source .venv/bin/activate && python3 -c "import json; from google.adk.evaluation.eval_set import EvalSet; print(EvalSet(**json.load(open('eval/adk/scrum_team.evalset.json'))))"
# ...(full EvalSet repr, ending in)... creation_timestamp=0.0)] creation_timestamp=0.0
# (no ValidationError - 10 eval_cases loaded)

$ source .venv/bin/activate && python3 -c "import json; from google.adk.evaluation.eval_config import EvalConfig; print(EvalConfig(**json.load(open('eval/adk/test_config.json'))))"
criteria={'tool_trajectory_avg_score': BaseCriterion(threshold=1.0, include_intermediate_responses_in_final=False, match_type='IN_ORDER')} custom_metrics=None user_simulator_config=None
```

Also verified through the actual CLI-facing loader functions (not just the
bare pydantic constructors), since `adk eval` itself calls these, not
`EvalSet(**...)`/`EvalConfig(**...)` directly:

```python
from google.adk.evaluation.local_eval_sets_manager import load_eval_set_from_file
from google.adk.evaluation.eval_config import get_evaluation_criteria_or_default
load_eval_set_from_file('eval/adk/scrum_team.evalset.json', 'eval/adk/scrum_team.evalset.json')  # -> 10 cases, no error
get_evaluation_criteria_or_default('eval/adk/test_config.json')  # -> same criteria dict, no error
```

## Files in this directory

| File | Purpose |
|---|---|
| `scrum_team.evalset.json` | The `EvalSet` - 10 `EvalCase` entries |
| `test_config.json` | The matching `EvalConfig` (`tool_trajectory_avg_score`, `IN_ORDER`) |
| `agent/scrum_team/__init__.py`, `agent/scrum_team/agent.py` | Loader shim for `adk eval` (see "Deviation" above) - re-exports the real `agents.scrum_team.agent.root_agent` unchanged |
| `litellm.local.yaml` | Dedicated, pinned LiteLLM config for local `run_adk_eval.py` runs (see "Reproducible model config" above) |
| `litellm.local-hostollama.yaml` | Same, but pointed at a native Ollama on this host - for `run_adk_eval.py --host-ollama` |
| `litellm.ci.yaml` | Dedicated, cheap-cloud-model LiteLLM config for `run_adk_eval.py --ci` / `adk-eval.yml` |
