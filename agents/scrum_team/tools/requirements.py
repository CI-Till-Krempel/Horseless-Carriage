# agents/scrum_team/tools/requirements.py
from __future__ import annotations
import re
from typing import Any, Dict, List
from pathlib import Path
from .base import _configured_repo_root, _state_file_path, _project_root, _record_touched_file, _coerce_dict_arg
from ..helpers import (
    is_story_done,
    STORY_STAGES,
    STAGE_OWNERS,
    blocks_direct_status_set,
    is_source_file,
    required_pre_implementation_approval,
    requires_pre_ready_design_approval,
    BLOCKER_CATEGORIES,
    BLOCKER_CATEGORY_OWNERS,
    should_escalate_blocker_to_user,
    sprint_backlog_pr_missing,
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

# The three stage transitions that are conceptually a "review" someone can
# deny (Architect's code review, QA's test review, PO's acceptance check) -
# see deny_review below. Draft/Ready/Implemented aren't reviews of someone
# else's completed work in the same sense, so they're not deniable this way.
_DENIABLE_REVIEW_STAGES = ("Reviewed", "Tested", "Accepted")

# A real eval run showed advance_story_stage('US-001', 'Tested') rejected 9
# times in a row for the identical reason, with neither of agent.py's own
# loop breakers (_detect_transfer_loop/_detect_repeated_call_loop) ever
# tripping - both only catch a single action repeated back-to-back with
# nothing else interleaved, but this loop was a repeating multi-step
# SEQUENCE (check_build -> gh_pr_comment -> advance_story_stage(rejected) ->
# transfer_to_agent -> DevTeam edits/pushes -> transfer back), so no single
# call ever repeated "in a row" even though the same (story, stage) pair
# kept failing the same way. See _reject_stage_transition below - this
# threshold matches TRANSFER_LOOP_THRESHOLD/REPEATED_CALL_LOOP_THRESHOLD's
# existing convention (agents/scrum_team/agent.py).
STAGE_REJECTION_LOOP_THRESHOLD = 3


def _reject_stage_transition(tool_context, story_id: str, stage: str, error_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wraps every content/evidence-gate rejection inside advance_story_stage
    (the Ready/Implemented/Reviewed/Tested/Accepted `elif` blocks) - never
    the structural checks above them (unknown stage, wrong owner,
    out-of-order, already BLOCKED), which aren't "the team attempting the
    same real work and failing" the same way. Tracks a rejection streak per
    (story_id, stage) pair in state (mirrors agent.py's own
    _transfer_loop/_repeated_call_loop state-dict pattern) that survives
    whatever other genuine tool calls happen between attempts - that's
    exactly what let the real eval run's loop evade the two existing
    breakers. Below STAGE_REJECTION_LOOP_THRESHOLD, returns error_response
    unchanged; at the threshold, raises a story blocker instead (same
    escalation _detect_transfer_loop already uses) and returns a
    loop-detected message, so the team moves on rather than repeating the
    identical failure forever.
    """
    s = tool_context.state
    key = f"{story_id}:{stage}"
    streaks = dict(s.get("_stage_rejection_streaks") or {})
    count = streaks.get(key, 0) + 1
    streaks[key] = count
    s["_stage_rejection_streaks"] = streaks

    if count < STAGE_REJECTION_LOOP_THRESHOLD:
        return error_response

    streaks[key] = 0
    s["_stage_rejection_streaks"] = streaks
    from ..helpers import infer_blocker_category
    agent_name = getattr(tool_context, "agent_name", None) or STAGE_OWNERS.get(stage, "")
    question = (
        f"advance_story_stage('{story_id}', '{stage}') has been rejected {count} times in a row for "
        f"the same reason - most recently: {error_response.get('message')}"
    )
    block_result = raise_story_blocker(story_id, question, infer_blocker_category(agent_name), tool_context=tool_context)
    if block_result.get("status") == "ok":
        return {
            "status": "error",
            "message": (
                f"\U0001f501 [STAGE REJECTION LOOP DETECTED] '{story_id}' has failed to reach {stage} "
                f"the same way {count} times in a row - marking it BLOCKED instead of rejecting again. "
                f"{question} Call resolve_story_blocker('{story_id}', resolution) once this has "
                "genuinely been addressed, then retry."
            ),
        }
    # raise_story_blocker itself failed (e.g. this story is already BLOCKED
    # for a different reason) - fall back to the original rejection rather
    # than lose it.
    return error_response

# Mirrors _story_readiness_issues' placeholder philosophy, applied to a
# denial's own reason text instead of a story's content: a rejection that
# just restates "denied" without saying what's actually wrong isn't
# something DevTeam can act on any better than no reason at all. Matched
# case-insensitively against the *whole* stripped reason (not a substring
# search), so a real explanation that happens to contain one of these words
# is never blocked.
_GENERIC_DENIAL_REASONS = {
    "no", "not good", "bad", "needs work", "not approved", "denied", "rejected",
    "does not meet criteria", "doesn't meet criteria", "fix it", "try again",
    "n/a", "na", "tbd", "not ready", "incomplete", "not done",
}
_MIN_DENIAL_REASON_LENGTH = 15


def _is_concrete_denial_reason(reason: str) -> bool:
    """
    A denial reason must be real, specific, actionable text - not empty, not
    a template placeholder, and not a generic one-liner that just restates
    the verdict itself without saying what's actually wrong or what would
    need to change. See ARCHITECTURE.md "Enforce Mandatory Process
    Mechanically, Not Just by Prompting" - the same principle
    _story_readiness_issues already applies to story content, applied here
    to the *reason* a review is denied.
    """
    cleaned = (reason or "").strip()
    if len(cleaned) < _MIN_DENIAL_REASON_LENGTH:
        return False
    if "<" in cleaned and ">" in cleaned:
        return False
    if cleaned.lower().rstrip(".!") in _GENERIC_DENIAL_REASONS:
        return False
    return True

# GH issue #121: MoSCoW rank used to keep product_backlog actually sorted in
# priority order. _preceding_story's docstring already asserted "backlog
# order is priority order" as an existing invariant, but nothing enforced
# it - items were only ever appended or mutated in place, so a story marked
# "Must" after the fact never actually moved ahead of the lower-priority
# stories still blocking it in the one-story-at-a-time gate. Unset/unknown
# priority ranks as "Must" (0), matching the "Must" default already used
# when rendering a story's priority elsewhere (see item.get("priority",
# "Must") below) - a story nobody has explicitly deprioritized shouldn't be
# silently pushed to the back of the queue.
_PRIORITY_RANK = {"Must": 0, "Should": 1, "Could": 2, "Won't": 3}


def _priority_rank(item: Dict[str, Any]) -> int:
    return _PRIORITY_RANK.get(item.get("priority"), _PRIORITY_RANK["Must"])


def _resort_backlog_by_priority(s: Dict[str, Any]) -> None:
    """Stable-sorts product_backlog by MoSCoW rank in place - stable so
    items sharing a priority keep their existing relative order."""
    backlog = list(s.get("product_backlog", []))
    backlog.sort(key=_priority_rank)
    s["product_backlog"] = backlog


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


def _coerce_backlog_item_dict(value: Any, tool_name: str) -> Dict[str, Any]:
    """Thin alias kept for this module's call sites - see _coerce_dict_arg
    (agents/scrum_team/tools/base.py) for the JSON/Python-repr recovery
    logic, shared with update_sprint_report's kpis argument."""
    return _coerce_dict_arg(value, tool_name)


def upsert_story(story: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Add or update a User Story by ID or Title.
    """
    try:
        story = _coerce_backlog_item_dict(story, "upsert_story")
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    story["type"] = "User Story"
    return upsert_backlog_item(story, tool_context=tool_context)

def upsert_epic(epic: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Requirements Management: Add or update an Epic by ID or Title.
    """
    try:
        epic = _coerce_backlog_item_dict(epic, "upsert_epic")
    except ValueError as e:
        return {"status": "error", "message": str(e)}
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
    try:
        issue = _coerce_backlog_item_dict(issue, "upsert_issue")
    except ValueError as e:
        return {"status": "error", "message": str(e)}
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
    # GH issue #120: res["status"] used to be hardcoded "ok" and never
    # revisited, even though set_priority/update_roadmap below can each
    # independently fail (e.g. a typo'd title_or_id matching nothing) - the
    # caller saw a reported success while nothing had actually changed, and
    # specs/ROADMAP.md could end up pointing at content that was never
    # really created/updated. Now propagates the first sub-call failure
    # into the overall status/message instead.
    res = {"status": "ok", "updates": []}
    if priority:
        p_res = set_priority(title_or_id, priority, tool_context=tool_context)
        res["updates"].append({"type": "priority", "result": p_res})
        if p_res.get("status") == "error" and res["status"] == "ok":
            res["status"] = "error"
            res["message"] = p_res.get("message", "set_priority failed")
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
        if r_res.get("status") == "error" and res["status"] == "ok":
            res["status"] = "error"
            res["message"] = r_res.get("message", "update_roadmap failed")
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
                "and stage ownership, including 'Draft' - the first stage in the pipeline (GH issue "
                "#94), not a free-form label. Omit 'status' here entirely (a new item defaults to "
                "'Draft' automatically) and call advance_story_stage instead."
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

    _resort_backlog_by_priority(s)
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
    Update priority for a backlog item, then re-sort product_backlog by
    MoSCoW rank (GH issue #121) so the one-story-at-a-time ordering gate
    (_preceding_story, which reads backlog order as priority order) actually
    reflects the change - a story newly marked "Must" now really does jump
    ahead of the lower-priority stories still blocking it.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))
    for x in backlog:
        if x.get("id") == title_or_id or x.get("title") == title_or_id:
            x["priority"] = priority
            s["product_backlog"] = backlog
            _resort_backlog_by_priority(s)
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


def sync_architecture_vision_from_markdown(tool_context=None) -> Dict[str, Any]:
    """
    Mirrors sync_requirements_from_markdown above for the standing
    specs/architecture/ARCHITECTURE-VISION.md document (see
    upsert_architecture_vision, agents/scrum_team/tools/docs.py) - a single
    well-known file, not a set of PRD-*.md files to pick a "primary" from,
    so this just reads it directly instead of needing that function's
    newest-mtime selection logic.
    """
    repo_root = _configured_repo_root(tool_context)
    path = repo_root / "specs" / "architecture" / "ARCHITECTURE-VISION.md"
    if not path.exists():
        return {"status": "ok", "message": "No ARCHITECTURE-VISION.md found."}
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if content:
        tool_context.state["architecture_vision"] = content
    return {"status": "ok", "architecture_vision_updated": bool(content)}

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
    # deny_review's own recorded reason - surfaced here (not just left in
    # conversation text) so DevTeam actually sees it via read_doc. Cleared by
    # advance_story_stage once the story advances past the denied stage.
    review_denial = item.get("review_denial")
    if review_denial:
        notes += (
            f"\n- ⚠️ REVIEW DENIED at {review_denial.get('stage')} by "
            f"{review_denial.get('by')}: {review_denial.get('reason')}"
        )
    # raise_story_blocker's own recorded question - surfaced here the same
    # way as review_denial above, so a BLOCKED story's open question is
    # mechanically visible (read_doc), not just left in conversation text.
    # Cleared by resolve_story_blocker once answered.
    blocked = item.get("blocked")
    if blocked:
        notes += (
            f"\n- \U0001f6ab BLOCKED ({blocked.get('category')}) - raised by {blocked.get('raised_by')}: "
            f"{blocked.get('question')}"
        )

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


# Sentinel distinguishing "story isn't in product_backlog at all" from
# "story is in product_backlog, and is genuinely first" - _preceding_story
# used to return None for both cases, so a story that only exists in
# sprint_backlog (added via plan_sprint_backlog_item with no matching
# product_backlog entry - see GH issue #106) was silently treated as having
# no predecessor and could advance through every stage regardless of what
# higher-priority product_backlog story was still incomplete.
NOT_IN_PRODUCT_BACKLOG = object()


def _preceding_story(product_backlog: List[Dict[str, Any]], story_id: str, title: str):
    """
    The nearest non-BLOCKED User Story before story_id/title in
    product_backlog order - backlog order is priority order (see
    RELEASE.md "Story workflow"). Epics are skipped: they aren't advanced
    through the STORY_STAGES pipeline themselves, so they shouldn't block a
    real story behind them.

    A BLOCKED predecessor (see raise_story_blocker) is skipped too, not
    just Epics - a story stuck on an unresolved question shouldn't also
    freeze every lower-priority story behind it; the team is meant to move
    on to the next one while it waits (see RELEASE.md "Blocked stories").
    The blocked story itself stays exactly where it is in product_backlog -
    only the ordering *check* looks past it, so its priority position is
    preserved for whenever it's resolved.

    Returns NOT_IN_PRODUCT_BACKLOG (not None) if story_id/title isn't in
    product_backlog at all - callers must treat that as "ordering can't be
    verified", not as "no predecessor, safe to proceed".
    """
    stories_only = [x for x in product_backlog if x.get("type", "User Story") != "Epic"]
    idx = next(
        (i for i, x in enumerate(stories_only) if x.get("id") == story_id or x.get("title") == title),
        None,
    )
    if idx is None:
        return NOT_IN_PRODUCT_BACKLOG
    for j in range(idx - 1, -1, -1):
        if not stories_only[j].get("blocked"):
            return stories_only[j]
    return None


def _current_story_in_progress(product_backlog: List[Dict[str, Any]]):
    """
    The story "currently being worked" under one-story-at-a-time ordering:
    the first non-Epic, non-BLOCKED story in product_backlog order that
    hasn't reached Accepted yet. Used to attach a story to a loop-detection
    trip that has no story ID of its own (an unproductive transfer_to_agent
    ping-pong, unlike a stuck advance_story_stage retry which already names
    one in its own arguments) - see agent.py's _detect_transfer_loop.
    """
    for item in product_backlog:
        if item.get("type", "User Story") == "Epic":
            continue
        if item.get("blocked"):
            continue
        if "Accepted" not in (item.get("stages_completed") or []):
            return item
    return None


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
    Draft -> Ready -> Implemented -> Reviewed -> Tested -> Accepted pipeline
    (see RELEASE.md "Story workflow" and spec-templates/DOD.md, DOR.md).

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

    blocked = product_item.get("blocked") or sprint_item.get("blocked")
    if blocked:
        return {
            "status": "error",
            "message": (
                f"Cannot advance '{story_id}' - it is BLOCKED ({blocked.get('category')}): "
                f"{blocked.get('question')}. Call resolve_story_blocker('{story_id}', resolution) "
                "once this has been answered, then retry."
            ),
        }

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

    # Every non-Epic item must have a real product_backlog entry so its
    # priority position can be verified at all (GH issue #106) - a purely
    # data-integrity requirement, unlike the "must reach Accepted first"
    # ordering restriction below, so it applies at every stage, Draft/Ready
    # included.
    preceding = _preceding_story(product_backlog, story_id, title)
    if preceding is NOT_IN_PRODUCT_BACKLOG:
        return {
            "status": "error",
            "message": (
                f"Cannot advance '{story_id}' to {stage} - it isn't in product_backlog, so its "
                "priority order relative to other stories can't be verified (see GH issue #106). "
                "Add it via plan_backlog_item/upsert_story first."
            ),
        }
    # One-at-a-time only gates actual DEVELOPMENT (Implemented onward), not
    # backlog grooming (Draft/Ready) - see docs/ARCHITECTURE.md "a story
    # can't advance past READY until the story immediately above it has
    # reached ACCEPTED". Product Owner must be able to groom/ready many
    # stories ahead of whichever one story is actively in development
    # (a real eval run's feedback: "don't start implementing until we have
    # enough stories ready for ~2 sprints") - without this exemption, this
    # check (previously applied to every stage, including Draft) made it
    # impossible for a second story to even reach Draft before the first
    # was Accepted, which would make a Ready-backlog-sufficiency target
    # above 1 permanently unsatisfiable.
    if target_idx >= STORY_STAGES.index("Implemented"):
        if preceding is not None and "Accepted" not in _story_stages_completed(preceding, {}):
            return {
                "status": "error",
                "message": (
                    f"Cannot advance '{story_id}' to {stage} - the higher-priority story "
                    f"'{preceding.get('id') or preceding.get('title')}' must reach Accepted first. "
                    "Development happens one story at a time, top to bottom, in backlog priority "
                    "order - Draft/Ready grooming may run ahead of it."
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
    if stage == "Ready":
        # GH issue #94: at interaction levels where a story's mockup/design
        # must be cleared by stakeholder review before it's Ready, that
        # approval is tracked per-story (record_design_approval sets this
        # flag directly on the story) rather than via the shared,
        # per-sprint human_approvals list the Implemented/release gates
        # use below - one blanket approval doesn't stand in for having
        # actually reviewed THIS story's own design.
        if requires_pre_ready_design_approval() and not (
            product_item.get("design_approved") or sprint_item.get("design_approved")
        ):
            message = (
                f"Cannot mark '{story_id}' as Ready - this interaction level requires the story's "
                f"mockup/design to be cleared by stakeholder review first. Call "
                f"record_design_approval('{story_id}', ...) once it has been."
            )
            from .notifications import record_blocking_interaction
            record_blocking_interaction(
                "approval",
                f"Story '{story_id}' is waiting on design approval before it can be marked Ready.",
                detail=message,
                tool_context=tool_context,
            )
            return {"status": "error", "message": message}
    elif stage == "Implemented":
        # Belt-and-suspenders alongside start_feature_branch's own check
        # (agents/scrum_team/tools/github.py) - covers spike stories, which
        # skip start_feature_branch entirely since they have no code to
        # branch for, but must still not reach Implemented before this
        # sprint's specs have actually merged into develop.
        missing_msg = sprint_backlog_pr_missing(s)
        if missing_msg:
            return {"status": "error", "message": missing_msg}
        # Which approval type (if any) is required depends on the configured
        # INTERACTION_LEVEL (see docs/INTERACTION-LEVELS.md) - e.g. "budget"
        # instead of "sprint" at the CEO level, none at all for EVAL.
        required_approval = required_pre_implementation_approval()
        if required_approval:
            approvals = sum(1 for a in s.get("human_approvals", []) if a.get("type") == required_approval)
            if approvals <= s.get("sprint_approval_baseline", 0):
                message = (
                    f"Cannot mark a story Implemented - this interaction level requires a fresh "
                    f"'{required_approval}' human approval for this sprint - call "
                    f"record_human_approval('{required_approval}', ...) first (see "
                    "docs/INTERACTION-LEVELS.md)."
                )
                from .notifications import record_blocking_interaction
                record_blocking_interaction(
                    "approval",
                    f"Story '{story_id}' is waiting on a '{required_approval}' human approval before it can be marked Implemented.",
                    detail=message,
                    tool_context=tool_context,
                )
                return {"status": "error", "message": message}
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
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Implemented - no real source file has been written "
                    "via write_file since the last story was Implemented (story/spec markdown "
                    "doesn't count). Write the actual code, or set {'spike': true} on this backlog "
                    "item if it's a genuine planning/spike story with no code to write."
                ),
            })
        estimates = s.get("story_estimates", {}) or {}
        est_entry = estimates.get(story_id) or estimates.get(title) or estimates.get(title_or_id)
        if not isinstance(est_entry, dict) or est_entry.get("actual") is None:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Implemented - actual tokens spent haven't been "
                    "logged yet. Call log_story_tokens(title_or_id, actual_tokens) first."
                ),
            })
    elif stage == "Reviewed":
        pr_calls = s.get("pr_review_calls", {}) or {}
        architect_review_count = pr_calls.get("Architect", 0)
        if architect_review_count <= s.get("architect_review_baseline", 0):
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Reviewed - no gh_pr_review/gh_pr_comment call from "
                    "Architect has been recorded since the last story was Reviewed. Leave an actual "
                    "review comment on the PR first."
                ),
            })
        # ISSUE-0044: the check above is sprint-wide, not per-story - without
        # this, a story deny_review just denied could still advance right
        # after, as long as *some* Architect review call (even the very one
        # that led to the denial) already satisfied it. Require a review
        # call that's genuinely NEW since THIS story's own denial, not just
        # since the last story that reached Reviewed at all.
        denial = product_item.get("review_denial") or sprint_item.get("review_denial")
        if denial and denial.get("stage") == "Reviewed" and architect_review_count <= denial.get("review_count_at_denial", -1):
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Reviewed - it was denied: {denial.get('reason')}. "
                    "Leave a NEW gh_pr_review/gh_pr_comment after addressing that, then retry."
                ),
            })
    elif stage == "Tested":
        pr_calls = s.get("pr_review_calls", {}) or {}
        qa_review_count = pr_calls.get("QA", 0)
        if qa_review_count <= s.get("qa_review_baseline", 0):
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - no gh_pr_review/gh_pr_comment call from QA "
                    "has been recorded since the last story was Tested. Leave an actual review "
                    "comment on the PR first."
                ),
            })
        # ISSUE-0044: same per-story freshness requirement as Reviewed above.
        denial = product_item.get("review_denial") or sprint_item.get("review_denial")
        if denial and denial.get("stage") == "Tested" and qa_review_count <= denial.get("review_count_at_denial", -1):
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - it was denied: {denial.get('reason')}. "
                    "Leave a NEW gh_pr_review/gh_pr_comment after addressing that, then retry."
                ),
            })
        last_build = s.get("last_check_build")
        if not last_build:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": f"Cannot mark '{story_id}' Tested - check_build() hasn't been called yet. Call it first.",
            })
        if last_build.get("passing") is False:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - the last check_build() result failed. Fix "
                    "the build and call check_build() again until it passes before retrying."
                ),
            })
        # GH issue #114: check_build() only verifies the build/dependency
        # install, not that any tests actually ran or passed - this gate
        # previously accepted a clean build + a QA PR comment as sufficient
        # for "Tested", even for a story with a failing or completely empty
        # test suite. Run the real test suite now and require it to have
        # actually run something, with nothing failing.
        from .quality import _execute_test_suite_coverage
        coverage_result = _execute_test_suite_coverage(tool_context)
        tests_run = coverage_result.get("tests_run", 0)
        if tests_run <= 0:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - running the test suite found no tests "
                    f"actually ran ({coverage_result.get('note') or 'no coverage summary found'}). "
                    "A story can't be Tested with an empty or unrunnable test suite."
                ),
            })
        if not coverage_result.get("available"):
            # A real eval run hit this with tests_run > 0 (some tests did run,
            # possibly all passing) but the coverage summary itself couldn't be
            # parsed - wording this the same as the "no tests ran" case above
            # (GH issue #114's original message) sent agents chasing an empty
            # test suite that wasn't the actual problem, burning an entire
            # sprint's budget on unrelated tooling changes. State plainly that
            # tests did run, and surface the real pytest output so whatever's
            # actually wrong (harness-side coverage parsing, a real test
            # dependency issue, etc.) is at least visible instead of a black box.
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - {tests_run} test(s) ran but the coverage "
                    f"summary could not be parsed from pytest's output ({coverage_result.get('note') or 'no coverage summary found'}). "
                    "This may not be a problem with your test suite - check the output above before "
                    "changing test/CI configuration further; a human may need to look at this."
                ),
            })
        if coverage_result.get("tests_failed", 0) > 0:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Tested - {coverage_result['tests_failed']} of "
                    f"{coverage_result['tests_run']} tests failed. Fix the failing tests before "
                    "retrying."
                ),
            })
    elif stage == "Accepted":
        # ISSUE-0043: Accepted previously had no evidence gate at all - any
        # role could call advance_story_stage(id, "Accepted") on assertion
        # alone. Require record_acceptance_check to have actually run for
        # THIS story first.
        acceptance_count = product_item.get("acceptance_check_count") or sprint_item.get("acceptance_check_count") or 0
        if acceptance_count <= 0:
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Accepted - call record_acceptance_check(title_or_id, "
                    "note) first to record that the acceptance criteria were actually verified."
                ),
            })
        # ISSUE-0044: same per-story freshness requirement as Reviewed/Tested
        # above - a denial recorded here must be followed by a genuinely NEW
        # record_acceptance_check call, not just re-use of the check that led
        # to the denial.
        denial = product_item.get("review_denial") or sprint_item.get("review_denial")
        if denial and denial.get("stage") == "Accepted" and acceptance_count <= denial.get("acceptance_count_at_denial", -1):
            return _reject_stage_transition(tool_context, story_id, stage, {
                "status": "error",
                "message": (
                    f"Cannot mark '{story_id}' Accepted - it was denied: {denial.get('reason')}. "
                    "Call record_acceptance_check again after addressing that, then retry."
                ),
            })

    # The stage-content gate (if any) actually passed this time - clear this
    # (story, stage) pair's rejection streak so a later, genuinely fresh
    # attempt at some OTHER stage doesn't inherit a stale count.
    streaks = s.get("_stage_rejection_streaks")
    if streaks and f"{story_id}:{stage}" in streaks:
        streaks = dict(streaks)
        streaks.pop(f"{story_id}:{stage}", None)
        s["_stage_rejection_streaks"] = streaks

    stages_completed.add(stage)
    ordered_stages = sorted(stages_completed, key=STORY_STAGES.index)
    update = {"stages_completed": ordered_stages, "status": stage}

    # A resolved deny_review denial shouldn't linger as stale feedback once
    # the story actually advances past the stage it was denied at.
    existing_denial = product_item.get("review_denial") or sprint_item.get("review_denial")
    if existing_denial and existing_denial.get("stage") == stage:
        update["review_denial"] = None

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


