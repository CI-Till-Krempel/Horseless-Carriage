# agents/scrum_team/tools/requirements.py
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from pathlib import Path
from .base import _configured_repo_root, _state_file_path, _project_root, _record_touched_file
from ..helpers import is_story_done

# Matches a bare item ID (US-0001, EP-0002, ...) and nothing else. Guards
# against building a filename like "US-0007-US-0001.md" when a story was
# (mis)titled with another item's ID instead of a real description - seen
# in real eval runs (0.1.0-run4/run5's specs/stories/US-0010-US-009.md,
# US-0007-US-0001.md, etc.).
_BARE_ID_PATTERN = re.compile(r"^[A-Za-z]{2,4}-\d{2,6}$")

# Matches a "filled-in" user story with every slot left empty (e.g. "As a ,
# I want , so that .") - distinct from the template's own placeholder text,
# which _update_story_markdown already substitutes away when a real value is
# given. Seen in real eval runs (0.1.0-run6): the model does replace the
# placeholder, just with nothing.
_BLANK_USER_STORY_PATTERN = re.compile(r"^as a\s*,\s*i want\s*,\s*so that\s*\.?\s*$", re.IGNORECASE)
_PLACEHOLDER_USER_STORY = "As a <role>, I want <capability>, so that <benefit>."


def _story_readiness_issues(item: Dict[str, Any], is_epic: bool) -> List[str]:
    """
    Mechanical content-completeness check, applied when a story is being
    marked Done (see spec-templates/DOD.md) - a doc-only checklist the model
    is *told* to consult doesn't stop it from writing an empty/garbage story
    anyway; this is the code-level backstop for the concrete failure modes
    actually observed (title reused as another item's ID, "As a , I want ,
    so that ." with every slot blank, no acceptance criteria at all).
    """
    issues = []
    title = (item.get("title") or "").strip()
    if not title or _BARE_ID_PATTERN.match(title):
        issues.append("title is missing or is just another item's ID, not a real description")
    if not is_epic:
        user_story = (item.get("user_story") or "").strip()
        if not user_story or user_story == _PLACEHOLDER_USER_STORY or _BLANK_USER_STORY_PATTERN.match(user_story):
            issues.append("user_story is missing, still the template placeholder, or has every slot left blank")
    if not item.get("acceptance_criteria"):
        issues.append("acceptance_criteria is empty")
    return issues

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
    Requirements Management: Update the product roadmap (specs/ROADMAP.md) for a specific version.
    """
    from .scrum import save_state_to_repo
    from .docs import seed_repository
    repo_root = _configured_repo_root(tool_context)
    roadmap_path = repo_root / "specs" / "ROADMAP.md"
    
    if not roadmap_path.exists():
        # Try to initialize from template
        template_path = _project_root() / "spec-templates" / "ROADMAP.md"
        if template_path.exists():
            roadmap_path.parent.mkdir(parents=True, exist_ok=True)
            content = template_path.read_text(encoding="utf-8", errors="replace")
            
            # Clean up example stories and placeholders from template
            cleaned_lines = []
            for line in content.splitlines():
                if "[ST-" in line and ("<" in line or "Your first story" in line or "Some enhancement" in line or "Future idea" in line):
                    continue
                cleaned_lines.append(line)
            
            roadmap_path.write_text("\n".join(cleaned_lines), encoding="utf-8")
        else:
            seed_repository(overwrite=False, tool_context=tool_context)
    
    if not roadmap_path.exists():
        return {"status": "error", "message": "ROADMAP.md not found and could not be seeded."}

    content = roadmap_path.read_text(encoding="utf-8", errors="replace")
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
                    # DEV updates completion status on sprint_backlog (via
                    # plan_sprint_backlog_item); PO's own product_backlog
                    # entry is never automatically synced from that - so
                    # checking product_backlog alone silently never sees a
                    # story marked Done during the sprint (roadmap checkbox
                    # stays unchecked forever). Check both, sprint_backlog
                    # first since it's the more current record during an
                    # active sprint, and treat "done" as sticky - if either
                    # list says Done, it's Done, regardless of what the
                    # other says.
                    sprint_data = next((x for x in tool_context.state.get("sprint_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                    product_data = next((x for x in tool_context.state.get("product_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                    s_data = {**product_data, **sprint_data}
                    s_id = s_data.get("id") or s
                    s_title = s_data.get("title") or ""
                    status = s_data.get("status", "To Do")
                    mark = " "
                    if is_story_done(status) or is_story_done(product_data.get("status")) or is_story_done(sprint_data.get("status")):
                        mark = "x"
                    elif str(status).strip().lower() == "in progress":
                        mark = "~"
                    elif str(status).strip().lower() == "in review":
                        mark = "R"
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
    _record_touched_file(str(roadmap_path.relative_to(repo_root)), tool_context)
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
    
    # If ID is missing or is a placeholder, generate a new one
    item_type = item.get("type", "User Story")
    prefix = "EP" if item_type == "Epic" else "US"
    placeholder = f"{prefix}-XXXX"
    
    if not item_id or item_id == placeholder:
        item_id = _generate_next_id(prefix, tool_context)
        item["id"] = item_id

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
    story_md_result = _update_story_markdown(updated_item, tool_context)
    if story_md_result.get("status") != "ok":
        # Surface the failure (e.g. a Definition-of-Ready rejection) instead
        # of silently discarding it, as this used to do unconditionally.
        return {"status": "error", "message": story_md_result.get("message"), "item": updated_item, "story_markdown": story_md_result}
    return {"status": "ok", "updated": (updated_item != item), "item": updated_item, "story_markdown": story_md_result}

def _generate_next_id(prefix: str, tool_context=None) -> str:
    s = tool_context.state
    backlog = s.get("product_backlog", [])
    
    max_num = 0
    for item in backlog:
        item_id = item.get("id", "")
        if item_id.startswith(f"{prefix}-"):
            try:
                # Handle cases like EP-0001 or US-0042
                num_str = item_id.split("-")[1]
                # Only take the numeric part if there's more text (though IDs should ideally just be Prefix-Number)
                numeric_part = ""
                for char in num_str:
                    if char.isdigit():
                        numeric_part += char
                    else:
                        break
                if numeric_part:
                    num = int(numeric_part)
                    if num > max_num:
                        max_num = num
            except (ValueError, IndexError):
                continue
    
    return f"{prefix}-{max_num + 1:04d}"

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
    Scan specs/stories/*.md and update state.
    """
    repo_root = _configured_repo_root(tool_context)
    stories_dir = repo_root / "specs" / "stories"
    if not stories_dir.exists():
        return {"status": "ok", "message": "No stories directory found."}
    s = tool_context.state
    backlog = list(s.get("product_backlog", []))
    sprint_backlog = list(s.get("sprint_backlog", []))
    found_stories = []
    for fp in stories_dir.glob("*.md"):
        if fp.name.startswith("TEMPLATE-") or fp.name == "README.md":
            continue
        content = fp.read_text(encoding="utf-8", errors="replace")
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

