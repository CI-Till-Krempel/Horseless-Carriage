# Issue

- Issue ID: ISSUE-0022
- Title: No Warning When Local GPU Support Is Enabled But Not Actually Working
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #49): as GPU support is essential for local-model performance, the system
should notify the user if there is no working GPU support for local models. `docker-compose.gpu.yaml`
and `docs/SETUP.md`'s "GPU Support" section (ISSUE-0018) already document that a driver/WSL2
misconfiguration on the host silently leaves Ollama running on CPU with no error from Docker - the
override file being merged in is no guarantee the GPU is actually reachable from inside the
container. The only way to tell was for a user to run one of two commands by hand
(`docker compose ... exec ollama nvidia-smi`, or grep Ollama's own startup log for `library=cuda` vs.
`library=cpu`) - nothing in the tooling itself ever checked this or told the user their `OLLAMA_GPU_
ENABLED=true` choice wasn't actually paying off.

## Acceptance Criteria
- When `OLLAMA_GPU_ENABLED=true` and the `ollama` container is running, `doctor.py` checks what
  Ollama itself detected (via its `library=cuda`/`library=cpu` "inference compute" log line) and
  prints a hard-to-miss warning if it fell back to CPU - not just another line among many, since a
  user could otherwise easily miss it among doctor.py's other output.
- No live check (and therefore no false "not working yet" warning) when the container isn't running
  yet, or when GPU support isn't enabled at all - this is a diagnostic for an already-running local
  GPU setup, not a new precondition for starting one.
- `run.py`'s pre-flight gate (`skip_llm_probe=True`, before any container is started) continues to
  skip this check for the same reason it already skips the live proxy-reachability check.
- When the GPU *is* confirmed working, doctor.py says so explicitly, so the check isn't only ever
  visible when something's wrong.

## Notes
- Where the gap lived: `doctor.py`'s LLM Configuration section only ever reported the *configured*
  provider/GPU choice, never whether a running local GPU setup was actually working.
- Builds directly on ISSUE-0018 (added the override file + manual verification docs) and ISSUE-0020
  (added the `OLLAMA_GPU_ENABLED` setup prompt) - this closes the loop by automating the verification
  step those two left as a manual, easy-to-forget follow-up.

## Test Approach
- `tests/test_lib_docker.py::TestOllamaGpuStatus` - unit tests for the new `ollama_gpu_status()`
  helper against representative `docker compose logs ollama` output (cuda, cpu, no matching line yet,
  last-line-wins when Ollama logs the line more than once, command failure, docker not installed).
- `tests/test_doctor.py::TestOllamaGpuWarning` - integration tests: warns loudly on `library=cpu`,
  confirms explicitly on `library=cuda`, does not run the check at all when GPU isn't enabled, the
  `ollama` container isn't up yet, or `skip_llm_probe=True`.

## Resolution
- Added `lib_docker.ollama_gpu_status(compose_args)`: runs `docker compose <args> logs ollama` and
  parses the last `inference compute ... library=<x>` line, returning `"cuda"`/`"cpu"`/`None`
  (undeterminable - best-effort diagnostic, never a hard gate).
- Wired into `doctor.check()`: when `active_provider == "local"`, `OLLAMA_GPU_ENABLED == "true"`, the
  `ollama` service is running, and `skip_llm_probe` is `False`, calls `ollama_gpu_status` and either
  prints an exclamation-banner-wrapped warning (`library=cpu`, added as a `warning`-severity
  `ActionableItem` too, so it also shows up in the punch list) or a plain confirmation (`library=cuda`).
- Documented in `docs/SETUP.md`'s "GPU Support" section: this check now runs automatically, so the
  two manual verification commands are a fallback, not the only way to know.
