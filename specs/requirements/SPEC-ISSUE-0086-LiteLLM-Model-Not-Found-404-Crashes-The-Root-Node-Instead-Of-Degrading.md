# Issue

- Issue ID: SPEC-ISSUE-0086
- Title: An Ollama `model not found` 404 On The First LLM Call Crashes The Whole ADK Root Node Instead Of Degrading Like Every Other Known LLM-Call Failure
- Status: Draft
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
Same investigation as SPEC-ISSUE-0085 (a real sprint against `decide-o-mat-KMP`, local Ollama install,
`.env`'s `OLLAMA_MODEL='qwen2.5-coder:32b'`, `OLLAMA_HOST_MODE='true'`). A separate, earlier failure in
the same install's `sessions/agent-my-sprint.log` (2026-11-05 16:18:52) shows the run's very first LLM
call crashing the entire process:

```
litellm.NotFoundError: Error code: 404 - {'error': {'message': 'litellm.NotFoundError: OllamaException
- {"error":"model \'qwen2.5-coder:32b\' not found"}. Received Model Group=scrum-orchestrator
Available Model Group Fallbacks=None', ...}}
...
google.adk.workflow._errors.DynamicNodeFailError: Dynamic node ScrumOrchestrator failed
2026-11-05 16:18:52,420 - google_adk.google.adk.runners - ERROR - Root node ScrumOrchestrator failed.
```

**Confirmed mechanism.** `OLLAMA_HOST_MODE=true` switches Docker Compose to
`docker-compose.local-hostollama.yaml` (`lib_docker.py:54-55`), which has **no dockerized `ollama`
service at all** - `litellm` is pointed straight at the user's own natively-running Ollama via
`host.docker.internal` (`docker-compose.local-hostollama.yaml:85-90`,
`config/model-templates/litellm.local-ollama.yaml:6`), purely to get GPU/Metal acceleration on macOS
that Docker Desktop can't pass through. The auto-pull logic that normally fetches the configured model
tag on first run lives entirely inside `ollama-entrypoint.sh:18-23`, which only runs in the dockerized
`ollama` container defined by `docker-compose.local.yaml` - host mode bypasses that container, so
nothing in this framework ever pulls the model; the user is expected to have already run `ollama pull
<model>` on the host themselves.

When that pull was skipped (or pulled the wrong tag), the resulting 404 is a
`litellm.exceptions.NotFoundError` - a subclass of `openai.NotFoundError`/`APIStatusError`. The
monkey-patched `LiteLLMClient.acompletion` in `agents/scrum_team/agent.py:180-229` degrades several
*specific* known-failure types into a synthetic response instead of raising: Gemini safety blocks
(`agent.py:186-189`), `APIConnectionError`/`ServiceUnavailableError`/`Timeout`/connection-flavored
`InternalServerError` (`agent.py:199-209`), and `RateLimitError` (`agent.py:220-228`). `NotFoundError`
is not among them, so it falls through to the unconditional `raise e` at `agent.py:229`, propagates
uncaught up through ADK, and kills the root node - exactly the observed `DynamicNodeFailError`/"Root
node ... failed" crash. Unlike every other handled failure (which produce a graceful in-band message
the agent can act on, retry, or report), this one takes down the entire session with a raw traceback -
for ScrumOrchestrator specifically, that means the *very first* turn of the run.

Documentation around this is misleading in exactly the way that produces this failure:
`docs/SETUP.md:376-377` tells the user to "Pull the model configured in .env's `OLLAMA_MODEL`" but the
example command directly below it is the literal default tag, `ollama pull llama3.1:8b` - a user who
already changed `OLLAMA_MODEL` to something else (as this install did, to `qwen2.5-coder:32b`) and
copy-pastes the example pulls the wrong model and gets this exact 404. `.env.local.example:8`'s comment
("Model tag pulled automatically on first start") sits a few lines above `OLLAMA_HOST_MODE` with no
cross-reference warning that host mode is the one configuration where that's no longer true. Only
`setup_llm.py:1072-1073` correctly interpolates the actual configured tag into its pull instruction, and
only at interactive first-setup time, not as a standing reference a user re-reads before a later sprint.

## Acceptance Criteria
- `LiteLLMClient.acompletion`'s degrade list in `agent.py` must also catch `litellm.exceptions.
  NotFoundError` (or the narrower "model not found" shape specifically, to avoid masking a genuinely
  different 404) and degrade it into an in-band synthetic response - the agent/role should see a clear,
  actionable tool/model error it can report via `raise_story_blocker`/`record_blocking_interaction`,
  not have the whole root node crash.
- The degraded message must be actionable: name the exact missing model tag and the fact that it needs
  to be pulled (mirroring `setup_llm.py`'s own correct wording), not a generic "LLM unavailable."
- `docs/SETUP.md`'s host-mode/local-ollama pull instructions must stop showing the literal default tag
  as the example command - either interpolate nothing (plain prose: "pull whatever tag you set
  `OLLAMA_MODEL` to") or make the example visually impossible to copy-paste unmodified.
- A new regression test simulating a `litellm.exceptions.NotFoundError` from the model call: assert
  `acompletion` degrades it to a response instead of re-raising, matching the pattern of the existing
  `APIConnectionError`/`RateLimitError` degrade tests.

## Notes
- Distinct from SPEC-ISSUE-0085: that issue is about a git-checkout deadlock reachable mid-sprint by
  any role; this one is about an LLM-transport-layer failure reachable by any role's very first call,
  specific to local-Ollama setups (host mode in particular, since that's the one path with no
  framework-driven auto-pull). Both independently contributed to this install's sprint runs stopping
  repeatedly, on different dates (2026-11-05 for this one, 2026-11-06/11-10 for SPEC-ISSUE-0085).
- Priority is "Should" rather than "Must": the trigger is an operator setup gap (wrong/missing model
  pull) that correct-docs-following usage avoids entirely, unlike SPEC-ISSUE-0085's checkout deadlock,
  which an operator cannot avoid at all once a `.idea`/`.DS_Store` file is tracked. The fix here is
  still worth making because a single operator mistake currently costs the *entire* session rather than
  one clear, recoverable error.

## Test Approach
- `agents/scrum_team/tests/test_agent.py` (wherever the existing `LiteLLMClient.acompletion` degrade
  tests for `APIConnectionError`/`RateLimitError`/Gemini-safety-block live): add
  `test_degrades_model_not_found_instead_of_raising`, constructing a `litellm.exceptions.NotFoundError`
  the same way the existing tests construct their target exception, asserting a synthetic response is
  returned and the real exception never propagates.
- Manual/docs check: confirm `docs/SETUP.md`'s revised wording no longer contains a literal
  copy-pastable wrong-tag example near the `OLLAMA_MODEL`-specific pull instruction.
