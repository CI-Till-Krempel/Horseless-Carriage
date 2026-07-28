# Issue

- Issue ID: ISSUE-0019
- Title: Merge Regression Left the NVIDIA GPU Reservation Always-On by Default
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
Merging PR #52 (ISSUE-0018's GPU-support fix) into `main` after PR #50 (ISSUE-0017's Ollama
keep-alive fix) had already merged produced a broken `docker-compose.local.yaml`. The merge commit
(`09e6961`, "Merge branch 'main' into fix/ollama-gpu-support-fragile-config") kept PR #52's intended
comment ("CPU-only by default. For NVIDIA GPU acceleration, merge in docker-compose.gpu.yaml...")
alongside the *original, pre-fix* commented-out `# Uncomment if the host has an NVIDIA GPU...` /
`# deploy: ... # capabilities: [gpu]` block from before ISSUE-0018 - except that block's leading `#`
comment markers were stripped, leaving a live, uncommented `deploy.resources.reservations.devices`
NVIDIA GPU reservation directly in the base file. Confirmed empirically:
`docker compose -f docker-compose.local.yaml config` showed the `ollama` service with an active
`deploy: resources: reservations: devices: [driver: nvidia, ...]` block present with no override
file included at all.

This is worse than the state ISSUE-0018 fixed: previously the block was at least harmlessly
commented out (CPU-only unless a user manually uncommented it); after this merge, `docker compose
-f docker-compose.local.yaml up` (the plain, documented default command) would unconditionally
attempt to reserve an NVIDIA GPU device and fail outright on any machine without one and without
the NVIDIA Container Toolkit/WSL2 GPU passthrough configured - exactly the class of "GPU stuff
should be strictly opt-in" problem ISSUE-0018 set out to fix, reintroduced by the merge itself
rather than by either PR's actual intended diff.

## Acceptance Criteria
- `docker compose -f docker-compose.local.yaml config` (no override) shows no `deploy`/GPU device
  reservation on the `ollama` service - CPU-only remains the true default.
- `docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml config` still shows the
  NVIDIA device reservation correctly merged in - the override mechanism itself (ISSUE-0018) is
  unaffected by this fix.
- The stray leftover pre-ISSUE-0018 comment ("Uncomment if the host has an NVIDIA GPU...") is
  removed - it no longer describes how GPU support actually works (via the override file, not
  hand-uncommenting) and its own referenced block was the one left live by the merge.

## Notes
- Root cause: a merge-conflict resolution artifact (`09e6961`), not a defect in either PR #50's or
  PR #52's own intended diff - each diff was individually correct against its own base; reconciling
  them (interleaving PR #52's deletion of the old commented block with PR #50's unrelated insertion
  immediately before it) is what went wrong, most visibly by the old block's `#` markers being
  stripped rather than the block being removed as PR #52 intended.
- A reminder to actually verify a merged result empirically (`docker compose ... config`) rather
  than assuming two independently-correct, independently-reviewed PRs compose correctly once merged
  - this was caught by re-running that exact verification command after syncing to `main`, the same
  one used to verify ISSUE-0018 originally, just re-run post-merge instead of only pre-merge.

## Test Approach
- Not unit-testable via `pytest` (Docker Compose configuration); verified directly, the same way
  ISSUE-0018 was: `python3 -c "import yaml; yaml.safe_load(...)"` for syntax, then
  `docker compose -f docker-compose.local.yaml config` (confirms no GPU reservation present) and
  `docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml config` (confirms the
  override still correctly adds it) for the actual merged behavior in both cases.

## Resolution
- Removed the live, uncommented `deploy.resources.reservations.devices` block and the stray
  leftover "Uncomment if the host has an NVIDIA GPU..." comment from `docker-compose.local.yaml`'s
  `ollama` service, restoring CPU-only-by-default.
- Kept PR #52's intended comment (pointing at `docker-compose.gpu.yaml`) and PR #50's Performance
  Tuning comment, both of which were correct and unaffected by this fix.
- Verified both compose-file combinations via `docker compose ... config` (see Test Approach).
