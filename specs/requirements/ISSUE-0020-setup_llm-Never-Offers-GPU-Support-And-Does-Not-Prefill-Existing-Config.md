# Issue

- Issue ID: ISSUE-0020
- Title: setup_llm.py Never Offers GPU Support and Does Not Prefill Existing Configuration
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported: `setup_llm.py` should ask whether to enable GPU support (recommending it when the host
supports it), and should prefill defaults from whatever is already configured on a re-run.

Investigating surfaced two separate, real gaps:

1. **No GPU prompt at all.** `run_local_provider` never mentioned `docker-compose.gpu.yaml`
   (ISSUE-0018) - a user had to know that file existed and merge it in manually with `-f`, with no
   guidance on whether their machine could actually use it.
2. **Re-running the script did not prefill the current setup**, contrary to its own module
   docstring's claim ("Re-run it any time... it reuses whatever's already in `.env` as the default
   for each prompt" - README.md). Two concrete places this was false:
   - `select_model` (used for every model choice - main model, eval-harness cheap model, the Ollama
     tag) always defaulted the empty-input choice to option 1 (the freshly fetched list's newest
     entry, or the curated Ollama list's first entry) - never to whatever was already configured.
     Re-running the script to just rotate an API key or adjust a budget would silently reset your
     model choice back to option 1 unless you noticed and re-selected it.
   - The Human Interaction Level prompt (`prompt_project_settings`) hard-coded `"(default)"` on
     option 1 (Product) and always used `"1"` as the empty-input default, regardless of
     `INTERACTION_LEVEL` already being set to e.g. `Stakeholder` or `CEO` in `.env`.

## Acceptance Criteria
- `run_local_provider` detects a usable NVIDIA GPU (best-effort, cross-platform) and asks whether to
  enable GPU acceleration, recommending "yes" when one is detected.
- The choice is persisted (`OLLAMA_GPU_ENABLED` in `.env`) and actually takes effect - `run.py`
  includes `docker-compose.gpu.yaml` automatically when set, and `setup_llm.py`'s own live
  configuration test exercises the GPU path too when enabled, so a misconfiguration surfaces
  immediately rather than only later.
- Re-running the script's GPU prompt defaults to whatever was already configured (`OLLAMA_GPU_ENABLED`),
  not the fresh detection result - a deliberate prior choice isn't silently flipped back.
- `select_model` prefills the model already configured (read back from the litellm.yaml-style file
  this script itself previously wrote), marking it "(current)" when it's in the freshly
  fetched/curated list, and keeping it as the default even when it isn't (e.g. a deprecated/renamed
  model) rather than resetting to option 1.
- The Human Interaction Level prompt defaults to whatever `INTERACTION_LEVEL` is already set to, with
  a dynamic "(current)" marker instead of a hard-coded "(default)" on Product.
- A test exists for each of the above.

## Notes
- Where the gaps lived: `setup_llm.py`'s `select_model` (model prompts) and
  `prompt_project_settings` (interaction level prompt), and `run_local_provider` (no GPU prompt at
  all, prior to this fix).
- GPU detection (`detect_nvidia_gpu`) runs `nvidia-smi --query-gpu=name --format=csv,noheader` and
  treats a successful, non-empty result as "yes" - the same signal `docker-compose.gpu.yaml`'s own
  `driver: nvidia` reservation actually needs (ISSUE-0018). Always "no" on macOS: Docker Desktop for
  Mac has no NVIDIA GPU passthrough support at all, regardless of `nvidia-smi`'s presence.
- The GPU prompt's default-enable logic (`gpu_default_enable`) intentionally lets a prior explicit
  choice override the fresh detection result - consistent with the broader "prefill existing config"
  fix, and avoids flipping a deliberate choice back just because detection is imperfect in a given
  environment (e.g. a remote/CI context without the same GPU visibility as the real target host).

## Test Approach
- `tests/test_setup_llm.py`: `TestSelectModel` (current-in-options marks + defaults to it,
  current-not-in-options still kept as default, still overridable numerically),
  `TestCurrentModelForRole` (reads back main/cheap/Ollama-tag-with-colon models from a previously
  written file; missing file/role returns ""), `TestDetectNvidiaGpu` (macOS always false; missing
  binary; successful/failing/empty/exception `nvidia-smi` results), `TestGpuDefaultEnable` (detection
  wins with no prior choice; prior choice wins over detection either direction),
  `TestCurrentInteractionLevelChoice` (reads back each level; unset/unrecognized defaults to "1"),
  and `TestRunConfigurationTest` additions verifying `OLLAMA_GPU_ENABLED` correctly includes/omits
  `docker-compose.gpu.yaml` in the live-test compose args.
- `tests/test_run.py`: `TestComposeFileArgs` additions verifying `compose_file_args` includes
  `docker-compose.gpu.yaml` only for a local setup with `OLLAMA_GPU_ENABLED=true`, and never for a
  cloud setup regardless of that variable.

## Resolution
- `setup_llm.py`:
  - `select_model(label, options, current="")`: marks `current` as "(current)" when present in
    `options`, defaults the empty-input choice to it either way (even when absent from `options`).
  - New `current_model_for_role(yaml_path, role)`: reads back a previously-written model tag for a
    given role from a litellm.yaml-style file.
  - New `detect_nvidia_gpu()` and `gpu_default_enable(gpu_detected, current_value)`.
  - New `current_interaction_level_choice(env_path)` (+ `_INTERACTION_LEVEL_CHOICES` constant),
    replacing the hard-coded `"1"`/`"(default)"` in `prompt_project_settings`.
  - `run_cloud_provider`: prefills main + eval-harness-cheap model defaults from the provider's own
    `config/model-templates/litellm.cloud-{provider}.yaml`.
  - `run_local_provider`: prefills `OLLAMA_MODEL`; adds the GPU detection/prompt, persisting
    `OLLAMA_GPU_ENABLED` and printing the resulting `docker compose ... up` command with `-f
    docker-compose.gpu.yaml` included when enabled.
  - `run_configuration_test`: includes `-f docker-compose.gpu.yaml` in the live-test compose args
    when `OLLAMA_GPU_ENABLED=true`.
- `run.py`'s `compose_file_args`: includes `-f docker-compose.gpu.yaml` automatically for a local
  setup when `OLLAMA_GPU_ENABLED=true` in `.env`.
- `.env.local.example`: documented `OLLAMA_GPU_ENABLED` alongside `OLLAMA_MODEL`/`OLLAMA_KEEP_ALIVE`.
- `docs/SETUP.md`'s "GPU Support" section now describes the automatic `setup_llm.py`/`run.py` path
  first, keeping the manual `-f` instructions for anyone skipping `setup_llm.py`.
- All 43 pre-existing `setup_llm.py` tests and 9 pre-existing `run.py` tests still pass unmodified;
  added tests listed under Test Approach all pass.
