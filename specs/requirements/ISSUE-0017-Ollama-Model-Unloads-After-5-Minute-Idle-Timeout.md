# Issue

- Issue ID: ISSUE-0017
- Title: Ollama Model Unloads After Ollama's Default 5-Minute Idle Timeout
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
Reported (GitHub issue #45): "is it possible to improve the performance. Is the model kept in memory
as long as the container runs? The container and ollama should be configured to keep the model
available." A log attached to the related GPU issue (#44) confirms the actual configuration in
effect: `env="map[... OLLAMA_KEEP_ALIVE:5m0s ...]"` - Ollama's own stock default. `docker-compose
.local.yaml`'s `ollama` service passed through only `OLLAMA_MODEL`; nothing set `OLLAMA_KEEP_ALIVE`,
so the model is unloaded from memory after 5 minutes of inactivity and the full model-load time is
paid again on the next request. This container's only job is serving this one model to this one
Scrum team continuously for the life of a sprint, so the 5-minute unload is pure overhead for this
use case, not a resource saving.

Separately, the report also asked whether CPU/RAM are "maxed out" for the `ollama` service.
`docker-compose.local.yaml` already sets no `deploy.resources.limits` on that service, so nothing in
this repo's configuration artificially caps it - a container without an explicit limit can already
use as much CPU/RAM as Docker Desktop's own VM is allocated. The actual lever for "give Ollama more
of the machine" is that VM allocation (Docker Desktop -> Settings -> Resources -> Advanced), which
this repo has no way to control from inside a compose file - this needed documenting, not a code fix
that doesn't exist to make.

## Acceptance Criteria
- `OLLAMA_KEEP_ALIVE` is configurable via `.env` (`.env.local.example`) and passed through to the
  `ollama` service in `docker-compose.local.yaml`, defaulting to `-1` (never unload) instead of
  Ollama's stock `5m`.
- `docs/SETUP.md` documents: why `-1` is the sensible default here, when to lower it (sharing the
  instance with other bursty workloads), and that CPU/RAM aren't compose-side capped already - with
  a pointer to Docker Desktop's own resource allocation as the actual lever.

## Notes
- Where the gap lived: `docker-compose.local.yaml`'s `ollama` service `environment:` list (only
  `OLLAMA_MODEL` was set) and `.env.local.example` (no `OLLAMA_KEEP_ALIVE` documented at all).
- Deliberately NOT changed in this pass: `OLLAMA_NUM_PARALLEL` (concurrency across the roles' calls)
  and `OLLAMA_CONTEXT_LENGTH`/effective context size (the same attached log shows repeated `msg=
  "truncating input prompt" limit=2050` warnings, i.e. a small effective context window) - both are
  real, visible-in-the-log tuning knobs, but changing either without validated headroom risks
  regressing memory use or context truncation across the three documented model sizes
  (`llama3.2:3b`/`llama3.1:8b`/`qwen2.5:14b`, 8GB-24GB+ RAM/VRAM) with no way to verify safety for
  all three from here. `OLLAMA_KEEP_ALIVE=-1` is a purely temporal setting (how long an already-sized
  model stays resident) with no such per-model memory-sizing tradeoff, which is why it was safe to
  change the default for, unlike the other two.
- The truncation warning is plausibly a knock-on symptom of CPU-only inference (see
  `specs/requirements/ISSUE-0018-GPU-Support-Is-A-Fragile-Hand-Edited-YAML-Comment.md`, filed from
  the same reported log) rather than an independent bug - worth revisiting once GPU support is
  actually verified working, rather than guessing at a new context-length default now.

## Test Approach
- Not unit-testable via `pytest` (Docker Compose/environment configuration, not application code);
  verified by validating the edited `docker-compose.local.yaml` parses as valid YAML
  (`python3 -c "import yaml; yaml.safe_load(...)"`) and by inspection against Ollama's own documented
  `OLLAMA_KEEP_ALIVE` semantics (negative value = never unload).

## Resolution
- `docker-compose.local.yaml`: added `OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE:--1}` to the `ollama`
  service's environment, with a comment explaining why `-1` fits this container's actual usage
  pattern; added a comment on the service documenting that CPU/RAM are already uncapped by this
  file and where the real allocation lever lives (Docker Desktop's VM resource settings).
- `.env.local.example`: added `OLLAMA_KEEP_ALIVE="-1"` alongside the existing `OLLAMA_MODEL`, with
  matching documentation.
- `docs/SETUP.md`: added a "Performance Tuning" subsection under "Running fully local" covering both
  of the above, with a forward pointer to the GPU Support section (ISSUE-0018) for hardware
  acceleration.
