# agents/scrum_team/helpers.py
from __future__ import annotations
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

def get_process_overhead_percentage() -> float:
    """Gets the process overhead percentage from environment variables."""
    return float(os.getenv("PROCESS_OVERHEAD_PERCENTAGE", "10.0"))


def ritual_token_budget(token_limit: int) -> float:
    """
    GH #395: ScrumMaster's own budget for ritual work - facilitation, retro,
    KPI calculation, authoring the sprint report - sized as
    get_process_overhead_percentage() percent of the sprint's main token
    budget. Checked (agent.py's check_cost_budget_callback) against
    ScrumMaster's own token_usage.agents entry, never against the shared
    total DevTeam/QA/Architect/ProductOwner spend down - see
    main_budget_token_usage below for the other half of that separation.
    Previously PROCESS_OVERHEAD_PERCENTAGE was purely cosmetic (only ever
    printed in the sprint report/cost breakdown, never actually enforced) -
    a real eval run showed why that wasn't enough: a verbose main sprint
    could exhaust the shared budget before ScrumMaster ever got a turn to
    run the retrospective or KPIs at all, and create_sprint_report
    "repeatedly fails" across runs as a direct result.
    """
    if token_limit <= 0:
        return 0.0
    return token_limit * (get_process_overhead_percentage() / 100.0)


def main_budget_token_usage(state) -> int:
    """
    GH #395: the token usage the main per-sprint ceiling (DevTeam/QA/
    Architect/ProductOwner) should actually be checked against -
    state.token_usage.total MINUS ScrumMaster's own usage. ScrumMaster's
    ritual work now draws on its own separate budget (ritual_token_budget
    above); without this exclusion, its own spend would still silently
    erode the shared ceiling everyone else is measured against, defeating
    the entire point of giving it a separate pool.
    """
    total = state.token_usage.total
    sm_usage = (state.token_usage.agents or {}).get("ScrumMaster", 0)
    return max(0, total - sm_usage)


def _retro_and_kpi_freshness(state) -> tuple:
    """Shared by closeout_remaining_work_fraction and sprint_report_step_active below - whether a fresh add_retro_action/add_impediment and a fresh calculate_kpis/update_sprint_report have each happened since the last sprint report."""
    process_signals = len(state.retro_actions or []) + len(state.impediment_log or [])
    retro_fresh = process_signals > state.retro_baseline
    kpi_fresh = state.kpi_update_count > state.kpi_baseline
    return retro_fresh, kpi_fresh


def sprint_report_step_active(state) -> bool:
    """
    GH #395: True once ScrumMaster has freshly logged this sprint's retro
    action/impediment AND a fresh KPI update, but create_sprint_report
    hasn't succeeded yet (sprint_report_pending_release not yet set) - i.e.
    the one mechanical step left this sprint is create_sprint_report
    itself. This window is deliberately both uncapped (ritual_token_budget
    above doesn't apply while this is True - see check_cost_budget_callback)
    and tool-gated (agent.py's before_tool_callback refuses anything that
    isn't the report call sequence while this is True) - uncapping it alone
    would just open a different way to never actually close the sprint.
    """
    retro_fresh, kpi_fresh = _retro_and_kpi_freshness(state)
    return retro_fresh and kpi_fresh and not state.sprint_report_pending_release


