# agents/scrum_team/state.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

class Budgets(BaseModel):
    total: int = 0
    total_usd: float = 0.0

class TokenUsage(BaseModel):
    total: int = 0
    agents: Dict[str, int] = Field(default_factory=dict)

class ScrumState(BaseModel):
    version: str = "1.0.0"
    product_vision: str = ""
    product_goals: List[str] = Field(default_factory=list)
    architecture_vision: str = ""
    product_backlog: List[Dict] = Field(default_factory=list)
    definition_of_done: List[str] = Field(default_factory=list)
    sprint_goal: str = ""
    sprint_number: int = 0
    sprint_backlog_pr_sprint: int = 0
    sprint_backlog: List[Dict] = Field(default_factory=list)
    impediment_log: List[Dict] = Field(default_factory=list)
    retro_actions: List[Dict] = Field(default_factory=list)
    decision_log: List[Dict] = Field(default_factory=list)
    sprint_report: str = ""
    budgets: Budgets = Field(default_factory=Budgets)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    litellm_keys: Dict[str, str] = Field(default_factory=dict)
    story_estimates: Dict[str, Any] = Field(default_factory=dict)
    sprint_report_kpis: Dict = Field(default_factory=dict)
    kpi_update_count: int = 0
    kpi_baseline: int = 0
    backlog_scope_complete: bool = False
    repo: Dict[str, str] = Field(default_factory=dict)
    github_app: Dict[str, str] = Field(default_factory=dict)
    github_token: Optional[str] = None
    last_auto_auth_error: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    transcript: List[Dict[str, Any]] = Field(default_factory=list)
    sprint_files_touched: List[str] = Field(default_factory=list)
    hc_version: str = "unknown"
    retro_baseline: int = 0
    human_approvals: List[Dict[str, Any]] = Field(default_factory=list)
    sprint_approval_baseline: int = 0
    release_approval_baseline: int = 0
    dev_touch_baseline: int = 0
    # GH issue #344: mirrors dev_touch_baseline for
    # _detect_unadvanced_implementation (agent.py) - the touch-count
    # snapshot taken the last time DevTeam was nudged for transferring away
    # with un-advanced implementation work, so the same reminder doesn't
    # repeat forever once DevTeam has already seen it. No leading
    # underscore - Pydantic treats underscore-prefixed fields as private
    # attributes, excluded from model_dump(), which would silently break
    # persistence to .hc/state.json.
    unadvanced_write_nudge_baseline: int = 0
    last_check_build: Optional[Dict[str, Any]] = None
    dependency_manifest_write_count: int = 0
    pr_review_calls: Dict[str, int] = Field(default_factory=dict)
    architect_review_baseline: int = 0
    qa_review_baseline: int = 0
    qa_tested_baseline: int = 0
    sprint_report_pending_release: bool = False
    blocking_interactions: List[Dict[str, Any]] = Field(default_factory=list)
    orchestrator_stall_count: int = 0
    overclaim_rejection_counts: Dict[str, int] = Field(default_factory=dict)
    # GH issue #342: retro-item triage - add_retro_action/add_impediment
    # now require a category ("technical"/"steering"/"human"). A "human"
    # category entry becomes a general_blockers record here (not tied to
    # any one story, unlike raise_story_blocker) - surfaced in the eval
    # report's Blockers section regardless of story association, and (if
    # high priority) able to stop an eval run the same way a BLOCKED
    # story's own stop reasons already do (see run_eval.py).
    general_blockers: List[Dict[str, Any]] = Field(default_factory=list)
    # steering_proposal_count only increases via a real, successful
    # propose_steering_change call (tools/workflow.py) - compared against
    # steering_baseline by create_sprint_report's own gate, same
    # "must be NEW since last report" pattern as retro_baseline/kpi_baseline,
    # so a "steering"-category retro finding can't just be logged and
    # forgotten the way GH issue #342 found every retro finding was.
    steering_proposal_count: int = 0
    steering_baseline: int = 0
    # GH issue #356: a structured, durable record of every propose_steering_
    # change call that has actually succeeded - rendered into
    # specs/reports/STEERING-NNN.md alongside the sprint report. Distinct
    # from steering_proposal_count above (a bare counter for the gate) -
    # this holds the actual content (role/rationale/PR link) a human would
    # need to audit the proposal later, since the PR body itself may be
    # lost once the PR merges/closes.
    steering_proposals: List[Dict[str, Any]] = Field(default_factory=list)