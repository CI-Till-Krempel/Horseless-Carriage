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
    product_backlog: List[Dict] = Field(default_factory=list)
    definition_of_done: List[str] = Field(default_factory=list)
    sprint_goal: str = ""
    sprint_number: int = 0
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
    last_check_build: Optional[Dict[str, Any]] = None
    pr_review_calls: Dict[str, int] = Field(default_factory=dict)
    architect_review_baseline: int = 0
    qa_review_baseline: int = 0
    sprint_report_pending_release: bool = False
    blocking_interactions: List[Dict[str, Any]] = Field(default_factory=list)
    orchestrator_stall_count: int = 0