def closeout_grace_percent(state=None) -> float:
    """
    How much EXTRA token/USD budget (as a percentage of the main sprint
    ceiling) ProductOwner/ScrumOrchestrator may still spend, combined, after
    the main budget is exhausted - specifically to finish the SPRINT CLOSE
    SEQUENCE's remaining step (create_release_pr) for real, rather than
    skipping it entirely. A real eval run produced no sprint report and no
    release PR at all on exhaustion, since every subsequent model call for
    every agent was replaced with a canned halt response the instant the
    main budget tripped - see check_cost_budget_callback/
    SPRINT_CLOSEOUT_GRACE_ROLES, agents/scrum_team/agent.py. DevTeam/QA/
    Architect get none of this grace - their work is frozen at exhaustion;
    only closing the sprint out still needs turns. Configurable via
    SPRINT_CLOSEOUT_GRACE_PERCENT; default 20.0 (20%, raised from an
    original 5.0 - see ISSUE-0046). 5% (a real run's main sprint ceiling was
    5,000,000, so 250,000 tokens of grace) wasn't enough headroom in
    practice: the close-out sequence used to be several sequential agent
    hops (a non-grace role's redirect to ProductOwner, ProductOwner ->
    Scrum Master for retro, Scrum Master -> QualityGuardian for KPIs,
    QualityGuardian -> ProductOwner for create_sprint_report/
    create_release_pr), each of which cost real tokens purely to reason
    about the next hand-off - a real run burned its entire 5% grace on two
    wrong guesses (an invalid stage transition, a stray transfer to
    Architect) before ever reaching Scrum Master's retro turn, then froze
    with nowhere left to redirect. Each agent's own LiteLLM virtual-key
    budget is still the ultimate financial backstop underneath this either
    way.

    GH #395 update: ScrumMaster is no longer in SPRINT_CLOSEOUT_GRACE_ROLES
    at all - its retro/KPI/sprint-report work now draws on its own separate,
    uncapped-but-gated ritual budget (ritual_token_budget,
    sprint_report_step_active) instead of sharing this one. What's left for
    this grace to cover is just ProductOwner's create_release_pr call.

    GH issue #220: the flat percentage above must be sized for the worst
    case (the whole close-out sequence still outstanding) every time, even
    on a call made after most of it has already finished - e.g. only
    create_release_pr left. When a ScrumState is passed, scale the
    configured percentage down by closeout_remaining_work_fraction(state)
    so a mostly-finished close-out gets a smaller ceiling than one that
    hasn't started, while never going below 25% of the configured value (a
    real incident showed even the *last* remaining step - a bad hand-off
    guess right before create_release_pr - can burn real tokens on its own,
    see ISSUE-0046 above). Omit state (or pass None) to get the flat,
    unscaled percentage - existing callers that never dealt with per-sprint
    progress signals keep today's exact behavior.
    """
    base = float(os.getenv("SPRINT_CLOSEOUT_GRACE_PERCENT", "20.0"))
    if state is None:
        return base
    return base * max(closeout_remaining_work_fraction(state), 0.25)


def closeout_remaining_work_fraction(state) -> float:
    """
    Estimates how much of the SPRINT CLOSE SEQUENCE (retro -> KPIs ->
    create_sprint_report -> create_release_pr) is still outstanding this
    sprint, as a fraction in [0.0, 1.0], purely from existing state signals
    - see GH issue #220. Used by closeout_grace_percent to scale its ceiling
    down as less work remains, instead of a flat percentage. GH #395: retro
    and KPIs are now gated by ScrumMaster's own separate ritual budget
    rather than this one (see sprint_report_step_active), but still used
    here as a progress signal for how much of the overall close-out is done.

    Mirrors the exact "fresh since baseline" checks create_sprint_report
    (tools/budget.py) and calculate_kpis (tools/quality.py) already enforce,
    rather than inventing new state: retro_baseline/kpi_baseline are bumped
    to the then-current counts the moment create_sprint_report succeeds, so
    a value still above baseline means that step is fresh *this* sprint.
    sprint_report_pending_release is set True by create_sprint_report and
    cleared by create_release_pr - so True on its own already implies retro,
    KPIs, and the report are all done and only the release PR remains.

    A brand-new ScrumState (nothing done yet) returns 1.0 - the full
    configured grace - identical to closeout_grace_percent()'s behavior
    before this scaling existed.
    """
    retro_fresh, kpi_fresh = _retro_and_kpi_freshness(state)

    TOTAL_STEPS = 4  # retro, KPIs, sprint report, release PR
    if state.sprint_report_pending_release:
        steps_done = 3  # retro + KPIs + sprint report already done; only release remains
    else:
        steps_done = (1 if retro_fresh else 0) + (1 if kpi_fresh else 0)

    return (TOTAL_STEPS - steps_done) / TOTAL_STEPS


# GH issue #220: roles that get NO SPRINT CLOSE SEQUENCE grace (see
# SPRINT_CLOSEOUT_GRACE_ROLES/closeout_grace_percent above) but should still
# be guaranteed at least one real turn each sprint even if a verbose
# planning phase already exhausted the whole main token budget before any
# of them ran once - see check_cost_budget_callback's reserved-floor branch,
# agent.py. Deliberately a ONE-TIME floor per role, not a standing
# exemption: it only applies while state.token_usage.agents has no recorded
# usage at all for that role this sprint - the instant it logs any real
# usage (update_token_usage_callback), the floor no longer applies and the
# normal hard-halt (no grace at all) resumes for that role, same as today.
NON_GRACE_FLOOR_ROLES = frozenset({"DevTeam", "QA", "Architect"})


