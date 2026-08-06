# agents/scrum_team/helpers.py
from __future__ import annotations
import os
import sys

def get_process_overhead_percentage() -> float:
    """Gets the process overhead percentage from environment variables."""
    return float(os.getenv("PROCESS_OVERHEAD_PERCENTAGE", "10.0"))


# --- Budget env var naming (GH issue #81) ---
# TOTAL_USD_BUDGET replaces SPRINT_USD_BUDGET as the canonical name for the
# whole-engagement, never-reset-per-sprint USD ceiling - the old name looked
# like a per-sprint value (same "SPRINT_" prefix as the genuinely-per-sprint
# SPRINT_TOKEN_BUDGET, which *does* reset every sprint via reset_sprint_budget),
# but actually behaves as a cumulative cap for the entire engagement (see
# BUDGET.md, reset_sprint_budget's docstring in tools/budget.py). Read via
# get_env_with_deprecated_fallback so an existing .env using the old name
# keeps working exactly as before - a silent drop here would fall back to
# this module's own hardcoded default, which could be a *higher* ceiling
# than what someone deliberately configured under the old name (issue #81:
# "make sure there is no scenario that can cause unexpected cloud costs").
_deprecated_env_vars_warned: set = set()


def get_env_with_deprecated_fallback(new_name: str, old_name: str) -> str | None:
    """
    Reads `new_name` from the environment; if unset/empty, falls back to
    `old_name` (printing a one-time-per-process deprecation warning to
    stderr) so a renamed env var never silently reverts to a hardcoded
    default just because an existing .env still uses the old key. Returns
    None if neither is set.
    """
    value = os.environ.get(new_name)
    if value:
        return value
    old_value = os.environ.get(old_name)
    if old_value:
        if old_name not in _deprecated_env_vars_warned:
            print(
                f"WARNING: {old_name} is deprecated - please rename it to {new_name} in your .env. "
                f"Using its value for now.",
                file=sys.stderr,
            )
            _deprecated_env_vars_warned.add(old_name)
        return old_value
    return None


# --- Human interaction levels (see docs/INTERACTION-LEVELS.md) ---
# Controls which of the three human-approval types (sprint/release/budget -
# see record_human_approval, agents/scrum_team/tools/scrum.py) is actually
# required, mechanically, before the team may implement stories or release
# an increment. Configured via the INTERACTION_LEVEL environment variable
# (see .env.example) - there is deliberately no state field for this: it's
# read fresh from the environment wherever it's needed (same pattern as
# get_process_overhead_percentage above), so it can't drift from what's
# actually configured for the running process.
INTERACTION_LEVELS = ("Product", "Stakeholder", "CEO", "EVAL")
_DEFAULT_INTERACTION_LEVEL = "Product"

# Which record_human_approval(approval_type, ...) must have a fresh entry
# (see the sprint_approval_baseline/release_approval_baseline "must be NEW"
# pattern already used elsewhere) before advance_story_stage(...,
# "Implemented") / create_release_pr may proceed, at each interaction level.
# None means "not required at this level - the team proceeds on its own
# judgment," not "any approval type satisfies it."
_PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL = {
    "Product": "sprint",
    "Stakeholder": "sprint",
    "CEO": "budget",
    "EVAL": None,
}
_PRE_RELEASE_APPROVAL_BY_LEVEL = {
    "Product": "release",
    "Stakeholder": "release",
    "CEO": None,
    "EVAL": None,
}

# Whether advance_story_stage(..., "Ready") requires the story's mockup/
# design to have been cleared via record_design_approval first (GH issue
# #94: "the designs are cleared by stakeholder review, then they are
# ready"), per interaction level. Unlike the sprint/release approvals above
# (one shared approval unlocks every story for the rest of the sprint),
# this is tracked per-story (see record_design_approval,
# agents/scrum_team/tools/requirements.py) - each story's own design needs
# its own sign-off, not one blanket approval for the whole backlog.
# - Product: not required - this human IS the Product Owner day-to-day, so
#   the Draft-stage conversation itself already is the review.
# - Stakeholder: required - this is exactly the "designs cleared by
#   stakeholder review" case the issue describes.
# - CEO: not required - a CEO-level human approves budget, not per-story
#   design.
# - EVAL: not required - fully autonomous, no human to review anything.
_PRE_READY_DESIGN_APPROVAL_REQUIRED_LEVELS = {"Stakeholder"}


