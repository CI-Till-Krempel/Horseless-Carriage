# Issue

- Issue ID: ISSUE-0018
- Title: GPU Support Is a Fragile Hand-Edited YAML Comment With No Enablement Path or Verification
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
Reported (GitHub issue #44): on a Windows machine using Docker Desktop, GPU support did not appear
to work - the resource manager showed almost no GPU activity, and the model ran slowly. The attached
Ollama container log confirms the mechanism directly: `msg="inference compute" id=cpu library=cpu
compute="" name=cpu ... total="31.3 GiB"` - Ollama only ever discovered a CPU compute device, never
a GPU.

`docker-compose.local.yaml`'s `ollama` service already had the correct NVIDIA device-reservation
block for enabling GPU passthrough, but it was entirely commented out, with only a one-line comment
("Uncomment if the host has an NVIDIA GPU") as the enablement instructions. This has two concrete
problems: (1) hand-editing a commented-out, indentation-sensitive YAML block in place is exactly the
kind of change that's easy to get subtly wrong (a stray leftover `#`, wrong indentation swallowed by
a parent key) with zero feedback beyond "GPU still isn't being used" - `docker compose up` doesn't
warn you your edit did nothing; (2) there was no documented way to actually verify whether the GPU
was detected versus just assuming the edit worked, and no Windows/Docker-Desktop-specific guidance
on the host-side prerequisites (WSL2 backend, NVIDIA driver with WSL2 CUDA support) that are
required before the container can see a GPU at all, regardless of how the compose file is edited.

## Acceptance Criteria
- GPU support is enabled via a separate, additive Compose override file (not a commented-out block
  requiring hand-editing), merged in with an extra `-f` flag.
- The base `docker-compose.local.yaml` documents the override file's existence at the point where
  the old commented block used to be, and in its top-of-file usage comment.
- `docs/SETUP.md` documents: the enablement command, Windows/Docker-Desktop-specific prerequisites
  (WSL2 backend + NVIDIA driver with WSL2 CUDA support; no separate `nvidia-container-toolkit` needed
  on Windows), Linux prerequisites (NVIDIA Container Toolkit), and - critically - how to verify the
  GPU was actually detected (`nvidia-smi` inside the container, and what to look for in Ollama's own
  startup log: `library=cuda` vs. `library=cpu`), so a host-level driver/WSL2 misconfiguration is
  distinguishable from a repo-configuration problem.
- Verified: `docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml config` actually
  merges in the NVIDIA device reservation; `docker compose -f docker-compose.local.yaml config` alone
  (no override) has no such reservation, confirming CPU-only remains the correct default.

## Notes
- Where the gap lived: `docker-compose.local.yaml`'s `ollama` service (the commented-out
  `deploy.resources.reservations.devices` block) and `docs/SETUP.md` (no GPU section existed at all
  prior to this).
- This does not, and cannot, guarantee GPU passthrough actually works on any given Windows/Docker
  Desktop host from inside this repo alone - the reporter's specific failure could still be a host-
  level driver/WSL2 configuration issue this repo has no visibility into. What this fixes is the part
  actually within this repo's control: replacing an error-prone, unverifiable manual edit with a
  robust, documented, single-flag enablement mechanism plus an explicit verification step, so a user
  can tell definitively whether the *repo config* is correct and the GPU still isn't detected (a host
  problem to chase down with the documented prerequisites) versus the repo config itself being wrong
  (no longer possible to get subtly wrong via a bad hand-edit).
- The same attached log shows `msg="truncating input prompt" limit=2050` warnings (small effective
  context window) - noted in `specs/requirements/ISSUE-0017-Ollama-Model-Unloads-After-5-Minute-Idle
  -Timeout.md` as plausibly a knock-on symptom of running CPU-only, worth revisiting once GPU support
  is confirmed working for the reporter rather than guessing at a fix now.

## Test Approach
- Not unit-testable via `pytest` (Docker Compose configuration, not application code); verified
  directly: `python3 -c "import yaml; yaml.safe_load(...)"` on both compose files, plus running
  `docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml config` and confirming the
  merged `ollama` service includes the NVIDIA device reservation, and `docker compose -f docker-
  compose.local.yaml config` alone does not.

## Resolution
- Added `docker-compose.gpu.yaml`: an additive override defining the `ollama` service's
  `deploy.resources.reservations.devices` NVIDIA GPU reservation, merged in via
  `docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml up`.
- Removed the commented-out block from `docker-compose.local.yaml`'s `ollama` service, replacing it
  with a comment pointing to the override file and docs; updated the file's top-of-file usage comment
  to show the GPU-enabled command alongside the plain one.
- Added a "GPU Support" section to `docs/SETUP.md`: enablement command, Windows/Docker-Desktop and
  Linux prerequisites, and two verification methods (`nvidia-smi` inside the container; grepping
  Ollama's own startup log for `library=cuda` vs. `library=cpu`).
- Verified both compose-file combinations produce the intended merged configuration via
  `docker compose ... config` (see Test Approach).