# The roles the SPRINT CLOSE SEQUENCE actually needs a real turn from once
# the main budget trips (see closeout_grace_percent above) - ProductOwner
# (create_release_pr) and ScrumOrchestrator itself: run_eval.py's
# continuation nudges (_run_one_sprint's _CONTINUE_NUDGE) are sent as a
# fresh top-level message each time, which re-enters through the root agent
# - if ScrumOrchestrator were hard-halted too, a nudge could never even
# route to anyone in the first place, silently reproducing the exact
# failure this grace exists to fix. ScrumOrchestrator has no code-writing
# tools, so including it adds negligible cost risk.
#
# GH #395: ScrumMaster is deliberately NOT a member here anymore - its own
# retro/KPI/sprint-report work now draws on a separate ritual budget
# (ritual_token_budget) that is entirely independent of whether the main
# budget has tripped, rather than sharing this grace allowance with
# whatever's left of it.
SPRINT_CLOSEOUT_GRACE_ROLES = frozenset({"ProductOwner", "ScrumOrchestrator"})


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
INTERACTION_LEVELS = ("Product", "CEO", "EVAL")
_DEFAULT_INTERACTION_LEVEL = "Product"

# Which record_human_approval(approval_type, ...) must have a fresh entry
# (see the sprint_approval_baseline/release_approval_baseline "must be NEW"
# pattern already used elsewhere) before advance_story_stage(...,
# "Implemented") / create_release_pr may proceed, at each interaction level.
# None means "not required at this level - the team proceeds on its own
# judgment," not "any approval type satisfies it."
_PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL = {
    "Product": "sprint",
    "CEO": "budget",
    "EVAL": None,
}
_PRE_RELEASE_APPROVAL_BY_LEVEL = {
    "Product": "release",
    "CEO": None,
    "EVAL": None,
}


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


# How much detail create_sprint_report actually renders for the human at
# each interaction level (see docs/INTERACTION-LEVELS.md) - distinct from
# the approval-gate mappings above: a CEO human still needs the team's
# retrospective to have genuinely happened (create_sprint_report's
# retro_baseline gate applies at every level, unconditionally), they just
# don't need every internal/technical detail rendered in the report they
# personally read.
# - "full": everything - per-agent token usage, retro/impediment detail,
#   story-level estimates, full transcript excerpts. For Product (embedded
#   day-to-day) and EVAL (the report is analyzed by tooling afterwards, not
#   read by a human at all - trimming it would only lose signal).
# - "executive": budget and headline outcomes only - a CEO approves spend,
#   not process detail; everything else is one line pointing at where the
#   full detail still lives (specs/reports/), never silently discarded.
_REPORT_DETAIL_LEVEL_BY_LEVEL = {
    "Product": "full",
    "CEO": "executive",
    "EVAL": "full",
}


def report_detail_level(level: str | None = None) -> str:
    """"full" | "executive" - see _REPORT_DETAIL_LEVEL_BY_LEVEL."""
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
# roles are a larger follow-up, not part of this pipeline yet).
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


# GH issue #354: a real eval run showed the model categorizing every single
# retro finding as "technical" across 5 sprints straight - including ones
# that, by #342's own prompt guidance, are textbook role-behavior/process-
# discipline gaps ("Maintain rigorous story sequencing...", "Keep feature
# branches synchronized..."). Prompt-only guidance on what "steering" means
# wasn't enough to get it actually used - this is a non-blocking mechanical
# nudge, not a new gate: a "technical" finding whose text matches these
# signals still succeeds, just with a warning suggesting category="steering"
# instead. False positives are fine (a real code task happening to mention
# "sequence" must not be refused); false negatives just mean no nudge fires.
_ROLE_BEHAVIOR_SIGNAL_PHRASES = (
    "discipline", "synchronized", "in strict sequence", "in sequence",
    "sequencing", "rigorous", "consistently", "going forward", "recurring",
    "repeatedly", "every sprint", "each sprint", "adhere to", "stay in sync",
    # GH issue #381: a real run (0.1.0-run47) filed 4 of 5 retro findings as
    # "technical" despite reading as team-process reminders, not one-time
    # code tasks - none of the phrases above happened to appear in any of
    # them (e.g. "Ensure automated tests are fully stable before starting
    # sprint test execution phases", "Recreate clean PRs promptly when merge
    # conflicts arise to avoid stale branch blocking", "Ensure all stories in
    # sprint backlog reach accepted stage before sprint finalization").
    # These generalize the imperative "do X before/promptly during Y"
    # process-gating shape those share, rather than overfitting to their
    # exact wording.
    "before starting", "before story implementation", "finalization",
    "promptly", "stale branch",
)
_ROLE_NAMES_FOR_BEHAVIOR_SIGNAL = (
    "devteam", "dev team", "architect", "qa", "scrummaster", "scrum master",
    "productowner", "product owner",
)


