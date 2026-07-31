Feature: Budget enforcement (dual-layer token + USD)
  check_cost_budget_callback (agents/scrum_team/agent.py:429-559) is a
  BeforeModelCallback on every sub-agent that enforces, in code, three
  independent guardrails before any LLM call is allowed to proceed: (0) every
  non-Orchestrator agent must already have its own budget-capped LiteLLM
  virtual key, (1) a local per-sprint token ceiling, and (2) a remote USD
  ceiling checked live against the LiteLLM proxy - except for a local/Ollama
  setup, where the USD check is skipped outright rather than left as a
  guardrail it cannot actually provide.

  Background:
    # agent.py:429-436 - get_scrum_state(callback_context.state) and
    # callback_context.agent_name feed every check below.
    Given a sub-agent is about to make an LLM call via check_cost_budget_callback

  # agent.py:437-457 - the Orchestrator is exempt (it needs at least one call
  # to run the setup wizard that creates every other agent's key); every
  # other agent with no entry in state.litellm_keys is hard-blocked, not
  # allowed to silently fall back to the unscoped LITELLM_PROXY_API_KEY.
  @automatable
  Scenario: An agent with no budget-capped virtual key yet is hard-blocked
    Given LITELLM_MASTER_KEY and LITELLM_PROXY_API_BASE are both set
    And agent_name is "DevTeam"
    And state.litellm_keys has no entry for "DevTeam"
    When check_cost_budget_callback runs
    Then it returns a canned LlmResponse containing "[NO BUDGET-CAPPED KEY]"
    And the real model is never called
    And the fix instruction names "create_litellm_virtual_key('DevTeam', ...)"

  # agent.py:449 - "agent_name != ScrumOrchestrator" is the exemption; the
  # Orchestrator itself is allowed through with no virtual key yet.
  @automatable
  Scenario: The Orchestrator is exempt from the no-virtual-key block
    Given LITELLM_MASTER_KEY and LITELLM_PROXY_API_BASE are both set
    And agent_name is "ScrumOrchestrator"
    And state.litellm_keys is empty
    When check_cost_budget_callback runs
    Then it returns None (the call proceeds to the model)

  # agent.py:459-479 - token_limit falls back to SPRINT_TOKEN_BUDGET (env)
  # if state.budgets.total is <= 0; usage >= limit halts and, via
  # _sync_roadmap_on_exhaustion_once, syncs specs/ROADMAP.md exactly once
  # per sprint before returning the canned halt response.
  @automatable
  Scenario: Token budget exhaustion halts the agent and syncs the roadmap once
    Given the agent already has a virtual key
    And state.token_usage.total >= state.budgets.total (or the SPRINT_TOKEN_BUDGET fallback)
    When check_cost_budget_callback runs
    Then it returns a canned LlmResponse containing "[TOKEN BUDGET EXCEEDED]"
    And sync_all_active_stories_to_roadmap + a git push run exactly once for this sprint
    And a blocking interaction of kind "critical_error" is recorded

  # agent.py:482-498 (LLM_LOCAL_PROVIDER == "true") - GH issue #75/#81: a
  # self-hosted Ollama model has no real per-token price, so the USD check
  # would trivially pass forever; it is skipped, not silently "passed".
  @automatable
  Scenario: USD budget check is skipped outright for a local/Ollama setup
    Given the agent already has a virtual key and is under the token budget
    And LLM_LOCAL_PROVIDER="true"
    When check_cost_budget_callback runs
    Then no request is sent to the LiteLLM proxy's /budget/info endpoint
    And check_cost_budget_callback returns None (the call proceeds)
    And a one-time info log notes only the token budget applies this sprint

  # agent.py:526-534, 550-557 - a network error talking to the proxy's
  # /budget/info endpoint fails CLOSED (halts the agent), it does not treat
  # "couldn't check" as "must be fine".
  @automatable
  Scenario: An unreachable LiteLLM proxy fails closed, not open
    Given the agent already has a virtual key, is under the token budget,
      and LLM_LOCAL_PROVIDER is not "true"
    And a request to "{proxy_base}/budget/info" raises requests.RequestException
      (proxy unreachable)
    When check_cost_budget_callback runs
    Then it returns a canned LlmResponse containing "[BUDGET ERROR]"
    And "Agent execution halted to prevent unmonitored spending" is in the message
    And a blocking interaction of kind "critical_error" is recorded

  # agent.py:539-549 - the mirror-image real-exceedance case, once the proxy
  # IS reachable: current spend >= budget_limit halts exactly like the
  # unreachable-proxy case above.
  @automatable
  Scenario: USD budget actually exceeded halts the agent
    Given the agent already has a virtual key, is under the token budget,
      and LLM_LOCAL_PROVIDER is not "true"
    And the proxy reports scrum-sprint-budget's current spend >= budget_limit
    When check_cost_budget_callback runs
    Then it returns a canned LlmResponse containing "[USD BUDGET EXCEEDED]"

  # agent.py:511-518 - a configuration bug (budget_limit resolves to <= 0
  # even after every fallback) is its own distinct, fail-closed error rather
  # than silently treated as "no limit".
  @automatable
  Scenario: A zero/negative resolved USD budget is a configuration error, not "unlimited"
    Given the agent already has a virtual key, is under the token budget,
      and LLM_LOCAL_PROVIDER is not "true"
    And state.budgets.total_usd and TOTAL_USD_BUDGET/SPRINT_USD_BUDGET all resolve to <= 0
    When check_cost_budget_callback runs
    Then it returns a canned LlmResponse containing "[CONFIGURATION ERROR]"

  # helpers.py:11-47 (get_env_with_deprecated_fallback) - TOTAL_USD_BUDGET is
  # the canonical name (GH issue #81); SPRINT_USD_BUDGET is still honored so
  # an existing .env using the old name is never silently reverted to a
  # hardcoded default.
  @automatable
  Scenario: The deprecated SPRINT_USD_BUDGET env var still works, with a one-time warning
    Given TOTAL_USD_BUDGET is unset and SPRINT_USD_BUDGET="25.0" is set
    When get_env_with_deprecated_fallback("TOTAL_USD_BUDGET", "SPRINT_USD_BUDGET") is called
    Then it returns "25.0"
    And a one-time-per-process deprecation warning is printed to stderr