def sync_requirements_from_markdown(tool_context=None) -> Dict[str, Any]:
    """
    Scan specs/requirements/PRD-*.md and update state (vision, goals).
    """
    repo_root = _configured_repo_root(tool_context)
    req_dir = repo_root / "specs" / "requirements"
    if not req_dir.exists():
        return {"status": "ok", "message": "No requirements directory found."}
    
    s = tool_context.state
    prds = list(req_dir.glob("PRD-*.md"))
    if not prds:
        return {"status": "ok", "message": "No PRDs found."}
    
    # Use the first PRD found as the primary source of truth for vision/goals
    # In the future, we could look for the 'latest' or a specifically named one
    prds.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    primary_prd = prds[0]
    content = primary_prd.read_text(encoding="utf-8", errors="replace")
    
    # Basic parsing for vision and goals
    vision = ""
    goals = []
    
    lines = content.splitlines()
    in_goals = False
    
    for line in lines:
        l = line.strip()
        if l.startswith("# "):
            # Product Title might be here
            pass
        elif l.startswith("## ") and ("Summary" in l or "Vision" in l):
            # Start of vision section
            continue
        elif l.startswith("## ") and "Goal" in l:
            in_goals = True
            continue
        elif l.startswith("## "):
            in_goals = False
            
        if in_goals and l.startswith("- "):
            goal = l[2:].strip()
            if goal and "<" not in goal: # Skip placeholders
                goals.append(goal)
        elif not in_goals and not l.startswith("#") and not l.startswith("---") and l:
            # If we are in the first few non-header paragraphs, it might be the vision
            if len(vision) < 500: # Limit vision size
                 vision += l + " "

    if vision:
        s["product_vision"] = vision.strip()
    if goals:
        s["product_goals"] = goals
        
    return {"status": "ok", "vision_updated": bool(vision), "goals_updated": len(goals), "prd": primary_prd.name}

