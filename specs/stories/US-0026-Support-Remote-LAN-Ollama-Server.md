# User Story

- Story ID: US-0026
- Title: Support Pointing Horseless Carriage at a Local-Network Ollama Server
- Status: Done
- Priority: Must
- Owner: DevTeam
- Last Updated: 2026-11-12

## As a user without a capable local GPU/CPU on the machine running Horseless Carriage, I want to point the Local/Ollama setup at an Ollama server already running elsewhere on my local network (LAN IP/hostname + port), so that I can still use a fully local/offline model.

## Acceptance Criteria
- Given `setup_llm.py`'s Local/Ollama flow, when the user declines "run Ollama natively on the host" and is then asked whether to use a remote LAN Ollama server, then answering yes prompts for a host/IP and a port (defaulting to `11434`).
- Given a remote host/port was entered, when `config/model-templates/litellm.local-ollama.yaml` is generated, then every role's `litellm_params.api_base` points at `http://<host>:<port>` instead of `http://ollama:11434` or `http://host.docker.internal:11434`.
- Given remote mode is enabled, when the stack's compose file selection is resolved (`lib_docker.compose_file_args`), then it resolves to `docker-compose.local-hostollama.yaml` (no dockerized `ollama` service provisioned) instead of `docker-compose.local.yaml` - the same "skip the bundled container" topology host-native mode already uses, reused rather than duplicated.
- Given remote mode is enabled, when `setup_llm.py`'s live end-to-end test or `doctor.py` runs, then Ollama reachability is probed at the configured `http://<host>:<port>` (via `lib_docker.host_ollama_reachable`) instead of `http://localhost:11434`, and the dockerized `ollama` container's GPU-status check never runs.
- Given remote mode and host-native mode are mutually exclusive, when host-native mode is accepted, then the remote-server prompt is skipped entirely (and vice versa); dockerized GPU acceleration is also skipped in either mode.
- Given a user re-runs `setup_llm.py`, when remote mode was previously configured, then the host/port/enabled state is prefilled from `.env` rather than reset to the generic placeholder default.
- Given the bundled-container flow (today's default), when none of this new behavior is exercised, then it is completely unchanged.
- `docs/CONFIGURATION.md` documents the new "Remote LAN Ollama server" option, the new env vars, and how it reuses the host-mode compose file.

## Notes
- GH-502 (milestone v0.3.0).
- New `.env` vars: `OLLAMA_REMOTE_ENABLED`, `OLLAMA_REMOTE_HOST`, `OLLAMA_REMOTE_PORT` (default `11434`) - see `.env.local.example`.
- Design reuses the existing GH-93 "host-native Ollama" mechanism (`docker-compose.local-hostollama.yaml`, `OLLAMA_HOST_MODE`) almost entirely: that compose file already has no dockerized `ollama` service, which is exactly what a remote server needs too - the only real difference is which `api_base` ends up in `litellm.local-ollama.yaml` and which address `doctor.py`/the live test probe for reachability.
- Touches: `setup_llm.py` (`run_local_provider`, `run_configuration_test`), `lib_docker.py` (`compose_file_args`), `doctor.py` (`check`, `print_ollama_gpu_confirmation`), `docker-compose.local-hostollama.yaml` (header comment only), `.env.local.example`, `docs/CONFIGURATION.md`.

## Test Approach
- Unit tests in `tests/test_setup_llm.py` (`TestRunLocalProviderRemoteMode`, plus additions to `TestWriteLitellmYaml` and `TestRunConfigurationTest`) covering host/port prompting, `.env` writes, generated `api_base`, re-run prefill, and mutual exclusivity with host-native mode.
- Unit tests in `tests/test_run.py` (`TestComposeFileArgs`) covering `OLLAMA_REMOTE_ENABLED` compose-file selection, including the "wins over GPU" case.
- Unit tests in `tests/test_doctor.py` (`TestRemoteOllamaModeCheck`, plus additions to `TestPrintOllamaGpuConfirmation`) covering reachability probing against the configured address and skipping the container-based GPU check.
- Unit test in `tests/test_lib_docker.py` (`TestHostOllamaReachable`) covering a custom `base_url` argument.
- Full suite run via `python3 run_tests.py` (host-script + Docker-based agent suite) - green.