def record_design_approval(title_or_id: str, note: str = "", tool_context=None) -> Dict[str, Any]:
    """
    Records that a human has reviewed and cleared this specific story's
    mockup/design (GH issue #94: "the designs are cleared by stakeholder
    review, then they are ready") - the mechanical counterpart
    advance_story_stage(..., "Ready") checks for (via
    requires_pre_ready_design_approval, agents/scrum_team/helpers.py) at
    interaction levels where that's required, instead of trusting the
    model's own assertion that a stakeholder looked at it.

    Deliberately per-story (a `design_approved` flag set directly on this
    one story, in both backlog copies), unlike record_human_approval's
    "sprint"/"release"/"budget" types - those are single approvals that
    cover every story for the rest of the sprint/release, but one blanket
    approval doesn't stand in for having actually reviewed each story's own
    design.

    At the Stakeholder level specifically, this now requires real evidence
    instead of a bare assertion: this story's own create_story_spec_pr
    (agents/scrum_team/tools/github.py) must have actually merged. A
    real eval run's feedback was that stakeholder approval should happen
    "by merge requests for the specific stories" - without this check,
    calling this tool was itself the only "approval" that ever happened,
    with no external artifact behind it at all.
    """
    from .scrum import save_state_to_repo
    from .github import story_spec_pr_merged

    s = tool_context.state
    sprint_backlog = list(s.get("sprint_backlog", []))
    product_backlog = list(s.get("product_backlog", []))
    sprint_idx = next((i for i, x in enumerate(sprint_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    product_idx = next((i for i, x in enumerate(product_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    if sprint_idx is None and product_idx is None:
        return {"status": "error", "message": f"No story found matching '{title_or_id}'."}

    story_id_for_evidence = (
        (product_backlog[product_idx].get("id") if product_idx is not None else None)
        or (sprint_backlog[sprint_idx].get("id") if sprint_idx is not None else None)
        or title_or_id
    )
    if requires_pre_ready_design_approval() and not story_spec_pr_merged(story_id_for_evidence, tool_context):
        return {
            "status": "error",
            "message": (
                f"Cannot record design approval for '{story_id_for_evidence}' - its "
                f"story-spec/{story_id_for_evidence} PR (see create_story_spec_pr) hasn't merged "
                "yet. Call create_story_spec_pr(title_or_id) first and get it reviewed/merged, "
                "then retry."
            ),
        }

    update = {"design_approved": True, "design_approval_note": note.strip()}
    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    story_id = (product_backlog[product_idx].get("id") if product_idx is not None
                else sprint_backlog[sprint_idx].get("id")) or title_or_id
    save_state_to_repo(tool_context)
    return {"status": "ok", "story_id": story_id, "design_approved": True}


def record_acceptance_check(title_or_id: str, note: str = "", tool_context=None) -> Dict[str, Any]:
    """
    Records that Product Owner has actually verified this story's acceptance
    criteria are met (docs/DEVELOPMENT-WORKFLOW.md "Verify" node) - the
    mechanical evidence advance_story_stage(..., "Accepted") now requires
    (ISSUE-0043), instead of trusting the model's own assertion that
    acceptance criteria were checked.

    Deliberately a per-story COUNTER (`acceptance_check_count`), not a
    one-time boolean like record_design_approval's `design_approved` -
    Accepted is deniable via deny_review, and ISSUE-0044's snapshot
    mechanism (a denial must be followed by a genuinely NEW signal, not just
    re-use of whatever satisfied the gate before) needs something that can
    grow past a snapshot taken at deny time. A boolean would already read
    "True" going into a re-check, indistinguishable from never having been
    reset.
    """
    from .scrum import save_state_to_repo

    s = tool_context.state
    sprint_backlog = list(s.get("sprint_backlog", []))
    product_backlog = list(s.get("product_backlog", []))
    sprint_idx = next((i for i, x in enumerate(sprint_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    product_idx = next((i for i, x in enumerate(product_backlog) if x.get("id") == title_or_id or x.get("title") == title_or_id), None)
    if sprint_idx is None and product_idx is None:
        return {"status": "error", "message": f"No story found matching '{title_or_id}'."}

    sprint_item = sprint_backlog[sprint_idx] if sprint_idx is not None else {}
    product_item = product_backlog[product_idx] if product_idx is not None else {}
    prior_count = product_item.get("acceptance_check_count") or sprint_item.get("acceptance_check_count") or 0
    new_count = prior_count + 1
    update = {"acceptance_check_count": new_count, "acceptance_check_note": note.strip()}
    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    story_id = (product_backlog[product_idx].get("id") if product_idx is not None
                else sprint_backlog[sprint_idx].get("id")) or title_or_id
    save_state_to_repo(tool_context)
    return {"status": "ok", "story_id": story_id, "acceptance_check_count": new_count}


def deny_review(title_or_id: str, stage: str, reason: str, tool_context=None) -> Dict[str, Any]:
    """
    Denies one of the three review-gated stage transitions - Reviewed
    (Architect's code review), Tested (QA), Accepted (Product Owner's
    acceptance check) - instead of silently just not calling
    advance_story_stage (see docs/DEVELOPMENT-WORKFLOW.md "Story stage
    pipeline"). Previously there was no mechanical difference at all
    between "I haven't gotten to this review yet" and "I reviewed it and
    it's not good enough" - both just meant the stage-advancing tool call
    never happened, so a rejection's reason (if the model even wrote one)
    only ever existed in free-text conversation, with no code-level
    guarantee DevTeam ever saw it or that it said anything concrete.

    A denial is refused unless `reason` is real, specific text (see
    _is_concrete_denial_reason) - not empty, a template placeholder, or a
    generic restatement of the verdict itself ("not good", "denied", ...).
    The accepted reason is written directly onto the story's own record
    (both backlog copies) and re-rendered into its Markdown file's Notes
    section (`_update_story_markdown`), which DevTeam already has `read_doc`
    to read - so a denial is mechanically visible and actionable, not just
    something said once in a conversation turn. Cleared automatically once
    the story actually advances past the stage it was denied at (see
    advance_story_stage) - a resolved denial shouldn't linger as stale
    feedback forever.

    For Reviewed/Tested specifically (ISSUE-0044), this also snapshots the
    denying role's current `pr_review_calls` count - advance_story_stage's
    own gate for those two stages requires the count to have grown past
    THIS snapshot, not just past the sprint-wide baseline, before the SAME
    story can complete that stage again. Without this, a denial had no
    teeth: that sprint-wide counter (shared across every story, not scoped
    to this one) could already be satisfied by the very review call that
    prompted the denial, letting the stage complete right away regardless.
    Accepted has its own per-story counter instead (ISSUE-0043's
    `record_acceptance_check` / `acceptance_check_count`, since Product
    Owner doesn't leave PR reviews) - this snapshots that count instead, so
    a denied acceptance check requires a genuinely new
    record_acceptance_check call before Accepted can complete again.
    """
    from .scrum import save_state_to_repo

    if stage not in _DENIABLE_REVIEW_STAGES:
        return {
            "status": "error",
            "message": f"deny_review only applies to {list(_DENIABLE_REVIEW_STAGES)}, not '{stage}'.",
        }

    agent_name = getattr(tool_context, "agent_name", None)
    expected_owner = STAGE_OWNERS[stage]
    if agent_name and agent_name != expected_owner:
        return {
            "status": "error",
            "message": f"Stage '{stage}' can only be denied by {expected_owner}, not {agent_name}.",
        }

    if not _is_concrete_denial_reason(reason):
        return {
            "status": "error",
            "message": (
                f"reason must be a concrete, actionable explanation (at least "
                f"{_MIN_DENIAL_REASON_LENGTH} characters, not a placeholder or a generic phrase like "
                "'not good'/'denied') - state specifically what's wrong and what would need to change, "
                "so DevTeam can actually act on it."
            ),
        }

    s = tool_context.state
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

    review_denial = {"stage": stage, "reason": reason.strip(), "by": agent_name or expected_owner}
    counter_key = {"Reviewed": "Architect", "Tested": "QA"}.get(stage)
    if counter_key:
        review_denial["review_count_at_denial"] = (s.get("pr_review_calls", {}) or {}).get(counter_key, 0)
    elif stage == "Accepted":
        review_denial["acceptance_count_at_denial"] = (
            product_item.get("acceptance_check_count") or sprint_item.get("acceptance_check_count") or 0
        )
    update = {"review_denial": review_denial}
    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    save_state_to_repo(tool_context)

    merged_item = {**product_item, **sprint_item, **update, "id": story_id, "title": title}
    story_md_result = _update_story_markdown(merged_item, tool_context)

    return {
        "status": "ok",
        "story_id": story_id,
        "stage": stage,
        "reason": reason.strip(),
        "message": (
            f"Denial recorded for '{story_id}' at {stage} - see its story file "
            "(read_doc) for DevTeam's next steps."
        ),
        "story_markdown": story_md_result,
    }


def raise_story_blocker(title_or_id: str, question: str, category: str, tool_context=None) -> Dict[str, Any]:
    """
    Marks a story BLOCKED - orthogonal to STORY_STAGES (can happen from any
    stage, not just a fixed point in the pipeline), for when the team
    genuinely can't proceed: a real open question nobody on the team can
    answer, or a mechanical loop-detection trip (see agent.py's
    _detect_transfer_loop/_detect_repeated_call_loop, which call this same
    function once a stuck story can be identified). advance_story_stage
    refuses every further call for this story while `blocked` is set.

    `category` is "technical" (routed to Architect) or "product" (routed to
    Product Owner - or, at the "Product" interaction level, escalated
    straight to the human User instead, since that human already IS the
    acting product owner day-to-day - see should_escalate_blocker_to_user,
    agents/scrum_team/helpers.py). Any role may call this - whoever
    recognizes the team is stuck first, not just whoever will resolve it.

    `question` must be real, specific text (reuses _is_concrete_denial_reason's
    placeholder/genericity checks - the same "must be processable, not just a
    verdict" philosophy applies to a blocker's open question as much as to a
    denial's reason). Always raises a blocking_interaction too (kind
    "blocked_story"), so a human reviewing list_blocking_interactions/the
    console log sees it exactly like any other "absolutely necessary human
    feedback" moment (GH issue #53), even before create_sprint_report's own
    "Open Questions for Stakeholder" section surfaces it at sprint close.
    """
    from .scrum import save_state_to_repo
    from .notifications import record_blocking_interaction

    if category not in BLOCKER_CATEGORIES:
        return {
            "status": "error",
            "message": f"category must be one of {list(BLOCKER_CATEGORIES)}, not '{category}'.",
        }

    if not _is_concrete_denial_reason(question):
        return {
            "status": "error",
            "message": (
                f"question must be a concrete, actionable question (at least "
                f"{_MIN_DENIAL_REASON_LENGTH} characters, not a placeholder or a generic phrase) - "
                "state specifically what's blocking progress and what answer would unblock it."
            ),
        }

    s = tool_context.state
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

    if product_item.get("blocked") or sprint_item.get("blocked"):
        return {
            "status": "error",
            "message": f"'{story_id}' is already BLOCKED - call resolve_story_blocker first if this has been answered.",
        }

    agent_name = getattr(tool_context, "agent_name", None)
    escalate = should_escalate_blocker_to_user(category)
    interaction_result = record_blocking_interaction(
        "blocked_story",
        f"'{story_id}' is BLOCKED ({category}): {question.strip()}",
        detail=(
            f"Raised by {agent_name or 'unknown'}. Resolve via "
            f"{BLOCKER_CATEGORY_OWNERS[category]}'s resolve_story_blocker('{story_id}', resolution)"
            + (" once the human User has answered (see resolve_blocking_interaction)." if escalate else ".")
        ),
        tool_context=tool_context,
    )

    blocked = {
        "question": question.strip(),
        "category": category,
        "raised_by": agent_name or "unknown",
        "raised_at_stage": product_item.get("status") or sprint_item.get("status"),
        "escalated_to_user": escalate,
        "blocking_interaction_id": (interaction_result.get("interaction") or {}).get("id"),
    }
    update = {"blocked": blocked}
    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    save_state_to_repo(tool_context)

    merged_item = {**product_item, **sprint_item, **update, "id": story_id, "title": title}
    story_md_result = _update_story_markdown(merged_item, tool_context)

    resolver = BLOCKER_CATEGORY_OWNERS[category]
    return {
        "status": "ok",
        "story_id": story_id,
        "blocked": blocked,
        "message": (
            f"'{story_id}' is now BLOCKED. "
            + (
                f"At the Product interaction level, this waits on the human User to answer - see "
                "list_blocking_interactions/resolve_blocking_interaction - before Product Owner can "
                "call resolve_story_blocker."
                if escalate else
                f"{resolver} should attempt to resolve it (resolve_story_blocker) - if it can't be "
                "resolved this sprint, the team moves on to the next story (see "
                "_preceding_story/create_sprint_report's 'Open Questions for Stakeholder')."
            )
        ),
        "story_markdown": story_md_result,
    }


def resolve_story_blocker(title_or_id: str, resolution: str, tool_context=None) -> Dict[str, Any]:
    """
    Clears a story's BLOCKED state (see raise_story_blocker) once an answer
    has actually been found, unblocking advance_story_stage for it again.
    Callable only by the category's owning role (Architect for "technical",
    Product Owner for "product") - mirrors deny_review being resolved by a
    fresh action from the role whose judgment the stage belongs to.

    At the "Product" interaction level, a "product"-category blocker was
    escalated straight to the human User when raised (see
    should_escalate_blocker_to_user) - this refuses Product Owner's own
    resolution until the linked blocking_interaction has actually been
    resolved by that human first (resolve_blocking_interaction), so the
    human's answer is a real precondition, not just a courtesy notification
    Product Owner could route around.

    `resolution` must be real, specific text (same _is_concrete_denial_reason
    check as the question/reason on the other side of this mechanism) - what
    the actual answer was, not just "fixed" or "resolved".
    """
    from .scrum import save_state_to_repo
    from .notifications import resolve_blocking_interaction

    s = tool_context.state
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

    blocked = product_item.get("blocked") or sprint_item.get("blocked")
    if not blocked:
        return {"status": "error", "message": f"'{story_id}' is not currently BLOCKED."}

    category = blocked.get("category")
    expected_resolver = BLOCKER_CATEGORY_OWNERS.get(category)
    agent_name = getattr(tool_context, "agent_name", None)
    if agent_name and expected_resolver and agent_name != expected_resolver:
        return {
            "status": "error",
            "message": f"A '{category}' blocker can only be resolved by {expected_resolver}, not {agent_name}.",
        }

    if blocked.get("escalated_to_user"):
        interactions = s.get("blocking_interactions", []) or []
        interaction = next((i for i in interactions if i.get("id") == blocked.get("blocking_interaction_id")), None)
        if interaction and not interaction.get("resolved"):
            return {
                "status": "error",
                "message": (
                    f"'{story_id}' was escalated to the human User (Product interaction level) - "
                    f"waiting on blocking_interaction #{interaction['id']} to be resolved "
                    "(resolve_blocking_interaction) before Product Owner can clear this here."
                ),
            }

    if not _is_concrete_denial_reason(resolution):
        return {
            "status": "error",
            "message": (
                f"resolution must be a concrete, actionable answer (at least "
                f"{_MIN_DENIAL_REASON_LENGTH} characters, not a placeholder or a generic phrase) - "
                "state what was actually decided/found, not just that it's 'resolved'."
            ),
        }

    if blocked.get("blocking_interaction_id") is not None:
        resolve_blocking_interaction(blocked["blocking_interaction_id"], tool_context=tool_context)

    update = {"blocked": None}
    if sprint_idx is not None:
        sprint_backlog[sprint_idx] = {**sprint_backlog[sprint_idx], **update}
        s["sprint_backlog"] = sprint_backlog
    if product_idx is not None:
        product_backlog[product_idx] = {**product_backlog[product_idx], **update}
        s["product_backlog"] = product_backlog

    save_state_to_repo(tool_context)

    merged_item = {**product_item, **sprint_item, **update, "id": story_id, "title": title}
    story_md_result = _update_story_markdown(merged_item, tool_context)

    return {
        "status": "ok",
        "story_id": story_id,
        "resolution": resolution.strip(),
        "message": f"'{story_id}' is no longer BLOCKED - advance_story_stage may be called for it again.",
        "story_markdown": story_md_result,
    }
