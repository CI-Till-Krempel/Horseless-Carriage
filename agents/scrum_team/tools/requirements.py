# agents/scrum_team/tools/requirements.py
from __future__ import annotations
import json
from typing import Any, Dict, List
from .base import _configured_repo_root, _state_file_path, _project_root

def upsert_story(story: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Add or update a User Story by ID or Title.
    """
    story["type"] = "User Story"
    return upsert_backlog_item(story, tool_context=tool_context)

def upsert_epic(epic: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Add or update an Epic by ID or Title.
    """
    epic["type"] = "Epic"
    return upsert_backlog_item(epic, tool_context=tool_context)

def update_roadmap(version: str, goals: List[str] = None, stories: List[str] = None, tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Update the product roadmap (docs/ROADMAP.md) for a specific version.
    """
    from .scrum import save_state_to_repo
    from .docs import seed_repository
    repo_root = _configured_repo_root(tool_context)
    roadmap_path = repo_root / "docs" / "ROADMAP.md"
    
    if not roadmap_path.exists():
        seed_repository(overwrite=False, tool_context=tool_context)
    
    if not roadmap_path.exists():
        return {"status": "error", "message": "ROADMAP.md not found and could not be seeded."}

    content = roadmap_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    
    in_version_section = False
    version_found = False
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("### ") and version in line:
            in_version_section = True
            version_found = True
            new_lines.append(line)
            i += 1
            if goals:
                new_lines.append("Goals")
                for g in goals:
                    new_lines.append(f"- {g}")
                while i < len(lines) and not (lines[i].startswith("Stories") or lines[i].startswith("###") or lines[i].startswith("---")):
                    i += 1
            if stories:
                while i < len(lines) and not (lines[i].startswith("Stories") or lines[i].startswith("###") or lines[i].startswith("---")):
                    new_lines.append(lines[i])
                    i += 1
                if i < len(lines) and lines[i].startswith("Stories"):
                    new_lines.append("Stories")
                    i += 1
                else:
                    new_lines.append("\nStories")
                for s in stories:
                    s_data = next((x for x in tool_context.state.get("product_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                    s_id = s_data.get("id") or s
                    s_title = s_data.get("title") or ""
                    status = s_data.get("status", "To Do")
                    mark = " "
                    if status == "In Progress": mark = "~"
                    elif status == "Done": mark = "x"
                    elif status == "In Review": mark = "R"
                    new_lines.append(f"- [{mark}] [{s_id}] {s_title}")
                while i < len(lines) and not (lines[i].startswith("###") or lines[i].startswith("---") or lines[i].strip() == ""):
                    i += 1
            continue
        if in_version_section and (line.startswith("###") or line.startswith("---")):
            in_version_section = False
        new_lines.append(line)
        i += 1

    if not version_found:
        insertion_idx = len(new_lines)
        for idx, line in enumerate(new_lines):
            if "## Task board" in line:
                insertion_idx = idx
                break
        insertion = [f"### {version}", "Goals"]
        if goals:
            for g in goals: insertion.append(f"- {g}")
        insertion.append("\nStories")
        if stories:
            for s in stories: insertion.append(f"- [ ] [{s}] {s}")
        insertion.append("\n")
        new_lines = new_lines[:insertion_idx] + insertion + new_lines[insertion_idx:]

    roadmap_path.write_text("\n".join(new_lines), encoding="utf-8")
    save_state_to_repo(tool_context)
    return {"status": "ok", "message": f"Roadmap updated for {version}"}

def plan_backlog_item(title_or_id: str, priority: str = None, version: str = None, tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Plan a backlog item.
    """
    res = {"status": "ok", "updates": []}
    if priority:
        p_res = set_priority(title_or_id, priority, tool_context=tool_context)
        res["updates"].append({"type": "priority", "result": p_res})
    if version:
        r_res = update_roadmap(version, stories=[title_or_id], tool_context=tool_context)
        res["updates"].append({"type": "roadmap", "result": r_res})
    return res

def upsert_backlog_item(item: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add or update a product backlog item.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))
    item_id = item.get("id")
    title = item.get("title")
    if not item_id and not title:
        return {"status": "error", "message": "Backlog item needs at least 'id' or 'title'."}

    def matches(x: Dict[str, Any]) -> bool:
        return (item_id and x.get("id") == item_id) or (title and x.get("title") == title)

    updated_item = None
    for i, x in enumerate(backlog):
        if matches(x):
            backlog[i] = {**x, **item}
            s["product_backlog"] = backlog
            updated_item = backlog[i]
            break
    if not updated_item:
        backlog.append(item)
        s["product_backlog"] = backlog
        updated_item = item

    save_state_to_repo(tool_context)
    _update_story_markdown(updated_item, tool_context)
    return {"status": "ok", "updated": (updated_item != item), "item": updated_item}

def set_priority(title_or_id: str, priority: str, tool_context=None) -> Dict[str, Any]:
    """
    Update priority for a backlog item.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))
    for x in backlog:
        if x.get("id") == title_or_id or x.get("title") == title_or_id:
            x["priority"] = priority
            s["product_backlog"] = backlog
            save_state_to_repo(tool_context)
            return {"status": "ok", "item": x}
    return {"status": "error", "message": "Item not found."}

def sync_stories_from_markdown(tool_context=None) -> Dict[str, Any]:
    """
    Scan docs/stories/*.md and update state.
    """
    repo_root = _configured_repo_root(tool_context)
    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.exists():
        return {"status": "ok", "message": "No stories directory found."}
    s = tool_context.state
    backlog = list(s.get("product_backlog", []))
    sprint_backlog = list(s.get("sprint_backlog", []))
    found_stories = []
    for fp in stories_dir.glob("*.md"):
        if fp.name.startswith("TEMPLATE-") or fp.name == "README.md":
            continue
        content = fp.read_text(encoding="utf-8")
        story_data = _parse_story_markdown(content)
        if not story_data.get("title"):
             continue
        found_stories.append(story_data)
        match_idx = -1
        for i, item in enumerate(backlog):
            if (story_data.get("id") and item.get("id") == story_data["id"]) or \
               (story_data.get("title") and item.get("title") == story_data["title"]):
                match_idx = i
                break
        if match_idx >= 0:
            backlog[match_idx] = {**backlog[match_idx], **story_data}
        else:
            backlog.append(story_data)
        if story_data.get("status") in ["In Progress", "Done"]:
             sprint_idx = -1
             for i, item in enumerate(sprint_backlog):
                 if (story_data.get("id") and item.get("id") == story_data["id"]) or \
                    (story_data.get("title") and item.get("title") == story_data["title"]):
                     sprint_idx = i
                     break
             if sprint_idx >= 0:
                 sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **story_data}
             else:
                 sprint_backlog.append(story_data)
    s["product_backlog"] = backlog
    s["sprint_backlog"] = sprint_backlog
    return {"status": "ok", "synced": len(found_stories)}

def _parse_story_markdown(content: str) -> Dict[str, Any]:
    data = {}
    lines = content.splitlines()
    for line in lines:
        if line.strip().startswith("- Story ID:"):
            data["id"] = line.split(":", 1)[1].strip()
        elif line.strip().startswith("- Title:"):
            data["title"] = line.split(":", 1)[1].strip()
        elif line.strip().startswith("- Status:"):
            data["status"] = line.split(":", 1)[1].strip()
        elif line.strip().startswith("- Priority:"):
            data["priority"] = line.split(":", 1)[1].strip()
        elif line.startswith("## As a"):
            data["user_story"] = line.strip("#").strip()
    if "## Acceptance Criteria" in content:
        parts = content.split("## Acceptance Criteria")
        if len(parts) > 1:
            ac_section = parts[1].split("##")[0].strip()
            ac_lines = [l.strip("- ").strip() for l in ac_section.splitlines() if l.strip().startswith("-")]
            if ac_lines:
                data["acceptance_criteria"] = ac_lines
    return data

def _update_story_markdown(item: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    from datetime import datetime
    repo_root = _configured_repo_root(tool_context)
    item_id = item.get("id", "US-XXXX")
    title = item.get("title", "Untitled")
    filename = f"{item_id}-{title.replace(' ', '-').replace('/', '-')}.md"
    story_path = repo_root / "docs" / "stories" / filename
    template_path = repo_root / "docs" / "stories" / "TEMPLATE-USER-STORY.md"
    if not template_path.exists():
        template_path = _project_root() / "docs" / "stories" / "TEMPLATE-USER-STORY.md"
    if not template_path.exists():
        return {"status": "error", "message": "Template not found."}
    template_content = template_path.read_text(encoding="utf-8")
    status = item.get("status", "Draft")
    priority = item.get("priority", "Must")
    user_story = item.get("user_story", "As a <role>, I want <capability>, so that <benefit>.")
    ac = item.get("acceptance_criteria", [])
    ac_text = "\n".join([f"- {line}" for line in ac]) if isinstance(ac, list) else str(ac)
    notes = item.get("value_hypothesis", "") or item.get("rationale", "")
    if item.get("dependencies"):
        notes += f"\n- Dependencies: {item.get('dependencies')}"
    test_approach = item.get("test_approach", "")
    if item.get("tasks"):
        test_approach += "\n\n### Tasks\n" + "\n".join([f"- {t}" for t in item.get("tasks", [])])
    content = template_content
    content = content.replace("US-XXXX", item_id)
    content = content.replace("<short story title>", title)
    content = content.replace("Draft | Ready | In Progress | Done | Rejected", status)
    content = content.replace("Must | Should | Could | Won't", priority)
    content = content.replace("<name>", item.get("owner", "Scrum Team"))
    content = content.replace("<YYYY-MM-DD>", datetime.now().strftime("%Y-%m-%d"))
    content = content.replace("As a <role>, I want <capability>, so that <benefit>.", user_story)
    if "## Acceptance Criteria" in content:
        parts = content.split("## Acceptance Criteria")
        header = parts[0]
        rest = parts[1].split("##", 1)
        next_section = "##" + rest[1] if len(rest) > 1 else ""
        content = header + "## Acceptance Criteria\n" + ac_text + "\n\n" + next_section
    if "## Notes" in content:
        parts = content.split("## Notes")
        header = parts[0]
        rest = parts[1].split("##", 1)
        next_section = "##" + rest[1] if len(rest) > 1 else ""
        content = header + "## Notes\n" + notes + "\n\n" + next_section
    if "## Test Approach" in content:
        parts = content.split("## Test Approach")
        header = parts[0]
        content = header + "## Test Approach\n" + test_approach + "\n"
    try:
        story_path.parent.mkdir(parents=True, exist_ok=True)
        story_path.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(story_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