def _parse_story_markdown(content: str) -> Dict[str, Any]:
    data = {}
    lines = content.splitlines()
    for line in lines:
        if line.strip().startswith("- Story ID:"):
            data["id"] = line.split(":", 1)[1].strip()
            data["type"] = "User Story"
        elif line.strip().startswith("- Epic ID:"):
            data["id"] = line.split(":", 1)[1].strip()
            data["type"] = "Epic"
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
    
    item_type = item.get("type", "User Story")
    is_epic = item_type == "Epic"
    
    id_prefix = "EP" if is_epic else "US"
    template_name = "TEMPLATE-EPIC.md" if is_epic else "TEMPLATE-USER-STORY.md"
    id_placeholder = f"{id_prefix}-XXXX"
    title_placeholder = "<short epic title>" if is_epic else "<short story title>"
    
    item_id = item.get("id")
    if not item_id or item_id == id_placeholder:
        item_id = _generate_next_id(id_prefix, tool_context)
        item["id"] = item_id
        
    title = item.get("title", "Untitled")
    filename_title = "Untitled" if _BARE_ID_PATTERN.match(title.strip()) else title
    filename = f"{item_id}-{filename_title.replace(' ', '-').replace('/', '-')}.md"
    story_path = repo_root / "specs" / "stories" / filename

    template_path = repo_root / "spec-templates" / "stories" / template_name
    if not template_path.exists():
        template_path = _project_root() / "spec-templates" / "stories" / template_name

    if not template_path.exists():
        return {"status": "error", "message": f"Template {template_name} not found."}

    status = item.get("status", "Draft")
    # Gated on is_story_done rather than "any non-Draft status", since status
    # is a free-form string used for all sorts of intermediate states (test
    # fixtures alone use "new"/"in_progress") - blocking all of those would
    # be far more disruptive than the actual observed failure mode: a story
    # explicitly marked *Done* with garbage/missing content (title reused as
    # another item's ID, blank user story, no acceptance criteria).
    if is_story_done(status):
        issues = _story_readiness_issues(item, is_epic)
        if issues:
            return {
                "status": "error",
                "message": (
                    "Fails Definition of Done (see spec-templates/DOD.md) - not written: "
                    + "; ".join(issues) + ". Fix the content before marking this Done."
                ),
                "issues": issues,
            }

    from .docs import _strip_agent_safeguard_comments
    template_content = _strip_agent_safeguard_comments(template_path.read_text(encoding="utf-8", errors="replace"))
    priority = item.get("priority", "Must")
    
    # User story text for stories, or overview for epics
    user_story = item.get("user_story", "")
    overview = item.get("overview", "") or item.get("description", "")
    
    if not user_story and not is_epic:
         user_story = "As a <role>, I want <capability>, so that <benefit>."
    
    ac = item.get("acceptance_criteria", [])
    ac_text = "\n".join([f"- {line}" for line in ac]) if isinstance(ac, list) else str(ac)
    notes = item.get("value_hypothesis", "") or item.get("rationale", "")
    if item.get("dependencies"):
        notes += f"\n- Dependencies: {item.get('dependencies')}"
    
    test_approach = item.get("test_approach", "")
    if item.get("tasks"):
        test_approach += "\n\n### Tasks\n" + "\n".join([f"- {t}" for t in item.get("tasks", [])])
    
    content = template_content
    content = content.replace(id_placeholder, item_id)
    content = content.replace(title_placeholder, title)
    content = content.replace("Draft | Ready | In Progress | Done | Rejected", status)
    content = content.replace("Must | Should | Could | Won't", priority)
    content = content.replace("<name>", item.get("owner", "Scrum Team"))
    content = content.replace("<YYYY-MM-DD>", datetime.now().strftime("%Y-%m-%d"))
    
    if is_epic:
        content = content.replace("<Describe the high-level business value and scope of this epic>", overview)
    else:
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
        _record_touched_file(str(story_path.relative_to(repo_root)), tool_context)
        return {"status": "ok", "path": str(story_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
