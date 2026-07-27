# agents/scrum_team/tools/requirements.py
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from pathlib import Path
from .base import _configured_repo_root, _state_file_path, _project_root, _record_touched_file
from ..helpers import (
    is_story_done,
    STORY_STAGES,
    STAGE_OWNERS,
    blocks_direct_status_set,
    is_source_file,
    required_pre_implementation_approval,
)

# Matches a bare item ID (US-0001, EP-0002, ISSUE-0003, ...) and nothing
# else. Guards against building a filename like "US-0007-US-0001.md" when a
# story was (mis)titled with another item's ID instead of a real
# description - seen in real eval runs (0.1.0-run4/run5's
# specs/stories/US-0010-US-009.md, US-0007-US-0001.md, etc.).
_BARE_ID_PATTERN = re.compile(r"^[A-Za-z]{2,6}-\d{2,6}$")

# Backlog item type -> ID prefix. "User Story" (the implicit default for
# any unrecognized/absent type) isn't listed here on purpose - see
# upsert_backlog_item's prefix lookup.
_ID_PREFIXES = {
    "Epic": "EP",
    "Issue": "ISSUE",
}

# Matches a "filled-in" user story with every slot left empty (e.g. "As a ,
# I want , so that .") - distinct from the template's own placeholder text,
# which _update_story_markdown already substitutes away when a real value is
# given. Seen in real eval runs (0.1.0-run6): the model does replace the
# placeholder, just with nothing.
_BLANK_USER_STORY_PATTERN = re.compile(r"^as a\s*,\s*i want\s*,\s*so that\s*\.?\s*$", re.IGNORECASE)
_PLACEHOLDER_USER_STORY = "As a <role>, I want <capability>, so that <benefit>."

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_title_for_dup_check(title: str) -> str:
    """Case/punctuation-insensitive key for near-exact title matches (see ISSUE-0008)."""
    return _NON_ALNUM_RE.sub("", (title or "").lower())


def _story_readiness_issues(item: Dict[str, Any], is_epic: bool, is_issue: bool = False) -> List[str]:
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
    if is_issue:
        overview = (item.get("overview") or item.get("description") or "").strip()
        if not overview:
            issues.append("overview/description is empty - describe the concrete gap this issue documents")
    elif not is_epic:
        user_story = (item.get("user_story") or "").strip()
        if not user_story or user_story == _PLACEHOLDER_USER_STORY or _BLANK_USER_STORY_PATTERN.match(user_story):
            issues.append("user_story is missing, still the template placeholder, or has every slot left blank")
    if not item.get("acceptance_criteria"):
        issues.append("acceptance_criteria is empty")
    return issues


def _story_stages_completed(product_data: Dict[str, Any], sprint_data: Dict[str, Any]) -> List[str]:
    """
    Merges stages_completed from both backlog copies (union, since
    advance_story_stage writes the same value to both but older/drifted
    data shouldn't silently lose progress either way) into STORY_STAGES
    order. A story from before this pipeline existed (bare status="Done"/
    "done"/etc., no stages_completed at all) is treated as having
    completed every stage, rather than being silently reset to square one.
    """
    combined = set(product_data.get("stages_completed") or []) | set(sprint_data.get("stages_completed") or [])
    if combined:
        return [st for st in STORY_STAGES if st in combined]
    if is_story_done(sprint_data.get("status") or product_data.get("status")):
        return list(STORY_STAGES)
    return []


def _render_story_block(story_id: str, title: str, stages_completed: List[str]) -> List[str]:
    lines = [f"- [{story_id}] {title}"]
    for stage in STORY_STAGES:
        mark = "x" if stage in stages_completed else " "
        lines.append(f"  - [{mark}] {stage.upper()}")
    return lines


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

