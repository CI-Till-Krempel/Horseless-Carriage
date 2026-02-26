# agents/scrum_team/agent.py
import os
from typing import Optional, Union, Dict, Any

import litellm
from google.genai import types
from google.adk.agents.llm_agent import LlmAgent, CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models.lite_llm import LiteLlm

from .prompts import (
    ORCHESTRATOR_PROMPT,
    PO_PROMPT,
    SM_PROMPT,
    DEV_PROMPT,
    QA_PROMPT,
    ARCH_PROMPT,
)
from .tools import (
    init_scrum_state,
    log_decision,
    upsert_backlog_item,
    set_priority,
    add_impediment,
    add_retro_action,
    plan_sprint_backlog_item,
    git_push,
    gh_pr_create,
    gh_pr_status,
    gh_pr_checks,
    gh_pr_comment,
    gh_pr_review,
    gh_release_create,
    write_file,
    create_from_template,
    configure_github_repo,
    configure_github_app,
    seed_repository,
    repo_status,
    save_state_to_repo,
    load_state_from_repo,
    update_budgets,
    get_budget_status,
    log_token_usage,
    create_sprint_report,
    create_release_pr,
    gh_pr_check_logs,
    create_litellm_virtual_key,
)

# --- LiteLLM Proxy wiring ---
# If LITELLM_PROXY_API_BASE is set, we assume proxy mode.
if os.getenv("LITELLM_PROXY_API_BASE"):
    litellm.use_litellm_proxy = True
    # LiteLLM reads base/key from env:
    # LITELLM_PROXY_API_BASE, LITELLM_PROXY_API_KEY

def inject_litellm_key_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: Injects agent-specific LiteLLM key if available in state.
    """
    state = callback_context.state
    agent_name = callback_context.agent_name
    keys = state.get("litellm_keys", {})
    agent_key = keys.get(agent_name)
    
    if agent_key:
        # LiteLLM acompletion respects api_key in kwargs. 
        # ADK's LiteLlm model passes its _additional_args to acompletion.
        # Since we can't easily modify _additional_args of the model instance per request,
        # and LiteLlm.generate_content_async doesn't look at llm_request for the api_key,
        # we have to set the global litellm.api_key for this request.
        # Since ADK runs sequentially, this is mostly safe.
        litellm.api_key = agent_key
    else:
        # Fallback to the proxy's main key if no agent-specific key
        litellm.api_key = os.getenv("LITELLM_PROXY_API_KEY")

# Set global num_retries to handle transient 429 errors from providers
litellm.num_retries = 3

def M(alias: str) -> LiteLlm:
    """
    Convenience helper to create a LiteLlm model reference.
    Use aliases from litellm.yaml when proxy mode is enabled.
    """
    return LiteLlm(model=alias)

# --- Budget Enforcement Callbacks ---

def enforce_budget_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """
    BeforeAgentCallback: Checks if the team is over budget before allowing an agent to start.
    """
    state = callback_context.state
    budgets = state.get("budgets", {})
    usage = state.get("token_usage", {})
    
    total_budget = budgets.get("total", 0)
    total_usage = usage.get("total", 0)
    
    if total_budget > 0 and total_usage >= total_budget:
        msg = (
            f"🚫 [BUDGET EXCEEDED] Total token budget ({total_budget}) reached. "
            f"Current usage: {total_usage}. Agent execution halted. "
            "Please trigger a Sprint Review/Retrospective or increase the budget via `update_budgets`."
        )
        return types.Content(role="model", parts=[types.Part(text=msg)])
    return None

def check_model_budget_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    """
    BeforeModelCallback: Checks budget before each individual LLM call.
    """
    state = callback_context.state
    budgets = state.get("budgets", {})
    usage = state.get("token_usage", {})
    
    total_budget = budgets.get("total", 0)
    total_usage = usage.get("total", 0)

    if total_budget > 0 and total_usage >= total_budget:
        msg = f"🚫 [MODEL BLOCKED] Budget exceeded ({total_usage}/{total_budget})."
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )
    return None

# --- Sub agents (role specialists) ---
product_owner = LlmAgent(
    name="ProductOwner",
    model=M("scrum-po"),
    description="Owns product vision/goals, backlog ordering, acceptance criteria, scope tradeoffs.",
    instruction=PO_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        upsert_backlog_item,
        set_priority,
        log_decision,
        create_from_template,
        write_file,
        gh_release_create,
        create_sprint_report,
        create_release_pr,
    ],
)

scrum_master = LlmAgent(
    name="ScrumMaster",
    model=M("scrum-sm"),
    description="Facilitates Scrum events, removes impediments, improves process, tracks actions.",
    instruction=SM_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        add_impediment,
        add_retro_action,
        log_decision,
        update_budgets,
        get_budget_status,
        log_token_usage,
        gh_pr_status,
        gh_pr_checks,
        gh_pr_comment,
        gh_pr_review,
    ],
)

dev_team = LlmAgent(
    name="DevTeam",
    model=M("scrum-dev"),
    description="Plans/estimates/implements stories, owns technical decisions, ensures DoD, creates sprint plan.",
    instruction=DEV_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        plan_sprint_backlog_item,
        add_impediment,
        log_decision,
        write_file,
        create_from_template,
        git_push,
        gh_pr_create,
        gh_pr_status,
        gh_pr_checks,
        gh_pr_comment,
        gh_pr_review,
        gh_pr_check_logs,
    ],
)

qa_agent = LlmAgent(
    name="QA",
    model=M("scrum-qa"),
    description="Improves test strategy and quality signals; proposes test cases and automation.",
    instruction=QA_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        add_impediment,
        log_decision,
        gh_pr_comment,
        gh_pr_review,
    ],
)

architect = LlmAgent(
    name="Architect",
    model=M("scrum-arch"),
    description="Identifies architectural risks, proposes tradeoffs, writes ADR-like notes.",
    instruction=ARCH_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        log_decision,
        gh_pr_comment,
        gh_pr_review,
    ],
)

# --- Root orchestrator (delegates to sub_agents) ---
root_agent = LlmAgent(
    name="ScrumOrchestrator",
    model=M("scrum-orchestrator"),
    description="Routes requests within Scrum team and maintains shared artifacts in session.state and the configured GitHub repo.",
    instruction=ORCHESTRATOR_PROMPT,
    before_agent_callback=enforce_budget_callback,
    before_model_callback=[inject_litellm_key_callback, check_model_budget_callback],
    tools=[
        init_scrum_state,
        log_decision,
        configure_github_repo,
        configure_github_app,
        seed_repository,
        repo_status,
        save_state_to_repo,
        load_state_from_repo,
        git_push,
        gh_pr_create,
        gh_pr_status,
        gh_pr_checks,
        gh_pr_comment,
        gh_pr_review,
        gh_pr_check_logs,
        write_file,
        create_from_template,
        update_budgets,
        get_budget_status,
        log_token_usage,
        create_sprint_report,
        create_release_pr,
        create_litellm_virtual_key,
    ],
    sub_agents=[product_owner, scrum_master, dev_team, qa_agent, architect],
)