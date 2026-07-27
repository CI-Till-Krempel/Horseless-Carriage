# agents/scrum_team/agent.py
import os
import requests
import logging
import sys
from typing import Optional, Union, Dict, Any, List

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
    
    # Function to set level for all stream handlers in a logger and add noise filter
    class ConsoleNoiseFilter(logging.Filter):
        def filter(self, record):
            # If we are in DEBUG mode globally, show everything
            if log_level_str == "DEBUG":
                return True
            # Silence specific noisy loggers at INFO/DEBUG level on console
            noisy_loggers = ["LiteLLM", "openai", "httpx", "urllib3", "google.adk"]
            if any(record.name.startswith(n) for n in noisy_loggers):
                return record.levelno >= logging.WARNING
            return True

    def set_stream_handlers_level(logger, level):
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(level)
                handler.addFilter(ConsoleNoiseFilter())

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
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from .helpers import get_process_overhead_percentage, is_story_done, get_interaction_level
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
    upsert_issue,
    update_roadmap,
    plan_backlog_item,
    set_priority,
    advance_story_stage,
    add_impediment,
    add_retro_action,
    record_human_approval,
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
    reset_sprint_budget,
    log_story_tokens,
    create_sprint_report,
    create_release_pr,
    start_feature_branch,
    mark_pr_ready_for_review,
    merge_story_pr,
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
    check_build,
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
# Not agent-callable tools (no LlmAgent lists them) - used only by
# _sync_and_commit_roadmap_on_exhaustion below, the mechanical last-gasp
# roadmap sync triggered when the sprint budget runs out mid-turn.
from .tools.requirements import sync_all_active_stories_to_roadmap
from .tools.base import _configured_repo_root, _run
from .state import ScrumState, Budgets, TokenUsage

# --- LiteLLM Proxy wiring ---
# If LITELLM_PROXY_API_BASE is set, we assume proxy mode.
if os.getenv("LITELLM_PROXY_API_BASE"):
    litellm.use_litellm_proxy = True
    litellm.api_base = os.getenv("LITELLM_PROXY_API_BASE")
    # Security hardening: Restrict proxy access
    # litellm.allowed_ips = ["127.0.0.1"] # This is for the proxy server, not client.
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

def _sync_and_commit_roadmap_on_exhaustion(callback_context: CallbackContext) -> None:
    """
    Mechanical, non-agent last-gasp action: once the budget guardrail below
    starts returning a canned "halted" LlmResponse instead of calling the
    model, NO agent - including Scrum Master - ever gets a real reasoning
    turn again this sprint (returning a non-None LlmResponse from a
    before_model_callback short-circuits the model call entirely; the same
    canned message just repeats on every subsequent call). A prompt
    instruction telling Scrum Master to "document the current state" would
    never actually be seen or acted on. So this runs here instead, in code,
    the moment exhaustion is first detected this sprint - syncing
    specs/ROADMAP.md to whatever every story's real stage is and committing
    it, so task status stays visible even though development just stopped.

    Best-effort: any failure here (no network, git error) must not prevent
    the caller from still returning its own canned budget-exceeded response.
    """
    try:
        # CallbackContext already exposes .state/.agent_name the same way
        # tool functions expect tool_context to (used elsewhere in this same
        # callback), so it can stand in for tool_context here.
        sync_all_active_stories_to_roadmap(callback_context)

        repo_root = str(_configured_repo_root(callback_context))
        branch_result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, tool_context=callback_context)
        current_branch = (branch_result.get("stdout") or "").strip()
        if current_branch and current_branch != "HEAD":
            git_push(
                branch=current_branch,
                commit_message="chore: sync roadmap - sprint budget exhausted",
                allow_protected=True,
                tool_context=callback_context,
            )
    except Exception as e:
        logging.getLogger(__name__).warning(f"Roadmap sync on budget exhaustion failed (non-fatal): {e}")


def _sync_roadmap_on_exhaustion_once(callback_context: CallbackContext) -> None:
    """
    Guards _sync_and_commit_roadmap_on_exhaustion so it runs exactly once per
    sprint - every call after the first exhaustion this sprint hits this same
    callback again (the canned response repeats on every subsequent turn), so
    without this guard it would redundantly re-sync/re-push on every single
    one of those. Cleared by reset_sprint_budget / the eval harness's
    per-sprint state_delta at the start of each new sprint, so exhaustion in
    a later sprint syncs again.
    """
    if callback_context.state.get("budget_exhaustion_synced"):
        return
    _sync_and_commit_roadmap_on_exhaustion(callback_context)
    callback_context.state["budget_exhaustion_synced"] = True