def get_interaction_level() -> str:
    """
    Reads INTERACTION_LEVEL from the environment (case-insensitive),
    falling back to "Product" - the most-supervised level - if unset or not
    one of INTERACTION_LEVELS, rather than silently disabling every
    human-approval gate on a typo'd value.
    """
    raw = (os.getenv("INTERACTION_LEVEL") or "").strip()
    for level in INTERACTION_LEVELS:
        if raw.lower() == level.lower():
            return level
    return _DEFAULT_INTERACTION_LEVEL


def required_pre_implementation_approval(level: str | None = None) -> str | None:
    """approval_type that must be freshly recorded before a story may reach Implemented at this interaction level (see docs/INTERACTION-LEVELS.md), or None if this level requires none."""
    return _PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL.get(level or get_interaction_level())


def required_pre_release_approval(level: str | None = None) -> str | None:
    """Same as required_pre_implementation_approval, for create_release_pr."""
    return _PRE_RELEASE_APPROVAL_BY_LEVEL.get(level or get_interaction_level())


def requires_pre_ready_design_approval(level: str | None = None) -> bool:
    """Whether advance_story_stage(..., "Ready") requires a fresh, per-story
    record_design_approval call at this interaction level - see
    _PRE_READY_DESIGN_APPROVAL_REQUIRED_LEVELS."""
    return (level or get_interaction_level()) in _PRE_READY_DESIGN_APPROVAL_REQUIRED_LEVELS


# How much detail create_sprint_report actually renders for the human at
# each interaction level (see docs/INTERACTION-LEVELS.md) - distinct from
# the approval-gate mappings above: a Stakeholder/CEO human still needs the
# team's retrospective to have genuinely happened (create_sprint_report's
# retro_baseline gate applies at every level, unconditionally), they just
# don't need every internal/technical detail rendered in the report they
# personally read.
# - "full": everything - per-agent token usage, retro/impediment detail,
#   story-level estimates, full transcript excerpts. For Product (embedded
#   day-to-day) and EVAL (the report is analyzed by tooling afterwards, not
#   read by a human at all - trimming it would only lose signal).
# - "business": drops internal/technical numbers (per-agent token usage,
#   transcript excerpts) a business stakeholder has no use for, keeps
#   everything about what was delivered and how the process went.
# - "executive": budget and headline outcomes only - a CEO approves spend,
#   not process detail; everything else is one line pointing at where the
#   full detail still lives (specs/reports/), never silently discarded.
_REPORT_DETAIL_LEVEL_BY_LEVEL = {
    "Product": "full",
    "Stakeholder": "business",
    "CEO": "executive",
    "EVAL": "full",
}


def report_detail_level(level: str | None = None) -> str:
    """"full" | "business" | "executive" - see _REPORT_DETAIL_LEVEL_BY_LEVEL."""
    return _REPORT_DETAIL_LEVEL_BY_LEVEL.get(level or get_interaction_level(), "full")

# Backlog item status values that count as "finished" for progress tracking
# (sprint_status_injection_callback, sprint-length feedback, etc). Case-
# insensitive: templates/prompts document "Done" (capitalized, see
# TEMPLATE-USER-STORY.md), but story/task status is free-form text set by
# the LLM, not a validated enum - matching only the lowercase "done" (as one
# caller previously did) silently undercounts every story actually marked
# "Done" the documented way. "accepted" covers the current STORY_STAGES
# pipeline below (Accepted is its terminal stage); "done"/"completed"/
# "closed" are kept for stories/tests predating that pipeline.
_DONE_STATUSES = {"done", "completed", "closed", "accepted"}


