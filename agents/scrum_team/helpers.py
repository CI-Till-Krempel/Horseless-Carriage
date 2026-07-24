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
