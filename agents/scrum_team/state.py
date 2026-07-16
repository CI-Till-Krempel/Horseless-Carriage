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