def check_cost_budget_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    """
    BeforeModelCallback: Checks if the team is over budget before allowing an agent to start.
    This is a real-time check against the LiteLLM proxy for USD and local state for tokens.
    """
    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name

    # 0. Require a budget-capped virtual key in proxy mode (Local Guardrail)
    # inject_litellm_key_callback falls back to LITELLM_PROXY_API_KEY when an
    # agent has no virtual key yet. That fallback key is not attached to
    # scrum-sprint-budget, so the USD check below (step 2) would be blind to
    # whatever it spends - and it may not be budget-capped at all (the
    # documented DB-wipe recovery flow points it at the unbounded
    # LITELLM_MASTER_KEY). Fail closed for every sub-agent rather than let it
    # spend on an unscoped key. The Orchestrator is exempt: it needs at least
    # one call to run the setup wizard that creates every other agent's key
    # in the first place.
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE")
    if master_key and proxy_base and agent_name != "ScrumOrchestrator" and not state.litellm_keys.get(agent_name):
        msg = (
            f"🚫 [NO BUDGET-CAPPED KEY] Agent '{agent_name}' has no LiteLLM virtual key yet. "
            f"Refusing to run on an unscoped fallback key - call create_litellm_virtual_key('{agent_name}', ...) first."
        )
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

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
        _sync_roadmap_on_exhaustion_once(callback_context)
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
            _sync_roadmap_on_exhaustion_once(callback_context)
            msg = (
                f"🚫 [USD BUDGET EXCEEDED] Total USD budget (${budget_limit:.2f}) reached. "
                f"Current spend: ${current_spend:.2f}. Agent execution halted."
            )
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=msg)]),
                model_version=llm_request.model or "unknown"
            )
    except requests.RequestException as e:
        _sync_roadmap_on_exhaustion_once(callback_context)
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

# --- Sprint Status Injection Callback ---

def sprint_status_injection_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: Injects current sprint and budget status for the Orchestrator's first message.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return

    # Only on the very first message of a run (no previous interaction)
    if llm_request.previous_interaction_id:
        return

    state = get_scrum_state(callback_context.state)
    
    sprint_goal = state.sprint_goal or "Not yet defined"
    
    # Calculate backlog progress
    backlog = state.sprint_backlog or []
    total_items = len(backlog)
    completed_items = len([i for i in backlog if is_story_done(i.get("status"))])
    
    # Budget info
    token_usage = state.token_usage.total
    token_limit = state.budgets.total
    usd_limit = state.budgets.total_usd
    
    # Identify active sprint status
    status_summary = f"""
[SYSTEM CONTEXT: CURRENT SPRINT & BUDGET STATUS]
- Sprint Goal: {sprint_goal}
- Sprint Backlog: {completed_items}/{total_items} items completed.
- Token Usage: {token_usage:,} / {token_limit:,} tokens used.
- USD Budget Limit: ${usd_limit:.2f}
- Repository: {state.repo.get('url', 'Not configured')} ({state.repo.get('branch', 'N/A')})
- Interaction Level: {get_interaction_level()} (see docs/INTERACTION-LEVELS.md - controls which
  record_human_approval type, if any, is required before implementing stories / releasing)
"""
    # Inject as a system message at the beginning of the contents
    llm_request.contents.insert(0, types.Content(role="system", parts=[types.Part(text=status_summary)]))
    logger.info("Injected sprint and budget status context for Orchestrator.")

# --- History Management Callbacks ---

def _get_transcript_max_entries() -> int:
    """Reads the configured max transcript entries, defaulting to 200."""
    try:
        return int(os.environ.get("TRANSCRIPT_MAX_ENTRIES", "200"))
    except (ValueError, TypeError):
        return 200

