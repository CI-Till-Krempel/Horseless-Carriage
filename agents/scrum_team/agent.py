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
logger = logging.getLogger("scrum-team")

# --- Monkey patch for OpenAI and LiteLLM APIError incompatibility ---
try:
    import openai
    import httpx
    import litellm.exceptions
    
    # 1. Patch openai.APIError
    _orig_openai_apierror_init = openai.APIError.__init__
    def _patched_openai_apierror_init(self, message, request=None, *, body=None, **kwargs):
        if not isinstance(request, httpx.Request):
            request = httpx.Request("GET", "http://localhost")
        kwargs.pop("response", None)
        kwargs.pop("status_code", None)
        return _orig_openai_apierror_init(self, message, request, body=body)
    openai.APIError.__init__ = _patched_openai_apierror_init

    # 2. Patch openai.APIConnectionError
    _orig_openai_connerror_init = openai.APIConnectionError.__init__
    def _patched_openai_connerror_init(self, *args, **kwargs):
        if "request" not in kwargs and len(args) < 1: # It's kw-only in some versions
            kwargs["request"] = httpx.Request("GET", "http://localhost")
        kwargs.pop("response", None)
        return _orig_openai_connerror_init(self, **kwargs) if not args else _orig_openai_connerror_init(self, *args, **kwargs)
    openai.APIConnectionError.__init__ = _patched_openai_connerror_init

    # 3. Patch litellm.exceptions.APIError
    _orig_litellm_apierror_init = litellm.exceptions.APIError.__init__
    def _patched_litellm_apierror_init(self, status_code=500, *args, **kwargs):
        kwargs.pop("response", None)
        if not isinstance(status_code, int):
            return _orig_litellm_apierror_init(self, 500, status_code, *args, **kwargs)
        return _orig_litellm_apierror_init(self, status_code, *args, **kwargs)
    litellm.exceptions.APIError.__init__ = _patched_litellm_apierror_init

    # 4. Patch litellm.exceptions.APIConnectionError
    _orig_litellm_connerror_init = litellm.exceptions.APIConnectionError.__init__
    def _patched_litellm_connerror_init(self, *args, **kwargs):
        kwargs.pop("response", None)
        return _orig_litellm_connerror_init(self, *args, **kwargs)
    litellm.exceptions.APIConnectionError.__init__ = _patched_litellm_connerror_init

    # 5. Patch LiteLLMClient.acompletion to handle Gemini safety blocks gracefully
    import google.adk.models.lite_llm as adk_litellm
    _orig_adk_acompletion = adk_litellm.LiteLLMClient.acompletion

    async def _patched_adk_acompletion(self, *args, **kwargs):
        try:
            return await _orig_adk_acompletion(self, *args, **kwargs)
        except Exception as e:
            # Check for the specific 'no choices' error which usually indicates a safety block
            if "no 'choices'" in str(e):
                logger.warning(f"Detected Gemini safety block: {e}")
                from litellm.utils import ModelResponse, Choices, Message
                model_name = kwargs.get("model") or "unknown-gemini"
                
                blocked_msg = "⚠️ [SAFETY BLOCK] The request was blocked by Gemini's safety filters. Please try rephrasing your request or avoiding sensitive topics."
                
                response = ModelResponse(
                    id="safety-block",
                    choices=[Choices(
                        finish_reason="safety", 
                        index=0, 
                        message=Message(content=blocked_msg)
                    )],
                    created=0,
                    model=model_name,
                    object="chat.completion"
                )
                
                if kwargs.get("stream"):
                    # Return an async iterable for streaming calls
                    async def _async_gen():
                        yield response
                    return _async_gen()
                
                return response
            raise e

    adk_litellm.LiteLLMClient.acompletion = _patched_adk_acompletion

    logger.info("Applied robust monkey-patches for OpenAI and LiteLLM exceptions and safety blocks")