def looks_like_role_behavior_finding(text) -> bool:
    """Heuristic (GH issue #354): does `text` read like a finding about how
    a role/the team behaves or follows process, rather than a concrete
    code/process task - see the module comment above this for why."""
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    if any(phrase in lowered for phrase in _ROLE_BEHAVIOR_SIGNAL_PHRASES):
        return True
    return any(role in lowered for role in _ROLE_NAMES_FOR_BEHAVIOR_SIGNAL)


_MIN_SHARED_KEYWORDS_FOR_RECURRENCE = 2
_RETRO_STOPWORDS = {
    "that", "this", "with", "from", "have", "been", "were", "will", "into",
    "before", "after", "during", "should", "would", "could", "about", "their",
    "there", "these", "those", "ensure", "ensuring", "maintain", "maintaining",
}


_NON_ALNUM_RE_FOR_RETRO = re.compile(r"[^a-z0-9]+")


def _retro_keywords(text: str) -> set:
    words = _NON_ALNUM_RE_FOR_RETRO.sub(" ", (text or "").lower()).split()
    return {w for w in words if len(w) >= 5 and w not in _RETRO_STOPWORDS}


def recurring_technical_finding_sprint(existing_entries, new_text: str, new_sprint_number, text_field: str) -> int | None:
    """
    GH issue #354 (recurrence escalation): among `existing_entries` (prior
    add_retro_action/add_impediment entries, each a dict with a category and
    a sprint_number), returns the sprint_number of the most recent
    STRICTLY-EARLIER-sprint "technical" entry whose text shares at least
    `_MIN_SHARED_KEYWORDS_FOR_RECURRENCE` significant keywords with
    `new_text` - i.e. the same kind of finding logged again without ever
    being escalated to a steering change. None if nothing recurs.
    """
    new_keywords = _retro_keywords(new_text)
    if len(new_keywords) < _MIN_SHARED_KEYWORDS_FOR_RECURRENCE:
        return None
    best_sprint = None
    for entry in existing_entries or []:
        if entry.get("category") != "technical":
            continue
        entry_sprint = entry.get("sprint_number")
        if entry_sprint is None or entry_sprint >= new_sprint_number:
            continue
        existing_keywords = _retro_keywords(entry.get(text_field, ""))
        if len(new_keywords & existing_keywords) >= _MIN_SHARED_KEYWORDS_FOR_RECURRENCE:
            if best_sprint is None or entry_sprint > best_sprint:
                best_sprint = entry_sprint
    return best_sprint