def is_story_done(status) -> bool:
    return isinstance(status, str) and status.strip().lower() in _DONE_STATUSES


# The mandatory, ordered story pipeline (see RELEASE.md "Story workflow" /
# spec-templates/DOD.md, DOR.md) - every story must pass through each stage
# in this exact order, no skipping. STAGE_OWNERS names the one internal
# agent name (see agents/scrum_team/agent.py's LlmAgent `name=` values)
# allowed to complete each stage via advance_story_stage
# (agents/scrum_team/tools/requirements.py).
#
# "Draft" (GH issue #94) is the first, real, code-enforced stage rather
# than just the inert default label a freshly-created story's free-form
# `status` field happened to show before this - the Product Owner
# completes it once a story concept/mockup exists worth shaping into a
# real backlog item (collaborating with Architect on technical feasibility,
# same as it already does for Ready - dedicated UX Lead/Business Analyst
# roles are a larger follow-up, not part of this pipeline yet). Moving on
# to "Ready" additionally requires the design to be cleared by
# record_design_approval first, at interaction levels where that's required
# (see requires_pre_ready_design_approval below).
STORY_STAGES = ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]

STAGE_OWNERS = {
    "Draft": "ProductOwner",
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
    - The STORY_STAGES names themselves (`upsert_story({"status": "Accepted"})`
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


# --- BLOCKED stories (agents stuck on an unresolved question or loop) ---
# raise_story_blocker/resolve_story_blocker (agents/scrum_team/tools/
# requirements.py) mark a story BLOCKED - orthogonal to STORY_STAGES, since
# it can happen from any stage, not just a fixed point in the pipeline.
# "category" decides who's asked to clarify: a technical question goes to
# Architect, a product/business question goes to Product Owner - or, at the
# "Product" interaction level, straight to the human User instead, since
# that human already IS the acting product owner day-to-day (same reasoning
# as requires_pre_ready_design_approval's "Product: not required - this
# human IS the Product Owner" note above). Technical questions always go to
# Architect regardless of level - there's no "human Architect" role at any
# level.
BLOCKER_CATEGORIES = ("technical", "product")

BLOCKER_CATEGORY_OWNERS = {"technical": "Architect", "product": "ProductOwner"}

# Which internal agent names raising a blocker (via a loop-detection trip -
# see agent.py's _detect_transfer_loop/_detect_repeated_call_loop) implies a
# *technical* question rather than a product one - the roles that only ever
# get stuck on implementation/architecture/test concerns, never on
# priority/scope/acceptance ones.
_TECHNICAL_BLOCKER_ROLES = {"DevTeam", "Architect", "QA"}


def infer_blocker_category(*agent_names: str) -> str:
    """
    "technical" if any given agent name is one of the roles that only ever
    gets stuck on implementation/architecture/test concerns
    (_TECHNICAL_BLOCKER_ROLES); "product" otherwise (ProductOwner,
    ScrumMaster, or unknown). Used by the loop-breakers in agent.py, which
    only have agent names in scope, not the actual content of what's stuck -
    a best-effort default category, not a substitute for a role explicitly
    calling raise_story_blocker with the category it actually means.
    """
    return "technical" if any(name in _TECHNICAL_BLOCKER_ROLES for name in agent_names) else "product"


def should_escalate_blocker_to_user(category: str, level: str | None = None) -> bool:
    """
    Whether a BLOCKED story's category should go straight to the human User
    instead of being routed to Product Owner/Architect in-conversation -
    true only for a "product"-category blocker at the "Product" interaction
    level (see the module comment above). Technical questions always stay
    with Architect, at every level - there's no human role standing in for
    Architect the way Product's human stands in for Product Owner.
    """
    return category == "product" and (level or get_interaction_level()) == "Product"


def sprint_backlog_pr_missing(state: dict) -> str | None:
    """
    Returns a rejection message if this sprint's Sprint Backlog PR
    (create_sprint_backlog_pr, agents/scrum_team/tools/github.py) hasn't
    successfully merged yet, else None. A real eval run showed why this
    needs to be a mechanical gate rather than PO_PROMPT's "call this BEFORE
    Dev Team opens the first feature branch" instruction alone: nothing
    stopped Dev Team from starting (or even finishing) a story before that
    PR ever ran, so when the sprint's token budget was cut mid-story,
    everything Product Owner had written that sprint (roadmap, PRD, epics,
    stories) was left uncommitted anywhere reachable - the state repo
    ended up with no specs at all.

    `create_sprint_backlog_pr` sets `sprint_backlog_pr_sprint` to the
    current `sprint_number` only once its merge actually succeeds; this
    compares that against the *current* `sprint_number` (not just "was it
    ever set") so a stale success from a previous sprint can't silently
    satisfy this one.
    """
    sprint_number = state.get("sprint_number", 0)
    if sprint_number <= 0:
        return "No sprint has been started yet (sprint_number is unset) - ask Scrum Master to call start_sprint(goal) first."
    if state.get("sprint_backlog_pr_sprint") != sprint_number:
        return (
            f"Cannot start implementation work - this sprint's (#{sprint_number}) Sprint Backlog PR "
            "hasn't merged yet. Product Owner must call create_sprint_backlog_pr() to publish this "
            "sprint's planning output (roadmap, PRD, epics, stories reaching Ready) to develop before "
            "any story can be implemented - see PO_PROMPT SPRINT PLANNING."
        )
    return None


# --- Ready-backlog sufficiency (don't start implementing until there's
# enough Ready work queued up) ---
# No velocity/story-points system exists in this codebase - story_estimates
# is token-bookkeeping for the sprint report (estimate vs actual per
# story), not a forward capacity signal. Rather than build a velocity
# system, ready_backlog_shortfall below uses a simple, documented,
# configurable story-COUNT proxy for "enough work queued up for N sprints"
# - see create_sprint_backlog_pr (agents/scrum_team/tools/github.py), the
# single choke point this gates.
def target_stories_per_sprint() -> int:
    """How many stories a sprint is assumed to get through, for sizing the
    Ready-backlog sufficiency target below. Configurable via
    TARGET_STORIES_PER_SPRINT; defaults to 3 - a deliberately simple,
    round-number assumption, not a measured velocity."""
    try:
        return max(1, int(os.getenv("TARGET_STORIES_PER_SPRINT", "3")))
    except (ValueError, TypeError):
        return 3


def ready_backlog_sprints_target() -> int:
    """How many sprints' worth of Ready work the backlog should hold before
    Dev Team may start implementing (see create_sprint_backlog_pr).
    Configurable via READY_BACKLOG_SPRINTS_TARGET; defaults to 2."""
    try:
        return max(1, int(os.getenv("READY_BACKLOG_SPRINTS_TARGET", "2")))
    except (ValueError, TypeError):
        return 2


def ready_backlog_shortfall(product_backlog: list) -> int:
    """
    How many more stories must reach Ready before the backlog holds
    target_stories_per_sprint() * ready_backlog_sprints_target() stories
    that are Ready-or-further but not yet Accepted (0 if already met).
    Counts non-Epic, non-BLOCKED items only - the same population
    _preceding_story's one-story-at-a-time ordering check considers "real"
    backlog work (agents/scrum_team/tools/requirements.py).
    """
    ready_count = sum(
        1 for x in (product_backlog or [])
        if x.get("type", "User Story") != "Epic"
        and not x.get("blocked")
        and "Ready" in (x.get("stages_completed") or [])
        and "Accepted" not in (x.get("stages_completed") or [])
    )
    target = target_stories_per_sprint() * ready_backlog_sprints_target()
    return max(0, target - ready_count)


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