except Exception as e:
    logger.warning(f"Failed to apply monkey-patches: {e}")

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
    list_docs,
    upsert_prd,
    upsert_srs,
    upsert_adr,
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

    # Filter out None values to allow Pydantic defaults to kick in
    data = {k: v for k, v in data.items() if v is not None}
        
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
    This is a real-time check against the LiteLLM proxy for USD and local state for tokens.
    """
    state = get_scrum_state(callback_context.state)
    
    # 1. Check Token Budget (Local Guardrail)
    token_limit = state.budgets.total
    # Fallback to environment if state is missing/zero
    if token_limit <= 0:
        try:
            token_limit = int(os.environ.get("SPRINT_TOKEN_BUDGET", 1000000))
        except (ValueError, TypeError):
            token_limit = 1000000
            
    token_usage = state.token_usage.total
    if token_limit > 0 and token_usage >= token_limit:
        msg = (
            f"🚫 [TOKEN BUDGET EXCEEDED] Sprint token limit ({token_limit:,}) reached. "
            f"Current usage: {token_usage:,}. Agent execution halted."
        )
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

    # 2. Check USD Budget (Remote Guardrail via LiteLLM Proxy)
    budget_limit = state.budgets.total_usd
    # Fallback to environment if state is missing/zero
    if budget_limit <= 0:
        try:
            budget_limit = float(os.environ.get("SPRINT_USD_BUDGET", 10.0))
        except (ValueError, TypeError):
            budget_limit = 10.0

    if budget_limit <= 0:
        # If still 0, something is wrong with configuration
        msg = "❌ [CONFIGURATION ERROR] No USD budget limit set for the sprint. Agent execution halted for safety."
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE")
    budget_id = "scrum-sprint-budget"

    if not master_key or not proxy_base:
        # Local check only if proxy is unavailable
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
                f"🚫 [USD BUDGET EXCEEDED] Total USD budget (${budget_limit:.2f}) reached. "
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

# --- History Management Callbacks ---

def history_management_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: Injects and synchronizes conversation history for the Orchestrator.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return

    state = get_scrum_state(callback_context.state)
    
    # 1. Injection logic (only for the very first turn of a run)
    if not llm_request.previous_interaction_id:
        if state.messages:
            history_contents = []
            for msg in state.messages:
                # Ensure we have a role and content
                role = msg.get("role", "user")
                content_text = msg.get("content", "")
                if content_text:
                    history_contents.append(types.Content(role=role, parts=[types.Part(text=content_text)]))
            
            if history_contents:
                # Check if we already have the same messages at the start (avoiding duplicates on resume)
                # This is a safety check.
                llm_request.contents = history_contents + llm_request.contents
                logger.info(f"Resumed {len(history_contents)} messages from conversation history.")

    # 2. Sync state.messages with the current full contents to keep it fresh
    new_history = []
    for content in llm_request.contents:
        text = "".join(p.text for p in content.parts if p.text)
        if text:
            new_history.append({"role": content.role, "content": text})
    
    if new_history:
        try:
            callback_context.state["messages"] = new_history
        except (TypeError, KeyError):
            try:
                setattr(callback_context.state, "messages", new_history)
            except Exception:
                pass

def history_management_after_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """
    AfterModelCallback: Saves the model response to the conversation history.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return None
        
    if not llm_response.content:
        return None
        
    text = "".join(p.text for p in llm_response.content.parts if p.text)
    if text:
        state = get_scrum_state(callback_context.state)
        history = list(state.messages)
        # Avoid duplicate appending if called multiple times for the same response
        if not history or history[-1].get("content") != text:
            history.append({"role": "model", "content": text})
            try:
                callback_context.state["messages"] = history
            except (TypeError, KeyError):
                try:
                    setattr(callback_context.state, "messages", history)
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
        list_docs,
        upsert_prd,
        upsert_srs,
        upsert_adr,
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
        read_doc,
        list_docs,
        create_from_template,
        git_push,
        gh_pr_create,
        gh_pr_status,
        gh_pr_checks,
        gh_pr_comment,
        gh_pr_review,
        gh_pr_check_logs,
        upsert_adr,
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
        upsert_adr,
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
        read_doc,
        list_docs,
        upsert_adr,
    ],
    sub_agents=[product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian],
    before_model_callback=[
        inject_litellm_key_callback, 
        check_cost_budget_callback, 
        history_management_callback
    ],
    after_model_callback=[
        update_token_usage_callback, 
        history_management_after_callback
    ],
)