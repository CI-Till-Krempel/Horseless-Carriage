Feature: Doctor gate (doctor.py)
  doctor.py collects every configuration problem into one punch list
  (DoctorResult) instead of stopping at the first one, classifies each as an
  ERROR (blocks running the agent) or a WARNING (does not), and is reusable
  as a pre-flight gate by other scripts (run.py, setup_all.py) via
  doctor.check() rather than by parsing printed text.

  Background:
    # doctor.py:87-101 (check()) - runs every check against repo_root, never
    # stopping at the first ActionableItem found.
    Given a repo_root with a valid ".env", "litellm.yaml", and state repo
      (docker/docker-compose/gh all "present and happy" unless a scenario
      below overrides one)

  # doctor.py:44-51 (ActionableItem) and :59-60 (has_errors) - severity is
  # exactly "error" or "warning"; has_errors is true iff any item is "error".
  @automatable
  Scenario: ERROR items block the agent, WARNING items do not
    Given "docker" is not on PATH (an ERROR-class problem)
    And "gh" is not on PATH (a WARNING-class problem)
    When doctor.check(repo_root) runs
    Then the result has_errors is True
    And an item with severity "error" mentions "docker"
    And an item with severity "warning" mentions "'gh' command not found"
    And doctor.run(repo_root) returns exit code 1

  # doctor.py:124-137 (TestGuardClauses::test_multiple_errors_are_all_collected_not_just_the_first
  # in tests/test_doctor.py) - the whole point of the punch-list refactor
  # (ISSUE-0021): every blocking problem surfaces in one pass.
  @automatable
  Scenario: Every problem is collected in one pass, not just the first one found
    Given docker, docker-compose, and gh are all missing from PATH
    And ".env" does not exist
    When doctor.check(repo_root) runs
    Then the result lists ERROR items for docker, docker-compose, and ".env not found"
    And none of these checks were skipped because an earlier one already failed

  # doctor.py:87-101 (skip_llm_probe param) and :250-251 - run.py's
  # pre-flight gate passes skip_llm_probe=True (nothing is running yet, so a
  # live reachability check could only ever report "not reachable" and would
  # cost several real seconds for nothing).
  @automatable
  Scenario: skip_llm_probe=True skips the live proxy reachability check entirely
    When doctor.check(repo_root, skip_llm_probe=True) runs
    Then lib_llm_test.llm_wait_for_proxy is never called
    And the output notes "(Skipping live proxy reachability check - not needed here.)"

  # doctor.py:236-249 and lib_docker.py:71-90 (ollama_gpu_status) - GH issue
  # #49: a driver/WSL2 misconfiguration otherwise leaves Ollama silently
  # running on CPU with no error from Docker at all.
  @automatable
  Scenario: OLLAMA_GPU_ENABLED=true but Ollama actually reports CPU is a loud WARNING
    Given the active provider is "local" (config/model-templates/litellm.local-ollama.yaml)
    And ".env" has OLLAMA_GPU_ENABLED="true"
    And the "ollama" container is running
    And lib_docker.ollama_gpu_status reports "cpu" (library=cpu in its own log)
    When doctor.check(repo_root) runs
    Then a WARNING item mentions "running on CPU" and a "!"-banner is printed
    And the message points at "docker compose ... exec ollama nvidia-smi"

  # doctor.py:247-248 - the mirror-image success case: no warning at all when
  # the GPU really is in use.
  @automatable
  Scenario: GPU acceleration confirmed prints a positive confirmation, not a warning
    Given the active provider is "local" with OLLAMA_GPU_ENABLED="true"
    And the "ollama" container is running
    And lib_docker.ollama_gpu_status reports "cuda"
    When doctor.check(repo_root) runs
    Then the output includes "GPU acceleration confirmed: Ollama reports library=cuda."
    And there are no GPU-related warnings

  # doctor.py:186-204 - the cheap, filesystem-only part of
  # check_state_repo.py's checks (specs/ presence, stray TEMPLATE-*.md) now
  # also runs inside doctor.py itself (GH issue #60).
  @automatable
  Scenario: Missing specs/ directory in the state repo is a WARNING, not an ERROR
    Given STATE_REPO_PATH points at an existing directory with no "specs" subdirectory
    When doctor.check(repo_root) runs
    Then a WARNING item mentions "no 'specs' directory yet"
    And the result has_errors is False