def _trim_transcript(transcript: List[Dict[str, Any]], max_entries: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Bounds transcript growth so a long-running sprint can't blow the token
    budget just by holding/replaying an ever-growing transcript in state.
    Keeps the most recent `max_entries` entries, replacing the dropped
    prefix with a single marker entry noting how many were omitted
    (summarized, not silently dropped). A transcript exactly at the
    threshold is left untouched.
    """
    if max_entries is None:
        max_entries = _get_transcript_max_entries()
    if max_entries <= 0 or len(transcript) <= max_entries:
        return transcript

    omitted_count = len(transcript) - max_entries
    marker = {
        "agent_name": "system",
        "role": "system",
        "content": f"[{omitted_count} earlier transcript entries omitted for token budget]",
    }
    return [marker] + transcript[-max_entries:]

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
    AfterModelCallback: Appends every agent's turn to the shared multi-agent
    transcript, and additionally saves the ScrumOrchestrator's own turns to
    the flat resumable conversation history used for CLI/web session resume.
    """
    if not llm_response.content:
        return None

    text = "".join(p.text for p in llm_response.content.parts if p.text)
    if not text:
        return None

    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name

    # Shared multi-agent transcript: every agent's turns are appended here,
    # tagged by agent_name, so the full sprint conversation is auditable.
    # Appending (rather than syncing/replacing) means each step of a
    # multi-step tool-calling turn gets its own entry.
    transcript = list(state.transcript)
    transcript.append({"agent_name": agent_name, "role": "model", "content": text})
    transcript = _trim_transcript(transcript)
    try:
        callback_context.state["transcript"] = transcript
    except (TypeError, KeyError):
        try:
            setattr(callback_context.state, "transcript", transcript)
        except Exception:
            pass

    # Orchestrator-specific: keep the flat resumable `messages` history used
    # to reconstruct the CLI/web session on resume. Unchanged from before.
    if agent_name == "ScrumOrchestrator":
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

# --- Tool Dispatch Error Handling ---

def on_tool_error_callback(tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, error: Exception) -> Optional[Dict[str, Any]]:
    """
    OnToolErrorCallback: without this, a model calling a tool name that isn't
    in its *own* role's tools=[...] list (e.g. ProductOwner hallucinating
    write_file, which only DevTeam/QualityGuardian actually have) crashes the
    entire ADK run with a bare ValueError - a single hallucinated tool name
    from one sub-agent otherwise aborts the whole multi-sprint session. ADK
    itself already distinguishes this exact case: when tool dispatch fails
    because the name isn't found at all, it synthesizes a placeholder
    `BaseTool(description="Tool not found")` before invoking this callback
    (see google.adk.flows.llm_flows.functions._execute_single_function_call_async) -
    that placeholder, not string-matching the exception message, is the
    signal used below. Returning a dict here turns that crash into an
    ordinary tool-response event the calling agent sees and can recover
    from (e.g. by using one of its real tools, or transfer_to_agent), instead
    of aborting the session.

    A real exception raised *inside* an actual tool call (tool.description is
    the tool's genuine description, not this placeholder) is a bug in that
    tool, not a permissions problem - returning None here lets it propagate
    and fail loudly, same as before this callback existed.
    """
    if tool.description != "Tool not found":
        return None
    agent_name = getattr(tool_context, "agent_name", None) or "this agent"
    return {
        "status": "error",
        "message": (
            f"Tool '{tool.name}' is not available to {agent_name} - it is not in this role's "
            "tool list, so this call cannot be made. Check the tools listed in your own system "
            "instruction and use one of those instead; if this genuinely requires a capability "
            "only another role has, use transfer_to_agent to hand off to that role rather than "
            "trying to call its tools directly."
        ),
    }

# --- Common Agent Configuration ---
COMMON_AGENT_CALLBACKS = {
    "before_model_callback": [inject_litellm_key_callback, check_cost_budget_callback],
    "after_model_callback": [update_token_usage_callback, history_management_after_callback],
    "on_tool_error_callback": on_tool_error_callback,
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
        upsert_issue,
        update_roadmap,
        plan_backlog_item,
        advance_story_stage,
        set_priority,
        log_decision,
        create_from_template,
        gh_release_create,
        create_sprint_report,
        create_release_pr,
        record_human_approval,
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
        upsert_issue,
        record_human_approval,
        log_decision,
        update_budgets,
        get_budget_status,
        log_token_usage,
        reset_sprint_budget,
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
        log_story_tokens,
        advance_story_stage,
        add_impediment,
        log_decision,
        write_file,
        read_doc,
        list_docs,
        create_from_template,
        start_feature_branch,
        mark_pr_ready_for_review,
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
        check_build,
        advance_story_stage,
        merge_story_pr,
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
        advance_story_stage,
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
        upsert_issue,
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
        sprint_status_injection_callback,
        history_management_callback
    ],
    after_model_callback=[
        update_token_usage_callback,
        history_management_after_callback
    ],
    on_tool_error_callback=on_tool_error_callback,
)