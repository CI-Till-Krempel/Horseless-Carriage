# Issue

- Issue ID: ISSUE-0028
- Title: setup_all.py Sequencing Bugs - Wrong Compose File, and Developer Mode Asked Too Late
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported live (Windows, Local/Ollama setup, `python3 setup_all.py`): after `setup_llm.py` configured
and verified a fully local Ollama setup, the subsequent `doctor.py` gate's live test failed with:

```
WARNING: LLM connectivity test failed - HTTP 500 for "scrum-po". litellm.APIConnectionError: Missing
Gemini API key. Set the GEMINI_API_KEY or GOOGLE_API_KEY environment variable.
```

despite no cloud provider ever being chosen. Root cause: `setup_project.py`'s step 4 ran a bare
`docker compose up -d db litellm` with no `-f` flags, always targeting the default
`docker-compose.yaml` (cloud/Gemini config, mounting the root `litellm.yaml`). `setup_all.py` runs
`setup_llm.py` (which correctly starts `docker compose -f docker-compose.local.yaml up -d db litellm
ollama` and verifies it works) immediately followed by `setup_project.py` - and since both compose
files define a same-named `litellm` service under the same default Compose project, the second,
unqualified `up` call recreated the `litellm` container against the WRONG (cloud) config, silently
undoing what `setup_llm.py` had just configured and verified. The `doctor.py` gate then probed this
now-misconfigured proxy and hit exactly the reported error.

A related sequencing problem, raised in review of the fix above: `setup_all.py`'s guided flow only
ever asked about developer mode (which forces a fresh rebuild of the `ollama`/`agent` images) at the
very end, in `offer_to_start` - by which point `setup_llm.py`'s own Local/Ollama live test had already
started a container (`ollama`) using whatever image was already present. If a user answers "yes" to
developer mode at that final prompt, `run.py dev` does rebuild fresh images before the real run - but
the setup-time live test that just validated the configuration was tested against a stale image the
rebuild was about to discard, wasting a real model pull and giving a misleading "it works" signal for
an image no longer in use.

## Acceptance Criteria
- `setup_project.py` starts `db`/`litellm` using whichever compose file(s) the actually-configured
  provider needs (the same `lib_docker.compose_file_args()` helper `run.py`/`rebuild_images.py` already
  use), not always the default `docker-compose.yaml` - so it never recreates a correctly-configured
  Local/Ollama `litellm` container against the wrong config.
- `setup_all.py` asks about developer mode as its very first step, before any guided step (and
  therefore before any container work) runs.
- That answer is threaded into `setup_llm.py`'s own Local/Ollama live test, which rebuilds the `ollama`
  image fresh (via `rebuild_images.rebuild`) before starting it for that test, when developer mode is
  enabled - not just at the very end via `run.py dev`.
- Cloud providers are unaffected by developer mode during setup (they never start a locally-built
  image - `litellm` is a pulled release image, `db` is postgres) - no rebuild attempt for them.
- `offer_to_start` no longer re-asks the developer-mode question (it's already decided) - it only
  folds the already-known value into the final `argv` handed to `run.py`.
- A rebuild failure warns but doesn't abort the live test - it continues with whatever image is
  already present, same fallback behavior `run.py dev`'s own rebuild step already has.

## Notes
- Where the gaps lived: `setup_project.py`'s step 4 (`docker compose up -d db litellm`, no `-f` args
  at all); `setup_all.py`'s `offer_to_start` (asked the developer-mode question last); `setup_llm.py`'s
  `run_configuration_test` (had no way to know about developer mode at all).
- `setup_llm.py`'s own live test already correctly determines the right compose file itself - the bug
  was specifically that `setup_project.py`, running immediately afterward in the same guided flow,
  didn't do the same thing and clobbered it.
- Standalone `setup_llm.py --dev` and `setup_project.py` (run on its own, without `setup_all.py`) both
  pick up the same fixes independently - `setup_llm.py`'s own `__main__` block now parses `--dev`/`dev`
  the same way `setup_all.py`/`rebuild_images.py` already do.

## Test Approach
- `tests/test_setup_project.py` (new file): the default (cloud) compose file is used when no
  Local/Ollama config is active; the local compose file (+ GPU override, when present) is used when
  one is; a `docker compose up` failure exits with its own returncode; Docker/Docker Compose missing
  still exits as before.
- `tests/test_setup_llm.py::TestRunConfigurationTest` (new tests): `dev=True` + Local/Ollama rebuilds
  `ollama` before the `stop_check`/`up` calls (in that order); `dev=False` never rebuilds; a cloud
  provider is never rebuilt regardless of `dev`; a rebuild failure warns but the live test still
  proceeds to start containers.
- `tests/test_setup_all.py` (updated `TestMain`/`TestOfferToStart`): developer mode is asked as
  `main()`'s first action (before any guided step), the answer is threaded into
  `setup_llm.main(dev=...)`, and `offer_to_start` no longer re-asks it.
- Full `pytest tests/`: 285 passed, no regressions.

## Resolution
- `setup_project.py`: added `lib_docker.compose_file_args(Path("."))` and passes the result into the
  `docker compose ... up -d db litellm` call; prints which compose file is active, matching `run.py`'s
  own convention.
- `setup_llm.py`: `main(dev=False)`, `run_local_provider(dev=False)`, and
  `run_configuration_test(..., dev=False)` all now accept/thread a `dev` flag; when `dev and provider
  == "local"`, rebuilds `ollama` via `rebuild_images.rebuild(compose_args)` before starting containers
  for the live test. `__main__` now parses `--dev`/`dev` for standalone use.
- `setup_all.py`: `main()` asks about developer mode first, threads it into
  `setup_llm.main(dev=dev)`, and passes the already-decided value straight into `offer_to_start(dev)`
  (which no longer asks it itself).
- Docs: `docs/SETUP.md`, `README.md` updated to describe the new step-0 developer-mode question.
