# agents/scrum_team/agent.py
import os
import json
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
    # GH issue #128: this used to be hardcoded to DEBUG regardless of
    # LOG_LEVEL, so the per-session log file always captured verbose DEBUG
    # traces from httpx/openai/litellm/google.adk (which can include full
    # request/response bodies and headers) even when a user explicitly
    # chose a quieter, safer LOG_LEVEL - contradicting SECURITY.md's claim
    # that this only happens under LOG_LEVEL=DEBUG. The file handler now
    # respects the same level as everything else; DEBUG is still available,
    # just only when actually requested.
    fh = logging.FileHandler(log_file)
    fh.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    root_logger.addHandler(fh)
    
    # LiteLLM specific logging
    if log_level_str == "DEBUG":
        litellm.set_verbose = True
    
    root_logger.info(f"Logging initialized. Console level: {log_level_str}, File: {log_file}")

# GH issue #127: a raw, per-run record of the actual conversation and tool
# calls - separate from the general application/debug log above, and
# distinct from state.json (which no longer stores the transcript at all,
# see REPO_STATE_KEYS in tools/scrum.py). propagate=False keeps this off
# the console entirely: every ADK frontend already renders the live
# conversation itself, so re-printing full turns via the root logger would
# just duplicate that output - this logger exists purely to make it durable
# on disk.
def _setup_transcript_logger() -> logging.Logger:
    transcript_file = os.path.join("/app/sessions", f"transcript-{os.getenv('SESSION_ID', 'default')}.log")
    os.makedirs(os.path.dirname(transcript_file), exist_ok=True)
    tlogger = logging.getLogger("hc.transcript")
    tlogger.setLevel(logging.INFO)
    tlogger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(transcript_file) for h in tlogger.handlers):
        fh = logging.FileHandler(transcript_file)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        tlogger.addHandler(fh)
    return tlogger

_setup_logging()
logger = logging.getLogger("scrum-team")
transcript_logger = _setup_transcript_logger()

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

    # 5. Patch LiteLLMClient.acompletion to handle Gemini safety blocks and
    # transient connection failures gracefully, instead of letting either
    # crash the whole run.
    import google.adk.models.lite_llm as adk_litellm
    _orig_adk_acompletion = adk_litellm.LiteLLMClient.acompletion

    def _degraded_llm_response(response_id: str, finish_reason: str, message: str, model_name: str, stream: bool):
        """Builds a synthetic single-turn ModelResponse standing in for a
        failed LLM call, so the caller gets a normal-shaped response
        instead of a propagating exception."""
        from litellm.utils import ModelResponse, Choices, Message

        response = ModelResponse(
            id=response_id,
            choices=[Choices(finish_reason=finish_reason, index=0, message=Message(content=message))],
            created=0,
            model=model_name,
            object="chat.completion",
        )
        if stream:
            async def _async_gen():
                yield response
            return _async_gen()
        return response

    async def _patched_adk_acompletion(self, *args, **kwargs):
        try:
            return await _orig_adk_acompletion(self, *args, **kwargs)
        except Exception as e:
            model_name = kwargs.get("model") or "unknown-gemini"
            # Check for the specific 'no choices' error which usually indicates a safety block
            if "no 'choices'" in str(e):
                logger.warning(f"Detected Gemini safety block: {e}")
                blocked_msg = "⚠️ [SAFETY BLOCK] The request was blocked by Gemini's safety filters. Please try rephrasing your request or avoiding sensitive topics."
                return _degraded_llm_response("safety-block", "safety", blocked_msg, model_name, kwargs.get("stream"))
            # GH issue #126: a transient LiteLLM proxy connection failure
            # (proxy not up yet, network blip, etc.) previously propagated
            # as a raw, unhandled exception all the way up through the
            # vendored google-adk CLI event loop, crashing the entire
            # interactive `adk run` process (exec'd as the container's PID
            # 1 by entrypoint.sh) on the very first message - the whole
            # session was lost, not just that one turn. Degrade the same
            # way the safety-block case above does, so the CLI's REPL loop
            # survives and the human can just retry the message.
            if isinstance(e, (
                litellm.exceptions.APIConnectionError,
                litellm.exceptions.ServiceUnavailableError,
                litellm.exceptions.Timeout,
            )) or (isinstance(e, litellm.exceptions.InternalServerError) and "onnection error" in str(e)):
                logger.warning(f"Detected LiteLLM connection failure: {e}")
                error_msg = (
                    "⚠️ [CONNECTION ERROR] Could not reach the LiteLLM proxy for this request "
                    f"({e}). Please check that the proxy is running and try again."
                )
                return _degraded_llm_response("connection-error", "stop", error_msg, model_name, kwargs.get("stream"))
            # A real eval run crashed the whole process here: a per-agent
            # LiteLLM virtual key's own max_budget (set ad hoc by
            # create_litellm_virtual_key, unrelated to the shared
            # scrum-sprint-budget check_cost_budget_callback already
            # enforces) was exceeded, and litellm.RateLimitError propagated
            # all the way up through ADK uncaught, killing `adk run`/
            # run_eval.py with no degraded response for the caller to react
            # to. Degrade the same way as a connection failure - the calling
            # agent gets a normal in-band message it can act on (e.g.
            # transfer to another role) instead of the whole session dying.
            if isinstance(e, litellm.exceptions.RateLimitError):
                logger.warning(f"Detected LiteLLM budget/rate-limit rejection: {e}")
                error_msg = (
                    f"⚠️ [BUDGET LIMIT] This agent's LiteLLM virtual key rejected the request "
                    f"({e}). Its own budget cap has likely been exceeded - a human should raise "
                    "or reset it (see README.md \"Budget Management\"); this agent cannot make "
                    "further LLM calls on this key until then."
                )
                return _degraded_llm_response("rate-limit-error", "stop", error_msg, model_name, kwargs.get("stream"))
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

