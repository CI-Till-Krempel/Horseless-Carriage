Feature: GPU support toggle (local/Ollama only)
  OLLAMA_GPU_ENABLED (.env) controls whether run.py/setup_llm.py merge in
  docker-compose.gpu.yaml alongside docker-compose.local.yaml. Detection
  (setup_llm.py's nvidia-smi probe) only drives the DEFAULT for the initial
  prompt; host-mode (cloud provider, no GPU override at all) and the
  GPU-overlay are mutually exclusive via compose_file_args; and doctor.py
  separately warns when the flag is on but Ollama is not actually using the
  GPU.

  Background:
    # setup_llm.py:544-563 (detect_nvidia_gpu) - always False on macOS
    # (no NVIDIA passthrough in Docker Desktop for Mac); elsewhere, a
    # best-effort nvidia-smi probe that never raises.
    Given detect_nvidia_gpu() is a best-effort, never-raising probe

  @automatable
  Scenario: GPU is never detected on macOS regardless of hardware
    Given sys.platform == "darwin"
    When detect_nvidia_gpu() is called
    Then it returns False without even checking for nvidia-smi

  @automatable
  Scenario: A missing nvidia-smi binary means "no GPU detected", not an error
    Given sys.platform != "darwin" and "nvidia-smi" is not on PATH
    When detect_nvidia_gpu() is called
    Then it returns False (no exception raised)

  # setup_llm.py:566-574 (gpu_default_enable) - re-running the wizard keeps
  # whatever was already explicitly configured; fresh detection only decides
  # the default on a first-time (unset) configuration.
  @automatable
  Scenario Outline: gpu_default_enable prefers an explicit prior choice over fresh detection
    Given OLLAMA_GPU_ENABLED is currently "<current_value>"
    And a GPU is <detected> this run
    When gpu_default_enable(gpu_detected=<detected_bool>, current_value="<current_value>") is called
    Then it returns <expected>

    Examples:
      | current_value | detected | detected_bool | expected |
      | true           | not detected | False    | True     |
      | false          | detected     | True     | False    |
      | (unset)        | detected     | True     | True     |
      | (unset)        | not detected | False    | False    |

  # lib_docker.py:16-39 (compose_file_args) - host-mode (a cloud provider
  # active) NEVER includes any compose override, GPU or otherwise; only a
  # "local" active provider can add docker-compose.gpu.yaml, and only on
  # top of docker-compose.local.yaml (never GPU-alone) - the two are
  # mutually exclusive in the sense that GPU can never apply to a cloud setup.
  @automatable
  Scenario Outline: compose_file_args resolves the right compose-file overlay
    Given the active provider is "<provider>"
    And OLLAMA_GPU_ENABLED is "<gpu_enabled>"
    When compose_file_args(repo_root) is called
    Then it returns <result>

    Examples:
      | provider | gpu_enabled | result                                                              |
      | gemini   | true        | [] (cloud setups never see any compose override, GPU flag ignored) |
      | local    | false       | ["-f", "docker-compose.local.yaml"]                                 |
      | local    | true        | ["-f", "docker-compose.local.yaml", "-f", "docker-compose.gpu.yaml"]|

  # lib_docker.py:71-90 (ollama_gpu_status) - parses Ollama's own
  # "inference compute ... library=<cuda|cpu>" startup log line; None if it
  # can't be determined yet (not running, docker unavailable).
  @automatable
  Scenario Outline: ollama_gpu_status parses Ollama's own startup log line
    Given `docker compose ... logs ollama` output contains "<log_line>"
    When ollama_gpu_status(compose_args) is called
    Then it returns "<status>"

    Examples:
      | log_line                                   | status |
      | inference compute id=0 library=cuda ...     | cuda   |
      | inference compute id=0 library=cpu ...      | cpu    |
      | (no such line yet)                          | (None) |

  # doctor.py:236-249 - GH issue #49: this is a fully mechanical check, not
  # a manual "did you remember to check nvidia-smi yourself" step. See also
  # doctor-gate.feature's equivalent scenarios (shared logic, cross-referenced
  # here for this toggle's own feature area).
  @automatable
  Scenario: doctor.py warns loudly when the GPU flag is on but Ollama reports CPU
    Given the active provider is "local", OLLAMA_GPU_ENABLED="true", and the
      "ollama" container is running
    And lib_docker.ollama_gpu_status reports "cpu"
    When doctor.check(repo_root) runs
    Then a WARNING item mentions "running on CPU" with a "!"-banner
    And it points at docs/SETUP.md's "GPU Support" prerequisites section

  # docs/SETUP.md "GPU Support" - manual, host-level verification that
  # cannot be simulated with mocks (real driver/WSL2/NVIDIA Container
  # Toolkit state).
  @manual-qa
  Scenario: A real GPU-enabled run actually uses the GPU (host-level verification)
    Given prerequisites are installed (NVIDIA driver, WSL2 backend on Windows
      or NVIDIA Container Toolkit on Linux)
    When "docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml up" runs
    Then "docker compose ... exec ollama nvidia-smi" lists the real GPU
    And Ollama's own log shows "library=cuda", not "library=cpu"