def upsert_issue(issue: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Add or update an Issue by ID or Title (ISSUE-XXXX,
    auto-generated if not provided). An Issue documents a concrete gap
    (e.g. a mandatory process rule that isn't actually enforced in code) -
    it's a requirement type, but goes through the same story development
    process as a User Story once picked up: `advance_story_stage` treats it
    like any other non-Epic backlog item (Ready -> Implemented -> Reviewed
    -> Tested -> Accepted), just documented and filed under
    `specs/requirements/` instead of `specs/stories/` - see
    spec-templates/requirements/TEMPLATE-ISSUE.md.
    """
    issue["type"] = "Issue"
    return upsert_backlog_item(issue, tool_context=tool_context)

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

    # `version in line` alone is a substring match: "v0.1" also matches
    # "### v0.1 Kanban" (a *different* heading, from the Task board section
    # further down in the template), which used to inject a spurious extra
    # copy of the Stories list there too. Anchor on a real version heading:
    # either the bare "### v0.1" this function's own insertion writes, or
    # the template's "### v0.1 — MVP (...)" form - never "### v0.1 <word>".
    version_heading_re = re.compile(rf"^###\s+{re.escape(version)}(\s*$|\s+—)")

    i = 0
    while i < len(lines):
        line = lines[i]
        if version_heading_re.match(line):
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
                    # plan_sprint_backlog_item/advance_story_stage); PO's own
                    # product_backlog entry is never automatically synced
                    # from that - so checking product_backlog alone silently
                    # never sees a story marked Done during the sprint
                    # (roadmap checkbox stays unchecked forever). Check both.
                    sprint_data = next((x for x in tool_context.state.get("sprint_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                    product_data = next((x for x in tool_context.state.get("product_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                    s_id = product_data.get("id") or sprint_data.get("id") or s
                    s_title = product_data.get("title") or sprint_data.get("title") or ""
                    stages_completed = _story_stages_completed(product_data, sprint_data)
                    new_lines.extend(_render_story_block(s_id, s_title, stages_completed))
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
            for s in stories:
                sprint_data = next((x for x in tool_context.state.get("sprint_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                product_data = next((x for x in tool_context.state.get("product_backlog", []) if x.get("id") == s or x.get("title") == s), {})
                s_id = product_data.get("id") or sprint_data.get("id") or s
                s_title = product_data.get("title") or sprint_data.get("title") or s
                stages_completed = _story_stages_completed(product_data, sprint_data)
                insertion.extend(_render_story_block(s_id, s_title, stages_completed))
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
    from .scrum import save_state_to_repo
    res = {"status": "ok", "updates": []}
    if priority:
        p_res = set_priority(title_or_id, priority, tool_context=tool_context)
        res["updates"].append({"type": "priority", "result": p_res})
    if version:
        # Persisted on the backlog item itself (not just passed through to
        # this one update_roadmap call) so advance_story_stage can later
        # find every story in this version on its own, without the caller
        # having to re-enumerate them each time a single story advances.
        s = tool_context.state
        backlog = list(s.get("product_backlog", []))
        idx = next((i for i, x in enumerate(backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
        if idx is not None:
            backlog[idx] = {**backlog[idx], "version": version}
            s["product_backlog"] = backlog
            save_state_to_repo(tool_context)
        r_res = update_roadmap(version, stories=[title_or_id], tool_context=tool_context)
        res["updates"].append({"type": "roadmap", "result": r_res})
    return res

def upsert_backlog_item(item: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add or update a product backlog item.
    """
    from .scrum import save_state_to_repo

    if blocks_direct_status_set(item.get("status")):
        return {
            "status": "error",
            "message": (
                f"Cannot set status to '{item.get('status')}' directly - stage transitions (and "
                "legacy 'Done'/'completed'/'closed', which are treated as every stage complete) "
                "must go through advance_story_stage(title_or_id, stage), which enforces ordering "
                "and stage ownership. Omit 'status' here (or set it to something outside the "
                "pipeline, e.g. 'Draft') and call advance_story_stage instead."
            ),
        }

    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))
    item_id = item.get("id")
    title = item.get("title")
    
    # If ID is missing or is a placeholder, generate a new one
    item_type = item.get("type", "User Story")
    prefix = _ID_PREFIXES.get(item_type, "US")
    placeholder = f"{prefix}-XXXX"
    
    if not item_id or item_id == placeholder:
        item_id = _generate_next_id(prefix, tool_context)
        item["id"] = item_id

    if not item_id and not title:
        return {"status": "error", "message": "Backlog item needs at least 'id' or 'title'."}

    def matches(x: Dict[str, Any]) -> bool:
        return (item_id and x.get("id") == item_id) or (title and x.get("title") == title)

    # ISSUE-0008: warn (don't block - a naive check would false-positive on
    # legitimately similar titles) when this title is a near-exact match of
    # a *different* existing item's title, so duplicate/conflicting story
    # files are at least surfaced instead of silently created.
    duplicate_warning = None
    if title:
        norm_title = _normalize_title_for_dup_check(title)
        if norm_title:
            for x in backlog:
                if matches(x):
                    continue
                if _normalize_title_for_dup_check(x.get("title", "")) == norm_title:
                    duplicate_warning = (
                        f"Title is a near-exact match of existing item '{x.get('id') or x.get('title')}' "
                        "- check specs/ (see list_docs/read_doc) for an existing story/epic/issue "
                        "before creating a new one."
                    )
                    break

    old_version = None
    updated_item = None
    for i, x in enumerate(backlog):
        if matches(x):
            old_version = x.get("version")
            backlog[i] = {**x, **item}
            s["product_backlog"] = backlog
            updated_item = backlog[i]
            break
    if not updated_item:
        backlog.append(item)
        s["product_backlog"] = backlog
        updated_item = item

    save_state_to_repo(tool_context)

    # ISSUE-0007: a story's version can be set directly here, bypassing
    # plan_backlog_item - without this, specs/ROADMAP.md would silently
    # never learn about it, the same class of bug as the already-fixed
    # direct-status bypass (see blocks_direct_status_set), just for
    # version instead of status. Priority needs no equivalent sync: nothing
    # in specs/ROADMAP.md is rendered from priority.
    new_version = updated_item.get("version")
    if new_version and new_version != old_version:
        _ = _sync_roadmap_for_story(item_id or title, tool_context)

    story_md_result = _update_story_markdown(updated_item, tool_context)
    if story_md_result.get("status") != "ok":
        # Surface the failure (e.g. a Definition-of-Ready rejection) instead
        # of silently discarding it, as this used to do unconditionally.
        return {
            "status": "error",
            "message": story_md_result.get("message"),
            "item": updated_item,
            "story_markdown": story_md_result,
            "duplicate_warning": duplicate_warning,
        }
    return {
        "status": "ok",
        "updated": (updated_item != item),
        "item": updated_item,
        "story_markdown": story_md_result,
        "duplicate_warning": duplicate_warning,
    }

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
    Scan specs/stories/*.md and specs/requirements/ISSUE-*.md (Issues are
    filed under requirements/, see upsert_issue, but go through this same
    story-sync path since they're driven through the same advance_story_stage
    pipeline) and update state.
    """
    repo_root = _configured_repo_root(tool_context)
    stories_dir = repo_root / "specs" / "stories"
    requirements_dir = repo_root / "specs" / "requirements"
    candidates = []
    if stories_dir.exists():
        candidates.extend(stories_dir.glob("*.md"))
    if requirements_dir.exists():
        candidates.extend(requirements_dir.glob("ISSUE-*.md"))
    if not candidates:
        return {"status": "ok", "message": "No stories or issues directory found."}
    s = tool_context.state
    backlog = list(s.get("product_backlog", []))
    sprint_backlog = list(s.get("sprint_backlog", []))
    found_stories = []
    for fp in candidates:
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
        elif line.strip().startswith("- Issue ID:"):
            data["id"] = line.split(":", 1)[1].strip()
            data["type"] = "Issue"
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
    is_issue = item_type == "Issue"

    id_prefix = _ID_PREFIXES.get(item_type, "US")
    if is_epic:
        template_name = "TEMPLATE-EPIC.md"
    elif is_issue:
        template_name = "TEMPLATE-ISSUE.md"
    else:
        template_name = "TEMPLATE-USER-STORY.md"
    id_placeholder = f"{id_prefix}-XXXX"
    if is_epic:
        title_placeholder = "<short epic title>"
    elif is_issue:
        title_placeholder = "<short issue title>"
    else:
        title_placeholder = "<short story title>"

    item_id = item.get("id")
    if not item_id or item_id == id_placeholder:
        item_id = _generate_next_id(id_prefix, tool_context)
        item["id"] = item_id

    title = item.get("title", "Untitled")
    filename_title = "Untitled" if _BARE_ID_PATTERN.match(title.strip()) else title
    filename = f"{item_id}-{filename_title.replace(' ', '-').replace('/', '-')}.md"
    # Issues document a process/tooling gap rather than a product feature, so
    # they're filed under specs/requirements/ (see
    # spec-templates/requirements/TEMPLATE-ISSUE.md) instead of
    # specs/stories/ - same pipeline (advance_story_stage), different shelf.
    dest_subdir = "requirements" if is_issue else "stories"
    story_path = repo_root / "specs" / dest_subdir / filename

    template_path = repo_root / "spec-templates" / dest_subdir / template_name
    if not template_path.exists():
        template_path = _project_root() / "spec-templates" / dest_subdir / template_name

    if not template_path.exists():
        return {"status": "error", "message": f"Template {template_name} not found."}

    status = item.get("status", "Draft")
    # Gated on is_story_done/"Ready" rather than "any non-Draft status",
    # since status is a free-form string used for all sorts of intermediate
    # states (test fixtures alone use "new"/"in_progress") - blocking all of
    # those would be far more disruptive than the actual observed failure
    # modes: a story marked *Ready* (start of the pipeline - see
    # spec-templates/DOR.md/advance_story_stage) or *Accepted*/Done (end of
    # it) with garbage/missing content (title reused as another item's ID,
    # blank or still-placeholder user story, no acceptance criteria).
    if is_story_done(status) or str(status).strip().lower() == "ready":
        issues = _story_readiness_issues(item, is_epic, is_issue)
        if issues:
            return {
                "status": "error",
                "message": (
                    f"Fails Definition of Ready/Done (see spec-templates/DOR.md, DOD.md) for status "
                    f"'{status}' - not written: " + "; ".join(issues) + ". Fix the content and retry."
                ),
                "issues": issues,
            }

    from .docs import _strip_agent_safeguard_comments
    template_content = _strip_agent_safeguard_comments(template_path.read_text(encoding="utf-8", errors="replace"))
    priority = item.get("priority", "Must")
    
    # User story text for stories, or overview for epics/issues
    user_story = item.get("user_story", "")
    overview = item.get("overview", "") or item.get("description", "")

    if not user_story and not is_epic and not is_issue:
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
    elif is_issue:
        content = content.replace("<Describe the concrete gap - e.g. a mandatory process rule that isn't actually enforced in code>", overview)
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


def _preceding_story(product_backlog: List[Dict[str, Any]], story_id: str, title: str) -> Dict[str, Any] | None:
    """
    The nearest User Story before story_id/title in product_backlog order -
    backlog order is priority order (see RELEASE.md "Story workflow").
    Epics are skipped: they aren't advanced through the 5-stage pipeline
    themselves, so they shouldn't block a real story behind them.
    """
    stories_only = [x for x in product_backlog if x.get("type", "User Story") != "Epic"]
    idx = next(
        (i for i, x in enumerate(stories_only) if x.get("id") == story_id or x.get("title") == title),
        None,
    )
    if idx is None or idx == 0:
        return None
    return stories_only[idx - 1]


def _sync_roadmap_for_story(story_id: str, tool_context) -> Dict[str, Any]:
    """
    Re-renders the roadmap's Stories block for story_id's version (falling
    back to the "Backlog (unplanned)" section, which already exists in the
    ROADMAP.md template, if the story was never assigned one via
    plan_backlog_item) - including every other story sharing that version,
    since update_roadmap replaces the whole Stories block for a version in
    one pass, not a single line.
    """
    s = tool_context.state
    backlog = s.get("product_backlog", []) or []
    match = next((x for x in backlog if x.get("id") == story_id or x.get("title") == story_id), None)
    version = (match or {}).get("version") or "Backlog (unplanned)"
    peers = [
        (x.get("id") or x.get("title"))
        for x in backlog
        if x.get("type", "User Story") != "Epic" and (x.get("version") or "Backlog (unplanned)") == version
    ]
    if story_id not in peers:
        peers.append(story_id)
    return update_roadmap(version, stories=peers, tool_context=tool_context)


def sync_all_active_stories_to_roadmap(tool_context) -> Dict[str, Any]:
    """
    Re-syncs specs/ROADMAP.md for EVERY version present in product_backlog,
    not just one story's peers (see _sync_roadmap_for_story above) - there's
    no single story to key off of when there's no agent turn left to name
    one. Used as a mechanical, non-agent-callable last-gasp sync when the
    sprint's token budget runs out mid-turn (see
    _sync_and_commit_roadmap_on_exhaustion in agent.py's
    check_cost_budget_callback), so specs/ROADMAP.md documents wherever
    every story actually landed, not just whichever one happened to be in
    progress when the callback tripped.
    """
    s = tool_context.state
    backlog = s.get("product_backlog", []) or []
    versions: Dict[str, List[str]] = {}
    for x in backlog:
        if x.get("type", "User Story") == "Epic":
            continue
        version = x.get("version") or "Backlog (unplanned)"
        versions.setdefault(version, []).append(x.get("id") or x.get("title"))

    results = {}
    overall_ok = True
    for version, story_ids in versions.items():
        res = update_roadmap(version, stories=story_ids, tool_context=tool_context)
        results[version] = res
        if res.get("status") != "ok":
            overall_ok = False
    return {"status": "ok" if overall_ok else "error", "versions_synced": list(versions.keys()), "results": results}


def advance_story_stage(title_or_id: str, stage: str, tool_context=None) -> Dict[str, Any]:
    """
    The single, mandatory mechanism for moving a story through the fixed
    Ready -> Implemented -> Reviewed -> Tested -> Accepted pipeline (see
    RELEASE.md "Story workflow" and spec-templates/DOD.md, DOR.md).

    Enforces, in code rather than by asking nicely in a prompt (which
    repeatedly wasn't enough on its own in real eval runs):
    - Stages complete strictly in order - no skipping.
    - Only the stage's owning role (STAGE_OWNERS, agents/scrum_team/
      helpers.py) may complete it.
    - Stories are worked one at a time, top to bottom, in backlog priority
      order: the immediately-preceding story (product_backlog order) must
      already be Accepted.

    Updates the story's own state AND specs/ROADMAP.md's per-stage
    checkboxes atomically in one call, instead of relying on the agent to
    remember a separate "now go update the roadmap" step.
    """
    from .scrum import save_state_to_repo

    if stage not in STORY_STAGES:
        return {"status": "error", "message": f"Unknown stage '{stage}'. Must be one of {STORY_STAGES}."}

    s = tool_context.state
    agent_name = getattr(tool_context, "agent_name", None)
    expected_owner = STAGE_OWNERS[stage]
    if agent_name and agent_name != expected_owner:
        return {
            "status": "error",
            "message": (
                f"Stage '{stage}' can only be completed by {expected_owner}, not {agent_name}. "
                "See RELEASE.md \"Story workflow\"."
            ),
        }

    sprint_backlog = list(s.get("sprint_backlog", []))
    product_backlog = list(s.get("product_backlog", []))
    sprint_idx = next((i for i, x in enumerate(sprint_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    product_idx = next((i for i, x in enumerate(product_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    if sprint_idx is None and product_idx is None:
        return {"status": "error", "message": f"No story found matching '{title_or_id}'."}

    sprint_item = sprint_backlog[sprint_idx] if sprint_idx is not None else {}
    product_item = product_backlog[product_idx] if product_idx is not None else {}
    story_id = product_item.get("id") or sprint_item.get("id") or title_or_id
    title = product_item.get("title") or sprint_item.get("title") or title_or_id
    stages_completed = set(_story_stages_completed(product_item, sprint_item))

    target_idx = STORY_STAGES.index(stage)
    missing = [st for st in STORY_STAGES[:target_idx] if st not in stages_completed]
    if missing:
        return {
            "status": "error",
            "message": (
                f"Cannot mark '{story_id}' as {stage} - it hasn't completed {missing} yet. "
                f"Stages complete strictly in order: {STORY_STAGES}."
            ),
        }
    if stage in stages_completed:
        return {
            "status": "ok",
            "message": f"{story_id} already marked {stage}.",
            "stages_completed": sorted(stages_completed, key=STORY_STAGES.index),
        }

    preceding = _preceding_story(product_backlog, story_id, title)
    if preceding is not None and "Accepted" not in _story_stages_completed(preceding, {}):
        return {
            "status": "error",
            "message": (
                f"Cannot advance '{story_id}' to {stage} - the higher-priority story "
                f"'{preceding.get('id') or preceding.get('title')}' must reach Accepted first. "
                "Stories are worked one at a time, top to bottom, in backlog priority order."
            ),
        }

    # Stage-specific content/process gates (ISSUE-0001 through ISSUE-0005,
    # ISSUE-0010) - advance_story_stage's ordering/ownership checks above
    # don't verify anything actually happened at the stage being claimed;
    # these do. Each baseline is bumped only once the transition actually
    # succeeds (see below), following the same "must be NEW since last
    # time" pattern as create_sprint_report's retro_baseline.
    source_touch_count = None
    architect_review_count = None
    qa_review_count = None
    if stage == "Implemented":
        # Which approval type (if any) is required depends on the configured
        # INTERACTION_LEVEL (see docs/INTERACTION-LEVELS.md) - e.g. "budget"
        # instead of "sprint" at the CEO level, none at all for EVAL.
        required_approval = required_pre_implementation_approval()
        if required_approval:
            approvals = sum(1 for a in s.get("human_approvals", []) if a.get("type") == required_approval)
            if approvals <= s.get("sprint_approval_baseline", 0):
                return {
                    "status": "error",
                    "message": (
                        f"Cannot mark a story Implemented - this interaction level requires a fresh "
                        f"'{required_approval}' human approval for this sprint - call "
                        f"record_human_approval('{required_approval}', ...) first (see "
                        "docs/INTERACTION-LEVELS.md)."
                    ),
                }
        if s.get("sprint_report_pending_release"):
            return {
                "status": "error",
                "message": (
                    "A sprint report was already created but create_release_pr hasn't succeeded "
                    "yet for it - finish the release before implementing further stories (see "
                    "ORCHESTRATOR_PROMPT SPRINT CLOSE SEQUENCE)."
                ),
            }
        is_spike = bool(product_item.get("spike") or sprint_item.get("spike"))
        touched = s.get("sprint_files_touched", []) or []
        source_touch_count = sum(1 for f in touched if is_source_file(f))
        if not is_spike and source_touch_count <= s.get("dev_touch_baseline", 0):
            return {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Implemented - no real source file has been written "
                    "via write_file since the last story was Implemented (story/spec markdown "
                    "doesn't count). Write the actual code, or set {'spike': true} on this backlog "
                    "item if it's a genuine planning/spike story with no code to write."
                ),
            }
        estimates = s.get("story_estimates", {}) or {}
        est_entry = estimates.get(story_id) or estimates.get(title) or estimates.get(title_or_id)
        if not isinstance(est_entry, dict) or est_entry.get("actual") is None:
            return {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Implemented - actual tokens spent haven't been "
                    "logged yet. Call log_story_tokens(title_or_id, actual_tokens) first."
                ),
            }
    elif stage == "Reviewed":
        pr_calls = s.get("pr_review_calls", {}) or {}
        architect_review_count = pr_calls.get("Architect", 0)
        if architect_review_count <= s.get("architect_review_baseline", 0):
            return {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Reviewed - no gh_pr_review/gh_pr_comment call from "
                    "Architect has been recorded since the last story was Reviewed. Leave an actual "
                    "review comment on the PR first."
                ),
            }
    elif stage == "Tested":
        pr_calls = s.get("pr_review_calls", {}) or {}
        qa_review_count = pr_calls.get("QA", 0)
        if qa_review_count <= s.get("qa_review_baseline", 0):
            return {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - no gh_pr_review/gh_pr_comment call from QA "
                    "has been recorded since the last story was Tested. Leave an actual review "
                    "comment on the PR first."
                ),
            }
        last_build = s.get("last_check_build")
        if not last_build:
            return {
                "status": "error",
                "message": f"Cannot mark '{story_id}' Tested - check_build() hasn't been called yet. Call it first.",
            }
        if last_build.get("passing") is False:
            return {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - the last check_build() result failed. Fix "
                    "the build and call check_build() again until it passes before retrying."
                ),
            }

    stages_completed.add(stage)
    ordered_stages = sorted(stages_completed, key=STORY_STAGES.index)
    update = {"stages_completed": ordered_stages, "status": stage}

    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    # Bump the relevant baseline now that the transition actually happened -
    # the story's own stage IS recorded in state regardless of the
    # markdown/roadmap sync outcome below, so these bump unconditionally too.
    if stage == "Implemented" and source_touch_count is not None:
        s["dev_touch_baseline"] = source_touch_count
    elif stage == "Reviewed" and architect_review_count is not None:
        s["architect_review_baseline"] = architect_review_count
    elif stage == "Tested" and qa_review_count is not None:
        s["qa_review_baseline"] = qa_review_count

    save_state_to_repo(tool_context)

    merged_item = {**product_item, **sprint_item, **update, "id": story_id, "title": title}
    story_md_result = _update_story_markdown(merged_item, tool_context)
    roadmap_result = _sync_roadmap_for_story(story_id, tool_context)

    # The stage IS recorded in state at this point regardless (state was
    # already saved above) - but this call must not report "ok" if the
    # roadmap/story-file sync it promises didn't actually happen. Silently
    # reporting success while specs/ROADMAP.md stays stale is exactly the
    # failure mode this whole mechanism exists to close.
    synced = story_md_result.get("status") == "ok" and roadmap_result.get("status") == "ok"
    return {
        "status": "ok" if synced else "error",
        "message": None if synced else (
            f"'{story_id}' is recorded as {stage} in state, but syncing specs/ROADMAP.md and/or "
            "the story file failed - see story_markdown/roadmap_sync for details. Retry so the "
            "roadmap actually reflects this."
        ),
        "story_id": story_id,
        "stage": stage,
        "stages_completed": ordered_stages,
        "story_markdown": story_md_result,
        "roadmap_sync": roadmap_result,
    }