# --- BLOCKED stories (agents stuck on an unresolved question or loop) ---
# raise_story_blocker/resolve_story_blocker (agents/scrum_team/tools/
# requirements.py) mark a story BLOCKED - orthogonal to STORY_STAGES, since
# it can happen from any stage, not just a fixed point in the pipeline.
# "category" decides who's asked to clarify: a technical question goes to
# Architect, a product/business question goes to Product Owner - or, at the
# "Product" interaction level, straight to the human User instead, since
# that human already IS the acting product owner day-to-day. Technical
# questions always go to Architect regardless of level - there's no "human
# Architect" role at any level.
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

    Debug-logs the exact state this decision was based on whenever it
    rejects: a real ADK eval run showed this firing "sprint_number is
    unset" despite the eval case's own fixture seeding sprint_number: 1 at
    the exact commit that run checked out - session-state resolution
    somewhere between the eval harness and this call apparently isn't 100%
    reliable, and tracing it from the tool-call log alone wasn't enough to
    pin down. This gives the next occurrence something concrete to compare
    against the fixture instead of having to infer it.
    """
    sprint_number = state.get("sprint_number", 0)
    if sprint_number <= 0:
        logger.debug(
            "sprint_backlog_pr_missing rejecting: sprint_number=%r, "
            "sprint_backlog_pr_sprint=%r, product_backlog ids=%r, state keys=%r",
            state.get("sprint_number"),
            state.get("sprint_backlog_pr_sprint"),
            [item.get("id") for item in (state.get("product_backlog") or []) if isinstance(item, dict)],
            sorted(state.keys()) if hasattr(state, "keys") else type(state),
        )
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


def ready_backlog_shortfall(product_backlog: list, backlog_scope_complete: bool = False) -> int:
    """
    How many more stories must reach Ready before the backlog holds
    target_stories_per_sprint() * ready_backlog_sprints_target() stories
    that are Ready-or-further but not yet Accepted (0 if already met).
    Counts non-Epic, non-BLOCKED items only - the same population
    _preceding_story's one-story-at-a-time ordering check considers "real"
    backlog work (agents/scrum_team/tools/requirements.py).

    `backlog_scope_complete` (state key set by declare_backlog_scope_complete,
    tools/requirements.py - ISSUE-0046): an honest escape hatch, deliberately
    NOT an automatic cap based on however many items merely happen to be in
    product_backlog right now - that would silently satisfy this gate for
    any real, open-ended backlog too (a PO could just never enter more than
    the target and this gate would trivially stop demanding more, which
    defeats its whole purpose). A real eval run's fixed, deliberately-
    closed-scope product ran genuinely out of real stories against a target
    of 3 and, with no honest way to say so, fabricated two throwaway
    "Additional Buffer Story" entries purely to pad the count instead. This
    flag requires an explicit, justified Product Owner declaration that
    real scope is genuinely exhausted (see declare_backlog_scope_complete)
    before the target is waived - never inferred just from list length.
    """
    if backlog_scope_complete:
        return 0
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


# --- Sprint-phase awareness (GH issue #403) ---
# The ritual the team is meant to follow - conceptual work (groom the
# backlog to Ready) -> plan & start the sprint -> development -> review,
# retro & release - was previously enforced only REACTIVELY: a tool call
# made out of phase gets mechanically rejected after the fact
# (new_sprint_item_blocked/sprint_backlog_pr_missing/ready_backlog_shortfall
# above), but nothing proactively told the team which phase it's actually
# in before that happens. A real eval run (0.1.0-run52) showed DevTeam try
# to jump straight to implementation, get rejected, and only then have
# Product Owner backfill the planning work that should have come first.
#
# current_sprint_phase below derives one of SPRINT_PHASES purely from
# existing state signals (nothing new to track) so it can be surfaced
# proactively - as a one-line system-context nudge every role sees every
# turn (agent.py's phase_awareness_injection_callback), and as a console-
# log prefix a human watching a live run can see at a glance. Nudging
# only: nothing here may ever refuse a tool call - the existing mechanical
# gates above remain the only real enforcement.
SPRINT_PHASES = (
    "Conceptual Work",
    "Plan & Start Sprint",
    "Development",
    "Review, Retro & Release",
)

SPRINT_PHASE_GUIDANCE = {
    "Conceptual Work": (
        "The Ready backlog isn't deep enough yet - groom it (upsert_prd/upsert_epic/upsert_story, "
        "then advance_story_stage(..., 'Ready')) until it is. Starting a sprint, publishing a Sprint "
        "Backlog PR, or implementation work don't belong in this phase yet."
    ),
    "Plan & Start Sprint": (
        "The Ready backlog is deep enough - Scrum Master calls start_sprint(goal) if that hasn't "
        "happened yet this sprint, then Product Owner publishes it via create_sprint_backlog_pr(). "
        "More backlog grooming belongs here only for a specific, stated reason, not as a default."
    ),
    "Development": (
        "This sprint's Sprint Backlog is published - implement/review/test/accept its stories. New "
        "backlog grooming or re-planning belongs in Conceptual Work/Plan & Start Sprint, not here."
    ),
    "Review, Retro & Release": (
        "This sprint's planned work is done (or its budget is spent) - run the retrospective, KPIs, "
        "sprint report, and release PR (Scrum Master's own SPRINT CLOSE SEQUENCE). Starting new "
        "implementation work does not belong here."
    ),
}


def current_sprint_phase(state: dict) -> str:
    """
    One of SPRINT_PHASES, derived purely from existing state - see the
    module comment above for why this exists and what it's for (nudging
    only, never a gate).
    """
    if sprint_backlog_pr_missing(state) is None:
        # Sprint started AND this sprint's own Sprint Backlog PR already
        # published - either doing the work, or already wrapping it up.
        if state.get("sprint_report_pending_release"):
            return "Review, Retro & Release"
        sprint_stories = [
            x for x in (state.get("sprint_backlog") or [])
            if x.get("type", "User Story") != "Epic"
        ]
        all_accepted = bool(sprint_stories) and all(
            "Accepted" in (x.get("stages_completed") or []) for x in sprint_stories
        )
        budgets = state.get("budgets") or {}
        usage = state.get("token_usage") or {}
        token_limit = budgets.get("total") or 0
        token_usage = usage.get("total") or 0
        budget_exhausted = token_limit > 0 and token_usage >= token_limit
        if all_accepted or budget_exhausted:
            return "Review, Retro & Release"
        return "Development"
    if ready_backlog_shortfall(state.get("product_backlog") or [], state.get("backlog_scope_complete", False)) <= 0:
        return "Plan & Start Sprint"
    return "Conceptual Work"
