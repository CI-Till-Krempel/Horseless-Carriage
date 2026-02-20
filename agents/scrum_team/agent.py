# agents/scrum_team/agent.py
import os

import litellm
from google.adk.agents.llm_agent import LlmAgent
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
)

# --- LiteLLM Proxy wiring ---
# If LITELLM_PROXY_API_BASE is set, we assume proxy mode.
if os.getenv("LITELLM_PROXY_API_BASE"):
    litellm.use_litellm_proxy = True
    # LiteLLM reads base/key from env:
    # LITELLM_PROXY_API_BASE, LITELLM_PROXY_API_KEY

def M(alias: str) -> LiteLlm:
    """
    Convenience helper to create a LiteLlm model reference.
    Use aliases from litellm.yaml when proxy mode is enabled.
    """
    return LiteLlm(model=alias)

# --- Sub agents (role specialists) ---
product_owner = LlmAgent(
    name="ProductOwner",
    model=M("scrum-po"),
    description="Owns product vision/goals, backlog ordering, acceptance criteria, scope tradeoffs.",
    instruction=PO_PROMPT,
    tools=[init_scrum_state, upsert_backlog_item, set_priority, log_decision],
)

scrum_master = LlmAgent(
    name="ScrumMaster",
    model=M("scrum-sm"),
    description="Facilitates Scrum events, removes impediments, improves process, tracks actions.",
    instruction=SM_PROMPT,
    tools=[init_scrum_state, add_impediment, add_retro_action, log_decision],
)

dev_team = LlmAgent(
    name="DevTeam",
    model=M("scrum-dev"),
    description="Plans/estimates/implements stories, owns technical decisions, ensures DoD, creates sprint plan.",
    instruction=DEV_PROMPT,
    tools=[init_scrum_state, plan_sprint_backlog_item, add_impediment, log_decision],
)

qa_agent = LlmAgent(
    name="QA",
    model=M("scrum-qa"),
    description="Improves test strategy and quality signals; proposes test cases and automation.",
    instruction=QA_PROMPT,
    tools=[init_scrum_state, add_impediment, log_decision],
)

architect = LlmAgent(
    name="Architect",
    model=M("scrum-arch"),
    description="Identifies architectural risks, proposes tradeoffs, writes ADR-like notes.",
    instruction=ARCH_PROMPT,
    tools=[init_scrum_state, log_decision],
)

# --- Root orchestrator (delegates to sub_agents) ---
root_agent = LlmAgent(
    name="ScrumOrchestrator",
    model=M("scrum-orchestrator"),
    description="Routes requests within Scrum team and maintains shared artifacts in session.state.",
    instruction=ORCHESTRATOR_PROMPT,
    tools=[init_scrum_state, log_decision],
    sub_agents=[product_owner, scrum_master, dev_team, qa_agent, architect],
)