Feature: Guided setup wizard (setup_llm.py / setup_all.py / setup_project.py)
  As a new operator, running the guided setup wizard walks me through picking
  an LLM provider, configuring project/budget settings, and starting the
  stack - re-runs prefill whatever I already configured, bad steps can be
  retried without losing earlier progress, and Ctrl-C during a step does not
  leave setup_all.py silently swallowing the interrupt as an ordinary failure.

  Background:
    # setup_llm.py:826-843 - main() always presents the same 4-way menu
    # before doing anything else.
    Given a fresh checkout with no ".env" file

  # setup_llm.py:845-854 - main() dispatches on the numbered choice; choice 4
  # is the only one that never calls out to a cloud provider's API.
  @manual-qa
  Scenario Outline: Selecting an LLM provider drives a different setup flow
    When I run "python3 setup_llm.py" and choose provider "<choice>"
    Then the "<flow>" flow runs
    And a live test request is sent through the LiteLLM proxy after config is written

    Examples:
      | choice | flow                                      |
      | 1      | Google Gemini cloud (fetch_gemini_models)  |
      | 2      | Anthropic Claude cloud (fetch_anthropic_models) |
      | 3      | OpenAI cloud (fetch_openai_models)         |
      | 4      | Local / Ollama (run_local_provider)        |

  # setup_llm.py:524-539 (current_model_for_role) and :450-475 (select_model):
  # a model already configured in litellm.yaml / the provider's own
  # model-templates file is shown as "(current)" and used as the Enter-key
  # default, even if it's no longer in the freshly fetched/curated list.
  @automatable
  Scenario: Re-running the wizard prefills the previously configured model
    Given "config/model-templates/litellm.cloud-gemini.yaml" already configures
      "scrum-po" with model "gemini-1.5-pro"
    When select_model is called for role "scrum-po" with a freshly fetched
      options list that does not include "gemini-1.5-pro"
    Then "gemini-1.5-pro" is marked "(current)" and offered as the default
      on an empty Enter keypress

  # setup_llm.py:262-275 (current_interaction_level_choice) - re-running the
  # wizard must default the Human Interaction Level prompt to whatever
  # INTERACTION_LEVEL is already set in .env, not always back to "1" (Product).
  @automatable
  Scenario: Re-running the wizard prefills the previously configured interaction level
    Given ".env" already has INTERACTION_LEVEL="CEO"
    When current_interaction_level_choice(env_path) is called
    Then it returns "3" (the numbered choice mapping to "CEO")

  # setup_llm.py:566-574 (gpu_default_enable) - an explicit prior choice
  # (OLLAMA_GPU_ENABLED already "true"/"false") wins over a fresh nvidia-smi
  # detection result; detection only drives the default on first-time setup.
  @automatable
  Scenario: Re-running the wizard keeps a deliberate GPU choice over fresh detection
    Given ".env" already has OLLAMA_GPU_ENABLED="false"
    And a usable NVIDIA GPU is detected on this machine this run
    When gpu_default_enable(gpu_detected=True, current_value="false") is called
    Then it returns False, not True

  # setup_llm.py:299-303 - prompt_text with the email pattern
  # r"^[^@\s]+@[^@\s]+\.[^@\s]+$" re-prompts on a non-matching value instead
  # of accepting it.
  @automatable
  Scenario: Retry on a bad step - invalid git email is rejected and re-prompted
    Given the Git identity prompt is showing
    When I type "not-an-email" for "Git user email"
    Then "Please enter a valid email address." is printed
    And I am prompted again for "Git user email"
    When I then type "devteam@example.com"
    Then GIT_USER_EMAIL is written as "devteam@example.com"

  # setup_all.py:87-98 (run_guided_step) - a step that raises SystemExit(1)
  # (e.g. setup_llm.py dying on a bad API key) is reported and the operator
  # is offered a retry loop, rather than aborting the whole guided setup.
  # See tests/test_setup_all.py::TestRunGuidedStep::test_retries_until_success.
  @automatable
  Scenario: Retry on a bad step - a failing step is retried until it succeeds
    Given a setup step that fails with SystemExit(1) on its first two attempts
      and succeeds on the third
    When run_guided_step("A step", step) is called and the operator answers
      "y" (retry) each time it is asked
    Then run_guided_step ultimately returns True
    And the step was attempted exactly 3 times

  # setup_all.py:64-84 (run_step) - unlike a plain failing step (caught and
  # reported), KeyboardInterrupt is explicitly re-raised, not swallowed as
  # just another failed step. See
  # tests/test_setup_all.py::TestRunStep::test_keyboard_interrupt_propagates.
  @automatable
  Scenario: Ctrl-C during a guided setup step propagates instead of being treated as a failure
    Given a setup step that raises KeyboardInterrupt when run
    When run_step("A step", step) is called
    Then the KeyboardInterrupt propagates out of run_step
    And it is not reported as "(A step) failed unexpectedly"
