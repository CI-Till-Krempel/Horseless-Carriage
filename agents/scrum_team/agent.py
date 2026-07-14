# agents/scrum_team/agent.py
import os
import requests
import logging
import sys
from typing import Optional, Union, Dict, Any

import litellm

# --- Logging Setup ---
def _setup_logging():
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Ensure sessions directory exists for the log file
    log_file = os.path.join("/app/sessions", f"agent-{os.getenv('SESSION_ID', 'default')}.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    # We set root logger to DEBUG to allow all logs to be captured by handlers
    root_logger.setLevel(logging.DEBUG)
    
    # Function to set level for all stream handlers in a logger
    def set_stream_handlers_level(logger, level):
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(level)

    log_level = getattr(logging, log_level_str, logging.INFO)

    # Apply to root logger
    set_stream_handlers_level(root_logger, log_level)

    # Apply to all existing loggers (LiteLLM etc. often have their own handlers)
    for name in logging.root.manager.loggerDict:
        set_stream_handlers_level(logging.getLogger(name), log_level)

    # Add file handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    root_logger.addHandler(fh)
    
    # LiteLLM specific logging
    if log_level_str == "DEBUG":
        litellm.set_verbose = True
    
    root_logger.info(f"Logging initialized. Console level: {log_level_str}, File: {log_file}")

_setup_logging()
from google.genai import types
from google.adk.agents.llm_agent import LlmAgent, CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models.lite_llm import LiteLlm

from .helpers import get_process_overhead_percentage
from .prompts import (
    ORCHESTRATOR_PROMPT,
    PO_PROMPT,
    SM_PROMPT,
    DEV_PROMPT,
    QA_PROMPT,
    ARCH_PROMPT,
    QUALITY_GUARDIAN_PROMPT,
)
from .tools import (
    init_scrum_state,
    log_decision,
    upsert_story,
    upsert_epic,
    update_roadmap,
    plan_backlog_item,
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
    read_doc,
    upsert_prd,
    upsert_srs,
)
from .tools.quality import (
    calculate_kpis,
    update_sprint_report as update_sprint_report_with_kpis,
)
from .tools.workflow import (
    generate_workflow_diagram,
    gather_workflow_improvement_proposals,
)
from .tools.budget import (
    calculate_cost_breakdown,
    recommend_sprint_budget,
    optimize_process_for_budget,
)
from .state import ScrumState, Budgets, TokenUsage

# --- LiteLLM Proxy wiring ---
# If LITELLM_PROXY_API_BASE is set, we assume proxy mode.
if os.getenv("LITELLM_PROXY_API_BASE"):
    litellm.use_litellm_proxy = True
    # Security hardening: Restrict proxy access to localhost
    litellm.allowed_ips = ["127.0.0.1"]
    # LiteLLM reads base/key from env:
    # LITELLM_PROXY_API_BASE, LITELLM_PROXY_API_KEY

def get_model_name(role: str) -> str:
    """Gets the model name for a given role from environment variables."""
    return os.getenv(f"SCRUM_{role.upper()}_MODEL", f"scrum-{role}")

def get_scrum_state(context_state) -> ScrumState:
    """Constructs a ScrumState object from the context state."""
    if context_state is None:
        return ScrumState()

    if isinstance(context_state, ScrumState):
        return context_state

    # Try various ways to get a dictionary representation
    data = {}
    if hasattr(context_state, "to_dict") and callable(context_state.to_dict):
        try:
            data = context_state.to_dict()
        except Exception:
            data = {}
    
    if not data:
        if hasattr(context_state, "model_dump") and callable(context_state.model_dump):
            data = context_state.model_dump()
        elif hasattr(context_state, "dict") and callable(context_state.dict):
            data = context_state.dict()
        elif hasattr(context_state, "copy") and callable(context_state.copy):
            data = context_state.copy()
        elif hasattr(context_state, "items") and callable(context_state.items):
            data = dict(context_state.items())
        else:
            # Fallback for ADK State object which might not have .copy()
            try:
                data = dict(context_state)
            except (TypeError, ValueError, KeyError):
                data = {}
                try:
                    # If context_state is iterable, try to build a dict
                    for k in context_state:
                        data[k] = context_state[k]
                except Exception:
                    pass
    
    # Map sub-models
    budgets_data = data.get("budgets") or {}
    token_usage_data = data.get("token_usage") or {}
    
    if isinstance(budgets_data, dict):
        data["budgets"] = Budgets(**budgets_data)
    if isinstance(token_usage_data, dict):
        data["token_usage"] = TokenUsage(**token_usage_data)
        
    return ScrumState(**data)

def inject_litellm_key_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: Injects agent-specific LiteLLM key if available in state.
    """
    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name
    agent_key = state.litellm_keys.get(agent_name)
    
    if agent_key:
        litellm.api_key = agent_key
    else:
        litellm.api_key = os.getenv("LITELLM_PROXY_API_KEY")

# --- Budget Enforcement Callbacks ---

def check_cost_budget_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    """
    BeforeModelCallback: Checks if the team is over budget before allowing an agent to start.
    This is a real-time check against the LiteLLM proxy.
    """
    state = get_scrum_state(callback_context.state)
    budget_limit = state.budgets.total_usd
    
    if budget_limit <= 0:
        return None

    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE")
    budget_id = "scrum-sprint-budget"

    if not master_key or not proxy_base:
        return None

    try:
        response = requests.post(
            f"{proxy_base}/budget/info",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json={"budgets": [budget_id]},
            timeout=5
        )
        response.raise_for_status()
        budget_info_list = response.json()
        current_spend = 0.0
        if budget_info_list and isinstance(budget_info_list, list) and len(budget_info_list) > 0:
            current_spend = budget_info_list[0].get("spend", 0.0)

        if current_spend >= budget_limit:
            msg = (
                f"🚫 [BUDGET EXCEEDED] Total USD budget (${budget_limit:.2f}) reached. "
                f"Current spend: ${current_spend:.2f}. Agent execution halted."
            )
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=msg)]),
                model_version=llm_request.model or "unknown"
            )
    except requests.RequestException as e:
        msg = f"❌ [BUDGET ERROR] Could not verify budget status with LiteLLM proxy: {e}. Agent execution halted to prevent unmonitored spending."
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

    return None

def update_token_usage_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """
    AfterModelCallback: Automatically updates the token usage in session state.
    """
    if not llm_response.usage_metadata:
        return None
    
    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name
    
    new_tokens = llm_response.usage_metadata.total_token_count or 0
    
    if new_tokens > 0:
        state.token_usage.total += new_tokens
        state.token_usage.agents[agent_name] = state.token_usage.agents.get(agent_name, 0) + new_tokens
        
        # Update the state object with the new usage values
        try:
            callback_context.state["token_usage"] = state.token_usage.model_dump()
        except (TypeError, KeyError):
            try:
                setattr(callback_context.state, "token_usage", state.token_usage.model_dump())
            except Exception:
                pass

    return None

# --- Common Agent Configuration ---
COMMON_AGENT_CALLBACKS = {
    "before_model_callback": [inject_litellm_key_callback, check_cost_budget_callback],
    "after_model_callback": update_token_usage_callback,
}

# --- Sub agents (role specialists) ---
product_owner = LlmAgent(
    name="ProductOwner",
    model=LiteLlm(get_model_name("po")),
    description="Owns product vision/goals, backlog ordering, acceptance criteria, scope tradeoffs.",
    instruction=PO_PROMPT,
    tools=[
        init_scrum_state,
        upsert_story,
        upsert_epic,
        update_roadmap,
        plan_backlog_item,
        set_priority,
        log_decision,
        create_from_template,
        gh_release_create,
        create_sprint_report,
        create_release_pr,
        read_doc,
        upsert_prd,
        upsert_srs,
    ],
    **COMMON_AGENT_CALLBACKS,
)

scrum_master = LlmAgent(
    name="ScrumMaster",
    model=LiteLlm(get_model_name("sm")),
    description="Facilitates Scrum events, removes impediments, improves process, tracks actions.",
    instruction=SM_PROMPT,
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
        generate_workflow_diagram,
        gather_workflow_improvement_proposals,
        calculate_cost_breakdown,
        recommend_sprint_budget,
        optimize_process_for_budget,
    ],
    **COMMON_AGENT_CALLBACKS,
)

dev_team = LlmAgent(
    name="DevTeam",
    model=LiteLlm(get_model_name("dev")),
    description="Plans/estimates/implements stories, owns technical decisions, ensures DoD, creates sprint plan.",
    instruction=DEV_PROMPT,
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
    **COMMON_AGENT_CALLBACKS,
)

qa_agent = LlmAgent(
    name="QA",
    model=LiteLlm(get_model_name("qa")),
    description="Improves test strategy and quality signals; proposes test cases and automation.",
    instruction=QA_PROMPT,
    tools=[
        init_scrum_state,
        add_impediment,
        log_decision,
        gh_pr_comment,
        gh_pr_review,
    ],
    **COMMON_AGENT_CALLBACKS,
)

architect = LlmAgent(
    name="Architect",
    model=LiteLlm(get_model_name("arch")),
    description="Identifies architectural risks, proposes tradeoffs, writes ADR-like notes.",
    instruction=ARCH_PROMPT,
    tools=[
        init_scrum_state,
        log_decision,
        gh_pr_comment,
        gh_pr_review,
        write_file,
    ],
    **COMMON_AGENT_CALLBACKS,
)

quality_guardian = LlmAgent(
    name="QualityGuardian",
    model=LiteLlm(get_model_name("quality")),
    description="Objectively assess and report on team effectiveness, result quality, maintainability, and security KPIs.",
    instruction=QUALITY_GUARDIAN_PROMPT,
    tools=[
        calculate_kpis,
        update_sprint_report_with_kpis,
    ],
    **COMMON_AGENT_CALLBACKS,
)

# --- Root orchestrator (delegates to sub_agents) ---
root_agent = LlmAgent(
    name="ScrumOrchestrator",
    model=LiteLlm(get_model_name("orchestrator")),
    description="Routes requests within Scrum team and maintains shared artifacts in session.state and the configured GitHub repo.",
    instruction=ORCHESTRATOR_PROMPT,
    tools=[
        # General state, setup, and high-level management tools
        init_scrum_state,
        log_decision,
        configure_github_repo,
        configure_github_app,
        seed_repository,
        repo_status,
        save_state_to_repo,
        load_state_from_repo,
        create_litellm_virtual_key,
        update_budgets,
        get_budget_status,
        log_token_usage,
    ],
    sub_agents=[product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian],
    **COMMON_AGENT_CALLBACKS,
)