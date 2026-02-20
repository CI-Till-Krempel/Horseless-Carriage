# agents/scrum_team/tools.py
from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_DOD = [
    "Code reviewed",
    "Automated tests passing",
    "Acceptance criteria met",
    "No critical security issues",
    "Docs updated if needed",
]

def init_scrum_state(tool_context=None) -> Dict[str, Any]:
    """
    Initialize all Scrum artifacts in session.state if missing.
    """
    s = tool_context.state

    s.setdefault("product_vision", "")
    s.setdefault("product_goals", [])
    s.setdefault("product_backlog", [])          # list[dict]
    s.setdefault("definition_of_done", list(DEFAULT_DOD))
    s.setdefault("sprint_goal", "")
    s.setdefault("sprint_backlog", [])           # list[dict]
    s.setdefault("impediment_log", [])           # list[dict]
    s.setdefault("retro_actions", [])            # list[dict]
    s.setdefault("decision_log", [])             # list[dict]

    return {"status": "ok", "initialized": True}

def log_decision(title: str, decision: str, rationale: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Append a decision to decision_log.
    """
    s = tool_context.state
    entry = {
        "title": title.strip(),
        "decision": decision.strip(),
        "rationale": rationale.strip(),
        "owner": owner.strip(),
    }
    s["decision_log"] = list(s.get("decision_log", [])) + [entry]
    return {"status": "ok", "decision": entry}

def upsert_backlog_item(item: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add or update a product backlog item by id (preferred) or by title.
    """
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))

    item_id = item.get("id")
    title = item.get("title")
    if not item_id and not title:
        return {"status": "error", "message": "Backlog item needs at least 'id' or 'title'."}

    def matches(x: Dict[str, Any]) -> bool:
        return (item_id and x.get("id") == item_id) or (title and x.get("title") == title)

    for i, x in enumerate(backlog):
        if matches(x):
            backlog[i] = {**x, **item}
            s["product_backlog"] = backlog
            return {"status": "ok", "updated": True, "item": backlog[i]}

    backlog.append(item)
    s["product_backlog"] = backlog
    return {"status": "ok", "updated": False, "item": item}

def set_priority(title_or_id: str, priority: str, tool_context=None) -> Dict[str, Any]:
    """
    Update priority for a backlog item.
    """
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))

    for x in backlog:
        if x.get("id") == title_or_id or x.get("title") == title_or_id:
            x["priority"] = priority
            s["product_backlog"] = backlog
            return {"status": "ok", "item": x}

    return {"status": "error", "message": "Item not found."}

def add_impediment(description: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an impediment to impediment_log.
    """
    s = tool_context.state
    imp = {"description": description.strip(), "owner": owner.strip(), "status": "open"}
    s["impediment_log"] = list(s.get("impediment_log", [])) + [imp]
    return {"status": "ok", "impediment": imp}

def add_retro_action(action: str, owner: str, success_metric: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an action item from retrospectives.
    """
    s = tool_context.state
    entry = {
        "action": action.strip(),
        "owner": owner.strip(),
        "success_metric": success_metric.strip(),
        "status": "open",
    }
    s["retro_actions"] = list(s.get("retro_actions", [])) + [entry]
    return {"status": "ok", "retro_action": entry}

def plan_sprint_backlog_item(title_or_id: str, plan: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add/update an item in sprint_backlog with implementation plan fields:
      approach: str
      tasks: list[str]
      estimate: str | number
      risks: list[str]
      test_approach: str
      dod_checks: list[str]
    """
    s = tool_context.state
    sprint: List[Dict[str, Any]] = list(s.get("sprint_backlog", []))

    key = title_or_id
    for i, x in enumerate(sprint):
        if x.get("id") == key or x.get("title") == key:
            sprint[i] = {**x, **plan}
            s["sprint_backlog"] = sprint
            return {"status": "ok", "updated": True, "item": sprint[i]}

    entry = {"title": key, **plan}
    sprint.append(entry)
    s["sprint_backlog"] = sprint
    return {"status": "ok", "updated": False, "item": entry}