from . import tui
from .helpers import get_process_overhead_percentage, is_story_done, get_interaction_level, STORY_STAGES, get_env_with_deprecated_fallback
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
    record_design_approval,
    add_impediment,
    add_retro_action,
    record_human_approval,
    record_blocking_interaction,
    resolve_blocking_interaction,
    list_blocking_interactions,
    plan_sprint_backlog_item,
    start_sprint,
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
    get_corrupted_state_raw_content,
    save_repaired_state,
    reset_state_from_git,
    clear_corrupted_state,
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
# Not re-exported from .tools (agent-facing tools only) - this internal
# variant is only for _sync_and_commit_roadmap_on_exhaustion below, which
# genuinely needs to push straight to a protected branch on the sprint
# budget running out. See git_push's own docstring for why allow_protected
# is deliberately not a parameter any agent-facing tool call can set.
from .tools.github import _git_push_impl
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
from .tools.base import _configured_repo_root, _run, _redact_secrets
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
    BeforeModelCallback: Injects agent-specific LiteLLM key.

    Sets the key as a per-agent additional arg on that agent's own LiteLlm
    model instance (LiteLlm._additional_args, merged into every completion
    call that specific instance makes - see google.adk.models.lite_llm),
    rather than mutating the process-wide litellm.api_key global (GH issue
    #116). The global mutation raced across concurrent sessions/roles in
    `adk web` mode (a single server process can run multiple sessions/
    sub-agent turns concurrently): between one coroutine's callback setting
    the global and that same coroutine's actual HTTP dispatch, a different
    concurrent coroutine (a different role) could overwrite the same
    global, billing one agent's request against a different role's
    budget-capped virtual key. Each role has its own LiteLlm instance, so
    scoping the key there instead means two different roles' calls can
    never clobber each other's key regardless of concurrency (a residual,
    narrower race remains between concurrent sessions sharing the SAME
    role's single agent-definition object - out of scope for this fix,
    which targets the cross-role misattribution actually reported).

    Falls back to the old global-mutation behavior if the current agent
    doesn't expose a LiteLlm-shaped model (e.g. a future ADK internals
    change) - better than silently injecting no key at all.

    Only trusts state.litellm_keys[agent_name] as an actual Bearer token if
    it looks like a real LiteLLM virtual key (starts with "sk-" - LiteLLM's
    own proxy auth enforces this format, per its "expected to start with
    'sk-'" error). The ADK evalset (eval/adk/scrum_team.evalset.json)
    pre-seeds this exact dict with non-"sk-" placeholder strings (e.g.
    "eval-fixture-key-devteam") purely to satisfy check_cost_budget_
    callback's "has a key at all" presence check for whichever role a given
    case is about - a real run exposed the gap once the harness actually
    reached a live model: this callback shipped that placeholder straight
    through as the real Bearer token, failing every one of that role's
    calls with a confusing 401 after 7 retries, while the root
    ScrumOrchestrator (never present in these fixtures) kept working via
    the LITELLM_PROXY_API_KEY fallback below. Falling back the same way for
    anything that doesn't look like a real key fixes this without needing
    to change the fixtures at all - and is a reasonable defensive floor
    regardless (state.litellm_keys should never be blindly trusted as a
    literal Bearer token without at least this basic shape check).
    """
    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name
    agent_key = state.litellm_keys.get(agent_name)
    key_to_use = agent_key if agent_key and agent_key.startswith("sk-") else os.getenv("LITELLM_PROXY_API_KEY")

    try:
        model = callback_context._invocation_context.agent.canonical_model
        additional_args = getattr(model, "_additional_args", None)
        if additional_args is not None:
            additional_args["api_key"] = key_to_use
            return
    except Exception:
        pass
    litellm.api_key = key_to_use

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
            _git_push_impl(
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


def _notify_critical_halt(callback_context: CallbackContext, msg: str) -> None:
    """Records + notifies a blocking interaction (GH issue #53) for a
    budget-halt event below - these are exactly the "critical tool error"
    case an unsupervised run needs pushed to a human, not just left as a
    chat message in a session nobody may be watching. Best-effort: a
    notification failure must never turn an already-critical halt into an
    unhandled exception on top.

    Guarded to fire once per sprint (GH issue #112), the same "once" pattern
    as the sibling _sync_roadmap_on_exhaustion_once right above - without
    this, every subsequent turn after a budget halt re-invoked this (the
    canned halt response repeats on every turn once the budget's exhausted),
    appending a new blocking_interactions entry and re-firing every
    configured notifier again and again, undoing the alert-fatigue fix
    ISSUE-0025 was meant to deliver. Cleared by reset_sprint_budget, same as
    budget_exhaustion_synced, so a halt in a later sprint notifies again."""
    if callback_context.state.get("critical_halt_notified"):
        return
    from .tools.notifications import record_blocking_interaction
    try:
        record_blocking_interaction("critical_error", msg, tool_context=callback_context)
    except Exception:
        pass
    callback_context.state["critical_halt_notified"] = True


def ensure_state_initialized_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: mechanically calls init_scrum_state() once per
    session, before any other before_model_callback (registered first in
    root_agent's before_model_callback list) - instead of relying on the
    Orchestrator to proactively call it itself as its own first tool call
    (GH issue #72: "the orchestrator cannot access the config" - repo URL,
    budgets, interaction level all showed as unset/zero for an entire
    session because init_scrum_state() was never actually invoked).

    This also fixes a knock-on effect of that gap: check_cost_budget_callback
    (which runs right after this one) halts the *entire* session outright -
    before the model, and therefore the ISSUE-0027 proactive greeting, ever
    runs - if state.budgets.total_usd is <= 0 and the SPRINT_USD_BUDGET env
    var also resolves to <= 0. init_scrum_state()'s own "HARD GUARDRAIL"
    (agents/scrum_team/tools/scrum.py) already replaces a 0/negative budget
    with a sane default (1M tokens / $10) - but only once it actually runs.
    Auto-running it here, before that budget check, means a misconfigured
    or literal-zero SPRINT_USD_BUDGET can no longer silently kill the
    session before the user ever sees a reply.

    Deliberately does not pass secrets anywhere new - init_scrum_state()
    already only reads from os.environ / the state repo, the same as if
    the model had called it itself; this just guarantees it actually runs.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return
    if callback_context.state.get("_state_auto_initialized"):
        return
    try:
        init_scrum_state(tool_context=callback_context)
    except Exception as e:
        logger.warning(f"ensure_state_initialized_callback: init_scrum_state() failed (non-fatal): {e}")
    callback_context.state["_state_auto_initialized"] = True


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
        _notify_critical_halt(callback_context, msg)
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

    # 2. Check USD Budget (Remote Guardrail via LiteLLM Proxy)
    if os.environ.get("LLM_LOCAL_PROVIDER") == "true":
        # GH issue #75: self-hosted Ollama models have no real per-token
        # price - LiteLLM's cost map has no entry for arbitrary local model
        # tags, so `spend` on scrum-sprint-budget stays ~$0 regardless of
        # actual usage, making this check pass trivially forever. Skip it
        # (and the network round-trip to the proxy) rather than let it stand
        # in as a guardrail it can't actually provide - the token budget in
        # step 1 above is the one that meaningfully caps a local sprint. See
        # docs/BUDGET.md.
        if not callback_context.state.get("_local_provider_usd_notice_shown"):
            logger.info(
                "check_cost_budget_callback: LLM_LOCAL_PROVIDER=true - skipping the remote USD "
                "budget check (self-hosted models have no real per-token price to track). "
                "Only the token budget applies for this sprint."
            )
            callback_context.state["_local_provider_usd_notice_shown"] = True
        return None

    budget_limit = state.budgets.total_usd
    # Fallback to environment if state is missing/zero. TOTAL_USD_BUDGET is
    # the canonical name (GH issue #81) - SPRINT_USD_BUDGET is still honored
    # via get_env_with_deprecated_fallback so an existing .env isn't silently
    # ignored in favor of the 10.0 hardcoded default below.
    if budget_limit <= 0:
        try:
            budget_limit = float(get_env_with_deprecated_fallback("TOTAL_USD_BUDGET", "SPRINT_USD_BUDGET") or 10.0)
        except (ValueError, TypeError):
            budget_limit = 10.0

    if budget_limit <= 0:
        # If still 0, something is wrong with configuration
        msg = "❌ [CONFIGURATION ERROR] No USD budget limit set for the sprint. Agent execution halted for safety."
        _notify_critical_halt(callback_context, msg)
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

        # Persisted so create_sprint_report can show it (GH issue #111) -
        # previously this was a local variable only, so the sprint report
        # could only ever print the configured ceiling, never how much was
        # actually spent, despite docs/BUDGET.md documenting the report as
        # showing both.
        budgets_state = callback_context.state.get("budgets", {}) or {}
        budgets_state["current_usd_spend"] = current_spend
        callback_context.state["budgets"] = budgets_state

        if current_spend >= budget_limit:
            _sync_roadmap_on_exhaustion_once(callback_context)
            msg = (
                f"🚫 [USD BUDGET EXCEEDED] Total USD budget (${budget_limit:.2f}) reached. "
                f"Current spend: ${current_spend:.2f}. Agent execution halted."
            )
            _notify_critical_halt(callback_context, msg)
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=msg)]),
                model_version=llm_request.model or "unknown"
            )
    except requests.RequestException as e:
        _sync_roadmap_on_exhaustion_once(callback_context)
        msg = f"❌ [BUDGET ERROR] Could not verify budget status with LiteLLM proxy: {e}. Agent execution halted to prevent unmonitored spending."
        _notify_critical_halt(callback_context, msg)
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=msg)]),
            model_version=llm_request.model or "unknown"
        )

    return None

_FAKE_TOOL_CALL_NAME_KEYS = ("function", "name")
_FAKE_TOOL_CALL_ARGS_KEYS = ("arguments", "args", "properties")
_JSON_ENVELOPE_MESSAGE_KEYS = ("message", "content", "text")


def recover_fake_tool_call_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> None:
    """
    AfterModelCallback: some models occasionally reply with plain TEXT that
    merely *looks* like a tool call - a JSON object shaped like
    `{"type": "function", "function": "<tool_name>", "arguments": {...}}` -
    instead of an actual ADK function_call part, even when the prompt
    explicitly says not to (see prompts.py's DELEGATION IS MANDATORY, NOT
    DESCRIPTIVE, which already names this exact pattern as "an improvised
    JSON blob"). GH issue #89: a real session hit this 8 times in a row
    with gemini-1.5-pro via the LiteLLM proxy - the existing stall-warning
    banner (_track_orchestrator_stall) didn't reliably get the model to
    self-correct, so this is a mechanical backstop rather than relying on
    the model's behavior changing.

    GH issue #95 surfaced a second, looser variant of the same habit from a
    local Ollama model: `{"function": "read_doc", "properties": {"path":
    ...}}` - no `"type"` key at all, and `"properties"` instead of
    `"arguments"`/`"args"`. The original exact-shape match missed this, so
    the call silently never happened. Treated as the same fake-tool-call
    pattern whenever `"type"` is absent (or is itself "function"), as long
    as an explicit args-shaped key is present alongside the name - that
    second condition keeps a merely-JSON-shaped prose reply (e.g. `{"status":
    "ok", "note": "..."}`, which has neither) from ever being misread as an
    attempted call.

    Also recovers a third pattern the same local model produced (also GH
    issue #91): a genuine conversational reply wrapped in a JSON envelope -
    `{"response_type": "info", "message": "..."}` - instead of plain text.
    That isn't a tool-call attempt at all, so it's unwrapped to its
    human-readable `message` rather than converted into a function_call.

    Mutates llm_response.content.parts *in place* (matching
    _track_orchestrator_stall's established pattern below, rather than
    returning a new LlmResponse) - ADK's own flow re-checks this exact
    object for function_call parts after every after_model_callback runs
    (see base_llm_flow.py's _handle_after_model_callback ->
    _postprocess_async) and dispatches them exactly as if the model had
    used real function-calling, including on_tool_error_callback's
    existing "tool not found" recovery if the name turns out to be
    hallucinated - so this converts the model's actual intent into a real,
    normally-dispatched tool call rather than reimplementing dispatch here.

    Requires an exact, whole-string JSON match against these precise shapes
    (not a substring search) so a legitimate prose reply that merely
    mentions a tool by name is never mistaken for this pattern - and only
    fires when there is no real function_call part already (nothing to
    recover) and exactly one text part (an unambiguous whole reply, not one
    part of a longer multi-part message).
    """
    if not llm_response.content or not llm_response.content.parts:
        return
    parts = llm_response.content.parts
    if any(getattr(p, "function_call", None) for p in parts):
        return
    text_parts = [p for p in parts if getattr(p, "text", None)]
    if len(text_parts) != 1:
        return

    text = (text_parts[0].text or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return
    try:
        parsed = json.loads(text)
    except Exception:
        return
    if not isinstance(parsed, dict):
        return

    tool_name = next((parsed[k] for k in _FAKE_TOOL_CALL_NAME_KEYS if isinstance(parsed.get(k), str) and parsed[k].strip()), None)
    has_args_key = any(k in parsed for k in _FAKE_TOOL_CALL_ARGS_KEYS)
    type_is_function = parsed.get("type") == "function"
    if tool_name and (type_is_function or has_args_key):
        tool_args = next((parsed[k] for k in _FAKE_TOOL_CALL_ARGS_KEYS if isinstance(parsed.get(k), dict)), {})
        logger.warning(
            f"recover_fake_tool_call_callback: {callback_context.agent_name} replied with text shaped like "
            f"a tool call ({tool_name!r}) instead of a real one (GH issue #89/#95) - converting it into an "
            "actual function call."
        )
        llm_response.content.parts = [types.Part(function_call=types.FunctionCall(name=tool_name, args=tool_args))]
        return

    if not tool_name and isinstance(parsed.get("response_type"), str):
        message = next((parsed[k] for k in _JSON_ENVELOPE_MESSAGE_KEYS if isinstance(parsed.get(k), str) and parsed[k].strip()), None)
        if message:
            logger.warning(
                f"recover_fake_tool_call_callback: {callback_context.agent_name} wrapped a plain reply in a "
                f"JSON envelope ({parsed.get('response_type')!r}) instead of replying in plain text (GH issue "
                "#91) - unwrapping it to the human-readable message."
            )
            text_parts[0].text = message


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

def _stories_ready_for_next_stage_count(state: ScrumState) -> int:
    """How many backlog items (sprint_backlog + product_backlog) have
    completed some STORY_STAGES stage but not the very next one - i.e.
    genuinely ready for another role to pick up (Ready-but-not-Implemented,
    Reviewed-but-not-Tested, etc.), not just sitting untouched. Feeds the
    Orchestrator's first-message menu (GH issue #58) - a nonzero count here
    is a concrete signal for offering "Discuss the sprint backlog" or
    "Refine User Stories", not something to compute from prose."""
    count = 0
    for item in (state.sprint_backlog or []) + (state.product_backlog or []):
        completed = set(item.get("stages_completed") or [])
        for i, stage in enumerate(STORY_STAGES[:-1]):
            if stage in completed and STORY_STAGES[i + 1] not in completed:
                count += 1
                break
    return count


def sprint_status_injection_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    BeforeModelCallback: Injects current sprint/budget/process status for
    the Orchestrator's first message - the concrete state signals
    ORCHESTRATOR_PROMPT's FIRST MESSAGE SUMMARY (GH issue #58) uses to pick
    which 2-5 next-action options are actually relevant right now, instead
    of a generic, state-blind greeting.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return

    # Only on the very first message of a run (no previous interaction) -
    # `not llm_request.previous_interaction_id` (as before) is wrong: it's
    # falsy on the first internal model call of *every* turn, not just a
    # session's true first turn ever (see GH issue #118 - the sibling
    # history_management_callback right below was rewritten to fix this
    # exact same broken check, via len(llm_request.contents) <= 1, but this
    # callback kept using the old one). Left broken, this risks the
    # Orchestrator re-injecting its sprint-status/menu content mid-
    # conversation - a confusing "did it just reset?" regression.
    if len(llm_request.contents) > 1:
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

    # Process signals (GH issue #58): what's actually waiting for attention
    # right now, not just sprint/budget numbers.
    product_vision = state.product_vision.strip() if state.product_vision else ""
    sprint_report_exists = bool((state.sprint_report or "").strip())
    open_impediments = [i for i in (state.impediment_log or []) if (i.get("status") or "open") == "open"]
    open_retro_actions = [r for r in (state.retro_actions or []) if (r.get("status") or "open") == "open"]
    ready_for_next_stage = _stories_ready_for_next_stage_count(state)

    # GH issue #85: state_json_corrupted is a raw flag on the state dict
    # (set by init_scrum_state, not a ScrumState field), not something
    # get_scrum_state's ScrumState parsing carries - read it directly so a
    # corrupted-and-unrecoverable state.json is surfaced to the human in
    # this same first-message context, instead of the session silently
    # starting blank with no explanation.
    state_json_corrupted = bool(callback_context.state.get("state_json_corrupted"))
    corruption_notice = (
        "\n- ⚠️ STATE.JSON WAS CORRUPTED: could not be loaded, even after searching git history for an "
        "earlier valid checkpoint - this session started with blank/default state instead. Tell the "
        "human this happened, then offer to help: get_corrupted_state_raw_content() to attempt an "
        "LLM-assisted repair (then save_repaired_state()), reset_state_from_git() to search all of git "
        "history for a usable earlier checkpoint, or clear_corrupted_state() to explicitly discard it "
        "and confirm starting fresh.\n"
        if state_json_corrupted else ""
    )

    # Identify active sprint status
    status_summary = f"""
[SYSTEM CONTEXT: CURRENT SPRINT & BUDGET STATUS]{corruption_notice}
- Sprint Goal: {sprint_goal}
- Sprint Backlog: {completed_items}/{total_items} items completed.
- Token Usage: {token_usage:,} / {token_limit:,} tokens used.
- USD Budget Limit: ${usd_limit:.2f}
- Repository: {state.repo.get('url', 'Not configured')} ({state.repo.get('branch', 'N/A')})
- Interaction Level: {get_interaction_level()} (see docs/INTERACTION-LEVELS.md - controls which
  record_human_approval type, if any, is required before implementing stories / releasing)
- Product Vision: {product_vision or "Not yet defined"}
- Sprint Report: {"already created for the current sprint - likely mid sprint-close, or ready for a new sprint" if sprint_report_exists else "not yet created this sprint"}
- Open Impediments: {len(open_impediments)}{f" (most recent: {open_impediments[-1].get('description', '')[:120]})" if open_impediments else ""}
- Retro Actions Logged: {len(open_retro_actions)}{f" (most recent: {open_retro_actions[-1].get('action', '')[:120]})" if open_retro_actions else ""}
- Stories Ready For Next Pipeline Stage: {ready_for_next_stage}
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
    BeforeModelCallback: recovers persisted conversation history into a
    genuinely fresh ADK session, and keeps state.messages synced with
    whatever the model actually saw this turn.
    """
    if callback_context.agent_name != "ScrumOrchestrator":
        return

    state = get_scrum_state(callback_context.state)

    # 1. Recovery injection - ONLY when ADK's own native session/event
    # history is itself empty (len(llm_request.contents) <= 1: nothing but
    # - at most - the bare new user message). google.adk.flows.llm_flows.
    # contents._ContentLlmRequestProcessor already replays the full
    # conversation from invocation_context.session.events into
    # llm_request.contents by default, BEFORE any before_model_callback
    # (this one included) ever runs - so on any normal, continuing
    # session, llm_request.contents already has the real history.
    # Gating this on `not llm_request.previous_interaction_id` (as
    # before) was wrong: that's falsy on the first internal model call of
    # *every* new user turn, not just a session's true first turn ever -
    # so on a normal continuing session, this re-prepended state.messages
    # on top of history ADK had already supplied, the sync step below then
    # persisted that doubled view back into state.messages, and the
    # following turn's already-longer ADK-native history got a FRESH
    # state.messages replay stacked on top yet again - compounding without
    # bound (GH issue #70, "loop is broken, orchestrator not starting
    # sprint": a sprint's worth of duplicated exchanges buried the user's
    # actual latest instruction under repeated old ones and inflated token
    # usage every single turn). The check below only fires for the one
    # case where it's actually needed and never duplicates: a genuinely
    # fresh ADK session (empty session.events) recovering context from a
    # persisted state.messages (e.g. after init_scrum_state() just loaded
    # .hc/state.json into this brand new session).
    if len(llm_request.contents) <= 1 and state.messages:
        history_contents = []
        for msg in state.messages:
            # Ensure we have a role and content
            role = msg.get("role", "user")
            content_text = msg.get("content", "")
            if content_text:
                history_contents.append(types.Content(role=role, parts=[types.Part(text=content_text)]))

        if history_contents:
            llm_request.contents = history_contents + llm_request.contents
            logger.info(f"Recovered {len(history_contents)} messages from persisted conversation history.")

    # 2. Sync state.messages with the current full contents to keep it fresh.
    # This is the path a pasted secret in a *user* message actually goes
    # through (history_management_after_callback only ever sees model
    # turns) - redact here too (GH issue #128).
    new_history = []
    for content in llm_request.contents:
        text = "".join(p.text for p in content.parts if p.text)
        if text:
            new_history.append({"role": content.role, "content": _redact_secrets(text)})
    
    if new_history:
        try:
            callback_context.state["messages"] = new_history
        except (TypeError, KeyError):
            try:
                setattr(callback_context.state, "messages", new_history)
            except Exception:
                pass

ORCHESTRATOR_STALL_THRESHOLD = 3


def _track_orchestrator_stall(callback_context: CallbackContext, llm_response: LlmResponse) -> None:
    """
    Tracks how many consecutive Orchestrator replies in a row have made NO
    tool call at all - i.e. it's just talking, not actually delegating,
    saving state, or otherwise mechanically acting (GH issue #70: "loop is
    broken, orchestrator not starting sprint even though the job is
    clear"). Once that streak hits ORCHESTRATOR_STALL_THRESHOLD, mechanically
    prepends a hard-to-miss banner directly onto the visible response text
    - not just a prompt instruction the model might not follow - and
    records a blocking interaction (GH issue #53/ISSUE-0025) so a human not
    reading every reply closely still gets notified. A real tool call (most
    commonly transfer_to_agent, itself a tool call) resets the streak - the
    Orchestrator delegating work is exactly the "acting, not just talking"
    behavior this exists to confirm is actually happening.
    """
    made_tool_call = any(getattr(p, "function_call", None) for p in (llm_response.content.parts or []))
    if made_tool_call:
        stall_count = 0
    else:
        stall_count = (callback_context.state.get("orchestrator_stall_count") or 0) + 1
    try:
        callback_context.state["orchestrator_stall_count"] = stall_count
    except (TypeError, KeyError):
        try:
            setattr(callback_context.state, "orchestrator_stall_count", stall_count)
        except Exception:
            pass

    if stall_count == ORCHESTRATOR_STALL_THRESHOLD and llm_response.content.parts:
        banner = (
            f"⏸ [NO ACTION TAKEN - {stall_count} replies in a row with no tool call] If you "
            "intend to act (delegate, save state, run a tool), you must actually call it now - "
            "describing what you would do is not enough.\n\n"
        )
        for part in llm_response.content.parts:
            if part.text:
                part.text = banner + part.text
                break
        try:
            record_blocking_interaction(
                "stalled",
                f"Orchestrator has replied {stall_count} times in a row without calling any tool.",
                detail="Check the chat - it may be waiting on something, or just talking instead of acting.",
                tool_context=callback_context,
            )
        except Exception:
            pass


def history_management_after_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """
    AfterModelCallback: Appends every agent's turn to the shared multi-agent
    transcript, and additionally saves the ScrumOrchestrator's own turns to
    the flat resumable conversation history used for CLI/web session resume.
    Also tracks whether the Orchestrator is stalling (see
    _track_orchestrator_stall) before extracting its final text, so a
    mechanical warning banner (if one was just added) is captured too.
    """
    if not llm_response.content:
        return None

    if callback_context.agent_name == "ScrumOrchestrator":
        _track_orchestrator_stall(callback_context, llm_response)

    text = "".join(p.text for p in llm_response.content.parts if p.text)
    if not text:
        return None

    state = get_scrum_state(callback_context.state)
    agent_name = callback_context.agent_name

    # GH issue #128: only the recorded copy is redacted - the model itself
    # already saw/produced the real text before this callback runs, so
    # redacting here can't break anything downstream in the live
    # conversation, only what gets persisted into transcript/messages (and
    # from there, sprint reports, the per-run transcript log, and the
    # target repo's state.json).
    recorded_text = _redact_secrets(text)

    # GH issue #127: durable raw record of this turn, independent of
    # state.transcript's size cap below and of state.json entirely (this
    # logger never touches persisted state) - logs the same redacted copy
    # as everything else below, not the raw text, so this new on-disk log
    # doesn't reopen the exact secret-leak gap issue #128 just closed.
    transcript_logger.info(f"[{agent_name}] {recorded_text}")

    # Shared multi-agent transcript: every agent's turns are appended here,
    # tagged by agent_name, so the full sprint conversation is auditable.
    # Appending (rather than syncing/replacing) means each step of a
    # multi-step tool-calling turn gets its own entry. Kept in in-memory
    # session state for the sprint-report excerpt/markdown-transcript
    # renderer (see write_conversation_transcript in tools/budget.py) - no
    # longer written into the target repo's .hc/state.json (GH issue #127).
    transcript = list(state.transcript)
    transcript.append({"agent_name": agent_name, "role": "model", "content": recorded_text})
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
        if not history or history[-1].get("content") != recorded_text:
            history.append({"role": "model", "content": recorded_text})
            try:
                callback_context.state["messages"] = history
            except (TypeError, KeyError):
                try:
                    setattr(callback_context.state, "messages", history)
                except Exception:
                    pass
    return None

# --- Busy Indicator (CLI mode) ---

def agent_thinking_start_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """BeforeModelCallback (last in the list - see COMMON_AGENT_CALLBACKS/
    root_agent's before_model_callback order): starts a terminal spinner for
    the gap between sending this request and getting a reply, since a real
    model call can take several seconds with no other output in between.
    Placed last so a call that another before_model_callback short-circuits
    (e.g. check_cost_budget_callback blocking on a missing budget key) never
    shows a spinner for a request that isn't actually going to be sent.
    AGENT_MODE=cli only (see tui.Spinner - also no-ops outside a real
    terminal); never raises, never blocks the request either way."""
    if os.getenv("AGENT_MODE", "web") == "cli":
        tui.start_thinking(callback_context.agent_name)
    return None


def agent_thinking_stop_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """AfterModelCallback (first in the list): stops the spinner started by
    agent_thinking_start_callback before any other after_model_callback
    prints anything, so the spinner line is cleanly overwritten rather than
    left interleaved with real output. Always returns None - a passive
    side-effect, never alters the response."""
    if os.getenv("AGENT_MODE", "web") == "cli":
        tui.stop_thinking()
    return None

# --- Tool Call Visibility ---

TRANSFER_LOOP_THRESHOLD = 6


def _detect_transfer_loop(tool_context: ToolContext, from_agent: str, to_agent: str) -> Optional[Dict[str, Any]]:
    """
    Breaks an unproductive transfer_to_agent ping-pong between exactly two
    agents - a real eval run saw ProductOwner and Scrum Master bounce
    transfer_to_agent back and forth ~40 times with no other tool call in
    between (create_sprint_report's mandatory-retro gate, see
    tools/budget.py, kept rejecting PO's attempt to close the sprint, and
    each rejection just sent it back to Scrum Master again). Nothing
    mechanical stopped it - it only stopped when a per-agent LiteLLM budget
    cap ran out and crashed the whole process. This tracks consecutive
    transfer_to_agent hops between the *same* pair of agents; any other
    tool call (real progress) or a transfer involving a third agent resets
    the streak. Mirrors _track_orchestrator_stall's "mechanical banner +
    blocking interaction" approach, but as a before_tool_callback gate since
    that's what actually sees each transfer's target agent.
    """
    state = tool_context.state
    pair = tuple(sorted((from_agent, to_agent)))
    loop_state = state.get("_transfer_loop") or {}
    count = loop_state.get("count", 0) + 1 if loop_state.get("pair") == list(pair) else 1
    state["_transfer_loop"] = {"pair": list(pair), "count": count}

    if count < TRANSFER_LOOP_THRESHOLD:
        return None

    state["_transfer_loop"] = {"pair": None, "count": 0}
    msg = (
        f"🔁 [TRANSFER LOOP DETECTED] {from_agent} and {to_agent} have handed off to each other "
        f"{count} times in a row with no other tool call in between - refusing this transfer. "
        "Stop transferring and actually call a tool that makes progress (e.g. the mandatory step "
        "you're both routing around), or explain the blocker instead of handing off again."
    )
    try:
        from .tools.notifications import record_blocking_interaction
        record_blocking_interaction(
            "stalled",
            f"{from_agent} and {to_agent} bounced transfer_to_agent {count}x with no progress.",
            detail=msg,
            tool_context=tool_context,
        )
    except Exception:
        pass
    return {"status": "error", "message": msg}


def log_tool_invocation_callback(tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext) -> Optional[Dict[str, Any]]:
    """
    BeforeToolCallback: prints a hard-to-miss notice for every tool call, to
    stderr - not just whatever a given ADK frontend chooses to render on its
    own. ADK's own `adk run` CLI REPL
    (google.adk.cli.cli.run_interactively/run_input_file) only echoes events
    that carry `.text` - a pure function_call/function_response event has
    none, so every tool call was completely invisible to anyone watching a
    foreground CLI session or `docker compose logs agent`, even for gated
    actions a human might be expected to notice (e.g. a release PR blocked
    on a missing approval - see create_release_pr in tools/github.py). The
    ADK web UI renders its own tool-call panel regardless, so this is a
    harmless duplicate there and the actual fix for CLI/daemon mode.

    AGENT_MODE=cli gets the boxed, per-role tui.speech_bubble presentation
    (a real interactive terminal, worth the extra lines); every other mode
    keeps the original single-line form, since that's a container log meant
    to be read with `docker compose logs`, not a live terminal.

    Deliberately logs argument *names* only, not values - tool arguments can
    carry large file contents or PR bodies, and printing full values here
    would be noisy at best and a way to leak sensitive content into logs at
    worst. Mostly a passive trace - only gates the specific transfer-loop
    case, see _detect_transfer_loop.

    GH issue #127: also records this same names-only call description into
    the shared transcript (state.transcript) and the durable per-run
    transcript log, so tool calls show up per-subagent in the human-
    readable markdown transcript (write_conversation_transcript in
    tools/budget.py) alongside model turns - previously only the model's
    own text was captured there, so every tool call was invisible in any
    persisted record, not just the live console.
    """
    agent_name = getattr(tool_context, "agent_name", None) or "?"
    arg_names = ", ".join(args.keys()) if args else ""
    call_desc = f"{tool.name}({arg_names})"

    transcript_logger.info(f"[{agent_name}] TOOL CALL: {call_desc}")
    try:
        state = get_scrum_state(tool_context.state)
        transcript = list(state.transcript)
        transcript.append({"agent_name": agent_name, "role": "tool_call", "content": call_desc})
        tool_context.state["transcript"] = _trim_transcript(transcript)
    except Exception:
        pass

    if tool.name == "transfer_to_agent":
        target_agent = (args or {}).get("agent_name")
        if target_agent:
            loop_result = _detect_transfer_loop(tool_context, agent_name, target_agent)
            if loop_result is not None:
                print(f"\U0001f501 [{agent_name}] transfer loop broken (-> {target_agent})", file=sys.stderr)
                return loop_result
    else:
        # Any non-transfer tool call is real progress - reset the ping-pong
        # streak so it only fires on genuinely unproductive bouncing.
        try:
            tool_context.state["_transfer_loop"] = {"pair": None, "count": 0}
        except Exception:
            pass

    if os.getenv("AGENT_MODE", "web") == "cli":
        try:
            print(tui.speech_bubble(agent_name, call_desc), file=sys.stderr)
            return None
        except Exception:
            pass
    print(f"\U0001f527 [{agent_name}] {call_desc}", file=sys.stderr)
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
    "before_model_callback": [inject_litellm_key_callback, check_cost_budget_callback, agent_thinking_start_callback],
    "after_model_callback": [agent_thinking_stop_callback, recover_fake_tool_call_callback, update_token_usage_callback, history_management_after_callback],
    "before_tool_callback": log_tool_invocation_callback,
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
        record_design_approval,
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
        start_sprint,
        add_impediment,
        add_retro_action,
        upsert_issue,
        record_human_approval,
        record_blocking_interaction,
        resolve_blocking_interaction,
        list_blocking_interactions,
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
        list_blocking_interactions,
        save_state_to_repo,
        load_state_from_repo,
        get_corrupted_state_raw_content,
        save_repaired_state,
        reset_state_from_git,
        clear_corrupted_state,
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
        ensure_state_initialized_callback,
        inject_litellm_key_callback,
        check_cost_budget_callback,
        sprint_status_injection_callback,
        history_management_callback,
        agent_thinking_start_callback
    ],
    after_model_callback=[
        agent_thinking_stop_callback,
        recover_fake_tool_call_callback,
        update_token_usage_callback,
        history_management_after_callback
    ],
    before_tool_callback=log_tool_invocation_callback,
    on_tool_error_callback=on_tool_error_callback,
)