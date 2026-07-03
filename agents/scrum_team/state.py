# agents/scrum_team/state.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class Budgets(BaseModel):
    total: float = 0.0
    agents: Dict[str, float] = Field(default_factory=dict)

class TokenUsage(BaseModel):
    total: int = 0
    agents: Dict[str, int] = Field(default_factory=dict)

class ScrumState(BaseModel):
    budgets: Budgets = Field(default_factory=Budgets)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    litellm_keys: Dict[str, str] = Field(default_factory=dict)
    backlog: Dict[str, Dict] = Field(default_factory=dict)
    epics: Dict[str, Dict] = Field(default_factory=dict)
    retrospective_actions: List[str] = Field(default_factory=list)
    sprint_backlog: Dict[str, Dict] = Field(default_factory=dict)
    sprint_report_kpis: Dict = Field(default_factory=dict)
    # Add other state variables here as needed