# agents/scrum_team/helpers.py
import os

def get_process_overhead_percentage() -> float:
    """Gets the process overhead percentage from environment variables."""
    return float(os.getenv("PROCESS_OVERHEAD_PERCENTAGE", "10.0"))

# Backlog item status values that count as "finished" for progress tracking
# (sprint_status_injection_callback, sprint-length feedback, etc). Case-
# insensitive: templates/prompts document "Done" (capitalized, see
# TEMPLATE-USER-STORY.md), but story/task status is free-form text set by
# the LLM, not a validated enum - matching only the lowercase "done" (as one
# caller previously did) silently undercounts every story actually marked
# "Done" the documented way. "accepted" covers the current 5-stage pipeline
# below (Accepted is its terminal stage); "done"/"completed"/"closed" are
# kept for stories/tests predating that pipeline.
_DONE_STATUSES = {"done", "completed", "closed", "accepted"}


def is_story_done(status) -> bool:
    return isinstance(status, str) and status.strip().lower() in _DONE_STATUSES


# The mandatory, ordered story pipeline (see RELEASE.md "Story workflow" /
# spec-templates/DOD.md, DOR.md) - every story must pass through each stage
# in this exact order, no skipping. STAGE_OWNERS names the one internal
# agent name (see agents/scrum_team/agent.py's LlmAgent `name=` values)
# allowed to complete each stage via advance_story_stage
# (agents/scrum_team/tools/requirements.py).
STORY_STAGES = ["Ready", "Implemented", "Reviewed", "Tested", "Accepted"]

STAGE_OWNERS = {
    "Ready": "ProductOwner",
    "Implemented": "DevTeam",
    "Reviewed": "Architect",
    "Tested": "QA",
    "Accepted": "ProductOwner",
}

_STAGE_NAMES_LOWER = {stage.lower() for stage in STORY_STAGES}


def is_pipeline_stage_name(status) -> bool:
    """True if status names one of STORY_STAGES (case-insensitively)."""
    return isinstance(status, str) and status.strip().lower() in _STAGE_NAMES_LOWER


def blocks_direct_status_set(status) -> bool:
    """
    True if `status` would let a caller fake a story's pipeline progress by
    setting it directly, rather than through advance_story_stage's
    ordering/ownership enforcement. Used by upsert_story/upsert_epic/
    plan_sprint_backlog_item to refuse it outright. Covers two escape
    hatches, not just one:
    - The 5 stage names themselves (`upsert_story({"status": "Accepted"})`
      would set a story straight to Accepted with none of
      advance_story_stage's enforcement applying at all).
    - The legacy is_story_done synonyms ("Done"/"completed"/"closed") -
      _story_stages_completed's read-side backward-compat treats any of
      these as *every* stage complete (for repos/data older than this
      pipeline), so setting one directly is an equally complete bypass,
      just spelled differently. Reading old data this way is still fine;
      writing new data this way is not.
    """
    return is_pipeline_stage_name(status) or is_story_done(status)


# Paths under these prefixes are spec/process documents, not application
# source code - see ISSUE-0002 (advance_story_stage's "Implemented" gate
# needs to tell "wrote real code" apart from "wrote a story markdown file").
_NON_SOURCE_PREFIXES = ("specs/", "spec-templates/", ".hc/")


def is_source_file(rel_path: str) -> bool:
    return isinstance(rel_path, str) and not rel_path.startswith(_NON_SOURCE_PREFIXES)


# Placeholder/generic retro content the model can produce to trivially
# satisfy create_sprint_report's retro_baseline gate (which only checks
# *count*, not quality) without actually doing the concrete reflection
# SM_PROMPT's RETROSPECTIVE REASONING section asks for - see ISSUE-0009.
_MIN_RETRO_FIELD_LEN = 8
_GENERIC_RETRO_PHRASES = {
    "communicate better", "improve communication", "do better", "be more careful",
    "work harder", "n/a", "none", "tbd", "todo", "stuff", "improve process",
}


def is_low_quality_retro_text(text) -> bool:
    """True if text is blank, a known generic placeholder, or too short to be a concrete reflection."""
    if not isinstance(text, str):
        return True
    cleaned = text.strip().lower().rstrip(".")
    return len(cleaned) < _MIN_RETRO_FIELD_LEN or cleaned in _GENERIC_RETRO_PHRASES


def new_sprint_item_blocked(state: dict) -> str | None:
    """
    Returns a rejection message if a previous sprint's close sequence was
    left incomplete, else None. "Incomplete" here means
    `sprint_report_pending_release` is set (create_sprint_report succeeded -
    so the retro/report step did happen, see retro_baseline - but
    create_release_pr never followed) and that prior sprint still has
    planned stories short of Accepted. See ISSUE-0010.

    Only meant to gate genuinely *new* sprint_backlog items - an ongoing
    sprint planning several stories before any of them reach Accepted is
    normal, not a skipped close sequence, so callers must only apply this
    to an item that isn't already in sprint_backlog.
    """
    if not state.get("sprint_report_pending_release"):
        return None
    unfinished = [
        x for x in (state.get("sprint_backlog") or [])
        if x.get("type", "User Story") != "Epic" and "Accepted" not in (x.get("stages_completed") or [])
    ]
    if not unfinished:
        return None
    unfinished_ids = [x.get("id") or x.get("title") for x in unfinished]
    return (
        "Cannot plan new sprint work - the previous sprint's retrospective/report was completed "
        "but create_release_pr was never called (or didn't succeed) for it, and it still has "
        f"stories short of Accepted ({unfinished_ids}). Finish the previous sprint's release "
        "(create_release_pr) before starting new work - see ORCHESTRATOR_PROMPT SPRINT CLOSE "
        "SEQUENCE."
    )
