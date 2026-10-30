"""
Regression coverage for _run_one_sprint's handling of ADK-internal errors
raised mid-turn. No pytest-asyncio dependency - drives the coroutine
directly via asyncio.run() inside plain sync test functions.
"""

import asyncio
import os
import time
from unittest.mock import patch

from agents.scrum_team.scripts.run_eval import (
    _run_one_sprint,
    _sprint_should_abort_run,
    _blocked_stories,
    _sprint_needs_human_this_harness_cannot_provide,
    _unresolved_high_priority_general_blocker,
    _unresolvable_release_pr_conflict,
    _sprint_message,
    _start_fresh_sprint_session,
)


class _FakeSession:
    def __init__(self, state=None, session_id="session-1"):
        self.state = state or {}
        self.id = session_id


class _FakeSessionService:
    async def get_session(self, app_name, user_id, session_id):
        return _FakeSession()


class _SelfTransferRunner:
    """Simulates ADK's resolve_and_derive_transfer_context rejecting a
    hallucinated agent-transfers-to-itself tool call mid-turn."""

    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        raise ValueError("Agent 'DevTeam' cannot transfer to itself.")
        yield  # pragma: no cover - never reached; keeps this an async generator


class _OtherValueErrorRunner:
    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        raise ValueError("some unrelated bug")
        yield  # pragma: no cover


class _FakeEvent:
    def __init__(self, author="TestAgent"):
        self.author = author
        self.content = None


class _OneEventThenHaltRunner:
    """Yields exactly one event then ends the turn - with max_events=1,
    _run_one_sprint's host-side loop breaks with stop_reason
    "max_events_reached" right after, the same way a real eval run's
    sprint got cut off mid-close-out."""

    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        yield _FakeEvent()


class _StatefulSessionService:
    """Unlike _FakeSessionService (a fresh, empty-state session every call),
    returns the SAME session/state across calls - so a mutation made mid-
    sprint (or by this test's own setup) is still visible on the next
    get_session() call, matching real ADK session behavior."""

    def __init__(self, state):
        self._session = _FakeSession(state)

    async def get_session(self, app_name, user_id, session_id):
        return self._session


class _FullFakeSessionService:
    """Unlike _StatefulSessionService (get_session only), also implements
    create_session/delete_session with call tracking - for testing
    _start_fresh_sprint_session (SPEC-ISSUE-0055) in isolation."""

    def __init__(self, state):
        self._state = dict(state)
        self.created = []
        self.deleted = []

    async def get_session(self, app_name, user_id, session_id):
        return _FakeSession(dict(self._state), session_id=session_id)

    async def create_session(self, app_name, user_id, state=None):
        new_id = f"session-{len(self.created) + 2}"
        self._state = dict(state or {})
        self.created.append({"id": new_id, "state": dict(self._state)})
        return _FakeSession(dict(self._state), session_id=new_id)

    async def delete_session(self, app_name, user_id, session_id):
        self.deleted.append(session_id)


class _NoProgressRunner:
    """Ends every turn immediately with no events and no tool activity at
    all - simulates a sprint invocation where the team did nothing new this
    attempt (e.g. it's still between real sprints)."""

    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        return
        yield  # pragma: no cover - never reached; keeps this an async generator


def _run(coro):
    return asyncio.run(coro)


def test_run_one_sprint_recovers_from_self_transfer_error():
    result = _run(_run_one_sprint(
        _SelfTransferRunner(), _FakeSessionService(), "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60,
    ))

    assert result["stop_reason"] == "adk_self_transfer_error"
    assert result["event_count"] == 0


def test_run_one_sprint_reraises_unrelated_value_error():
    try:
        _run(_run_one_sprint(
            _OtherValueErrorRunner(), _FakeSessionService(), "app", "user", "session-1",
            "hello", max_events=300, deadline=time.monotonic() + 60,
        ))
    except ValueError as e:
        assert "some unrelated bug" in str(e)
    else:
        raise AssertionError("expected the unrelated ValueError to propagate, not be swallowed")


def test_run_one_sprint_does_not_mistake_a_stale_report_from_before_this_call_as_fresh():
    """
    GH #413: _run_one_sprint no longer clears sprint_report via state_delta
    (that mutation now belongs to start_sprint's own mechanical reset,
    tied to the real sprint boundary) - it snapshots whatever's already in
    state as a baseline and only treats a CHANGED value as "produced this
    invocation". A session that already has a report sitting in state
    (e.g. the team hasn't called start_sprint for a new sprint yet) must
    not be mistaken for "this attempt just produced one".
    """
    session_service = _StatefulSessionService({"sprint_report": "Stale report from a prior sprint"})
    result = _run(_run_one_sprint(
        _NoProgressRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))

    assert result["stop_reason"] != "sprint_report_produced"
    assert result["sprint_report"] is None


def test_run_one_sprint_detects_a_genuinely_new_report_against_the_baseline():
    """Sibling to the stale-report case above: a report that actually
    CHANGES from whatever baseline existed is real completion evidence."""
    state = {"sprint_report": "Stale report from a prior sprint"}
    session_service = _StatefulSessionService(state)

    class _ProducesNewReportRunner:
        async def run_async(self, user_id, session_id, new_message, state_delta=None):
            state["sprint_report"] = "Fresh report from this very call"
            return
            yield  # pragma: no cover

    result = _run(_run_one_sprint(
        _ProducesNewReportRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))

    assert result["stop_reason"] == "sprint_report_produced"
    assert result["sprint_report"] == "Fresh report from this very call"


def test_run_one_sprint_discards_identical_kpis_left_over_from_a_prior_sprint():
    """Sibling to the stale-report case above, for sprint_report_kpis: a
    session that already has a KPI dict sitting in state (kpi_update_count
    never advanced this invocation) must not be mistaken for "this attempt
    just recomputed them"."""
    session_service = _StatefulSessionService({
        "sprint_report_kpis": {"maintainability": {"test_coverage": 0.97}},
        "kpi_update_count": 3,
    })
    result = _run(_run_one_sprint(
        _NoProgressRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))

    assert result["sprint_report_kpis"] is None


def test_run_one_sprint_detects_fresh_kpis_even_when_identical_to_the_prior_value():
    """
    GH issue (0.1.0-run57): a real eval run had update_sprint_report
    genuinely recompute KPIs in two separate sprints that happened to come
    out byte-identical to the previous sprint's values (a simple fixed test
    project's coverage/complexity/security-scan numbers don't necessarily
    change sprint to sprint) - comparing the KPI dict's own value against a
    baseline wrongly treated this as "nothing new", discarding real data.
    kpi_update_count (bumped by every successful update_sprint_report call
    regardless of whether the values changed) is the correct signal.
    """
    kpis = {"maintainability": {"test_coverage": 0.97}}
    state = {"sprint_report_kpis": dict(kpis), "kpi_update_count": 3}
    session_service = _StatefulSessionService(state)

    class _RecomputesIdenticalKpisRunner:
        async def run_async(self, user_id, session_id, new_message, state_delta=None):
            # Same values as before, but update_sprint_report really did run.
            state["sprint_report_kpis"] = dict(kpis)
            state["kpi_update_count"] = 4
            return
            yield  # pragma: no cover

    result = _run(_run_one_sprint(
        _RecomputesIdenticalKpisRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))

    assert result["sprint_report_kpis"] == kpis


def test_run_one_sprint_never_passes_a_state_delta_to_the_runner():
    """GH #413: no harness-side state mutation at all any more - token
    budget/report/KPI resets are exclusively start_sprint's job now,
    mechanical and tied to the real sprint boundary."""
    calls = []

    class _CapturingRunner:
        async def run_async(self, user_id, session_id, new_message, **kwargs):
            calls.append(kwargs)
            return
            yield  # pragma: no cover

    _run(_run_one_sprint(
        _CapturingRunner(), _FakeSessionService(), "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))

    assert len(calls) == 1
    assert "state_delta" not in calls[0]


def test_run_one_sprint_reports_whether_the_release_is_still_pending():
    """
    Acceptance Criteria (GH #421): sprint_report_pending_release is read
    straight from end-of-invocation session state and returned alongside
    stop_reason, so _sprint_should_abort_run can tell "a report was
    authored" apart from "the release also actually went out".
    """
    session_service = _StatefulSessionService({"sprint_report_pending_release": True})
    result = _run(_run_one_sprint(
        _NoProgressRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))
    assert result["sprint_report_pending_release"] is True

    session_service = _StatefulSessionService({"sprint_report_pending_release": False})
    result = _run(_run_one_sprint(
        _NoProgressRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
    ))
    assert result["sprint_report_pending_release"] is False


def test_run_one_sprint_keeps_nudging_when_a_report_appears_but_the_release_is_still_pending():
    """
    SPEC-ISSUE-0066 / 0.1.0-run70: a new sprint_report alone is NOT proof the
    sprint actually closed - mirrors _sprint_should_abort_run's own
    GH #421 invariant, but applied here, where a real run showed the
    premature stop actually happens. Sprint 4 of 0.1.0-run70 had its turn
    loop end ("sprint_report_produced") one step before ScrumMaster reached
    create_release_pr, leaving sprint_report_pending_release stuck True for
    all of the next sprint. The loop must keep nudging instead of declaring
    the sprint done while the release is still pending - only exhausting
    max_nudges/max_events/max_duration like it would with no report at all.
    """
    state = {
        "sprint_report": "Stale report from a prior sprint",
        "sprint_report_pending_release": True,
    }
    session_service = _StatefulSessionService(state)

    class _ReportWithoutReleaseRunner:
        async def run_async(self, user_id, session_id, new_message, state_delta=None):
            state["sprint_report"] = "Fresh report, release still pending"
            return
            yield  # pragma: no cover

    result = _run(_run_one_sprint(
        _ReportWithoutReleaseRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=2,
    ))

    assert result["stop_reason"] != "sprint_report_produced"
    assert result["stop_reason"] == "max_nudges_exhausted"
    # The report is real and should still be surfaced even though the sprint
    # isn't considered cleanly closed - downstream metrics/manifest and
    # _sprint_should_abort_run both need to see it alongside the still-True
    # pending flag.
    assert result["sprint_report"] == "Fresh report, release still pending"
    assert result["sprint_report_pending_release"] is True


def test_run_one_sprint_still_stops_immediately_once_the_release_has_also_gone_out():
    """Unchanged, already-correct behavior: a new report with the release
    flag already clear is a genuinely clean close-out and should still stop
    the loop right away, same as before this fix."""
    state = {
        "sprint_report": "Stale report from a prior sprint",
        "sprint_report_pending_release": False,
    }
    session_service = _StatefulSessionService(state)

    class _CleanCloseoutRunner:
        async def run_async(self, user_id, session_id, new_message, state_delta=None):
            state["sprint_report"] = "Fresh report, release already merged"
            return
            yield  # pragma: no cover

    result = _run(_run_one_sprint(
        _CleanCloseoutRunner(), session_service, "app", "user", "session-1",
        "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=2,
    ))

    assert result["stop_reason"] == "sprint_report_produced"
    assert result["sprint_report"] == "Fresh report, release already merged"


def test_sprint_should_not_abort_when_no_critical_halt_occurred():
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "sprint_report_produced"}) is False
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "max_nudges_exhausted"}) is False


def test_sprint_should_not_abort_when_a_critical_halt_still_closed_out_cleanly():
    # SPEC-ISSUE-0045 / 0.1.0-run25: check_cost_budget_callback's SPRINT CLOSE
    # SEQUENCE grace now redirects a frozen non-grace agent back to
    # ProductOwner (agent.py's _budget_halt_response) instead of leaving the
    # sprint permanently stuck - if that worked and a real sprint report came
    # out the other end, AND the release PR also actually succeeded
    # (sprint_report_pending_release cleared), the sprint ended in a clean
    # state and the whole 5-sprint run shouldn't be abandoned over it.
    assert _sprint_should_abort_run({
        "critical_halt": True, "stop_reason": "sprint_report_produced",
        "sprint_report": "a real report", "sprint_report_pending_release": False,
    }) is False


def test_sprint_should_not_abort_when_the_host_side_backstop_produced_the_report():
    """
    Acceptance Criteria (0.1.0-run60): _run_one_sprint's own host-side
    backstop (right after the per-sprint loop: "if not sprint_report and
    session.state.get('critical_halt_notified')...") exists precisely so a
    report always gets produced even when the harness's own max_events/
    max_duration cap - not the in-session budget grace - ends the sprint
    first. That path genuinely populates sprint_report (and, in a real run,
    the release PR went out too) but never revises stop_reason away from
    "max_events_reached"/"max_duration_exceeded" - checking the stop_reason
    STRING instead of whether a report actually exists wrongly treated this
    as "no clean closeout" and aborted an otherwise-healthy run after just
    one sprint.
    """
    assert _sprint_should_abort_run({
        "critical_halt": True, "stop_reason": "max_events_reached",
        "sprint_report": "a fallback report, produced by the host-side backstop",
        "sprint_report_pending_release": False,
    }) is False


def test_sprint_should_abort_when_a_critical_halt_left_no_clean_close_out():
    # The pre-fix behavior (and still correct) for the case the grace
    # allowance doesn't cover: a real run previously hit an unrelated
    # transfer-loop crash in the *next* sprint after silently continuing
    # from exactly this kind of unclean state (GH issue #167). No
    # sprint_report at all here - genuinely nothing was ever produced,
    # unlike the host-side-backstop case above.
    assert _sprint_should_abort_run({"critical_halt": True, "stop_reason": "max_nudges_exhausted"}) is True
    assert _sprint_should_abort_run({"critical_halt": True, "stop_reason": "max_events_reached"}) is True


def test_sprint_should_abort_when_the_report_was_produced_but_the_release_never_went_out():
    """
    Acceptance Criteria (GH #421): a real eval run (0.1.0-run55) had
    create_sprint_report succeed, then create_release_pr fail later in the
    SAME invocation (an untracked-files checkout error, GH #419) -
    sprint_report_pending_release stayed True (only create_release_pr's own
    success path clears it), mechanically blocking the next sprint's
    start_sprint call. A report existing alone is not proof of a clean
    close-out - this must still abort.
    """
    assert _sprint_should_abort_run({
        "critical_halt": True, "stop_reason": "sprint_report_produced",
        "sprint_report": "a real report", "sprint_report_pending_release": True,
    }) is True


def test_sprint_should_abort_when_release_is_still_pending_even_without_a_critical_halt():
    """
    SPEC-ISSUE-0067 / 0.1.0-run70: a stuck sprint_report_pending_release is
    unsafe to continue from regardless of why the sprint ended - not only
    when a critical budget halt occurred. 0.1.0-run70's Sprint 4 hit exactly
    this combination (no critical halt at all, just its own per-sprint turn
    loop running out of turns one step before create_release_pr) and nothing
    caught it before Sprint 5 began structurally blocked by the inherited
    phase. (Previously this asserted False, on the mistaken assumption this
    case was "already handled" elsewhere - SPEC-ISSUE-0066 narrows how often
    it arises, but doesn't eliminate it, so this function needs its own fix.)
    """
    assert _sprint_should_abort_run({
        "critical_halt": False, "stop_reason": "sprint_report_produced",
        "sprint_report": "a real report", "sprint_report_pending_release": True,
    }) is True


def test_sprint_should_not_abort_without_a_critical_halt_when_nothing_was_ever_produced():
    """A non-critical-halt sprint that simply hasn't produced a report yet
    (e.g. still early/healthy, mid-development) must not be treated as stuck
    - only an actual report-exists-but-release-still-pending signal should
    trigger an abort on the non-critical-halt path."""
    assert _sprint_should_abort_run({
        "critical_halt": False, "stop_reason": "max_events_reached", "sprint_report_pending_release": True,
    }) is False


def _story(story_id, blocked=None):
    return {"id": story_id, "title": story_id, "blocked": blocked}


class TestBlockedStories:
    def test_empty_when_nothing_blocked(self):
        sprint_result = {"product_backlog": [_story("US-0001")]}
        assert _blocked_stories(sprint_result) == {}

    def test_finds_every_blocked_story(self):
        sprint_result = {
            "product_backlog": [
                _story("US-0001", {"category": "technical", "question": "why?"}),
                _story("US-0002"),
                _story("US-0003", {"category": "product", "question": "which color?"}),
            ]
        }
        result = _blocked_stories(sprint_result)
        assert set(result.keys()) == {"US-0001", "US-0003"}

    def test_handles_missing_product_backlog(self):
        assert _blocked_stories({}) == {}


class TestUnresolvedHighPriorityGeneralBlocker:
    """
    Acceptance Criteria (GH issue #342): a "human"-category retro/impediment
    finding raised with priority="high" is, by definition, something
    genuinely outside the team's own authority to resolve - this scripted,
    unattended harness has no human to act on it, so the run should stop
    rather than burn further sprints' budget, same reasoning as
    _sprint_needs_human_this_harness_cannot_provide's "needs_human" case.
    """

    def test_none_when_no_general_blockers(self):
        assert _unresolved_high_priority_general_blocker({}) is None

    def test_none_when_only_normal_priority(self):
        sprint_result = {"general_blockers": [{"description": "x", "priority": "normal", "resolved": False}]}
        assert _unresolved_high_priority_general_blocker(sprint_result) is None

    def test_none_when_high_priority_already_resolved(self):
        sprint_result = {"general_blockers": [{"description": "x", "priority": "high", "resolved": True}]}
        assert _unresolved_high_priority_general_blocker(sprint_result) is None

    def test_finds_an_unresolved_high_priority_blocker(self):
        sprint_result = {"general_blockers": [
            {"description": "normal one", "priority": "normal", "resolved": False},
            {"description": "the real one", "priority": "high", "resolved": False},
        ]}
        result = _unresolved_high_priority_general_blocker(sprint_result)
        assert result is not None
        assert result["description"] == "the real one"


@patch.dict(os.environ, {}, clear=False)
class TestSprintNeedsHumanThisHarnessCannotProvide:
    def setup_method(self):
        os.environ.pop("INTERACTION_LEVEL", None)  # default: "Product"

    def test_none_when_nothing_blocked(self):
        sprint_result = {"product_backlog": [_story("US-0001")]}
        assert _sprint_needs_human_this_harness_cannot_provide(sprint_result, set()) is None

    def test_needs_human_for_a_product_category_blocker_at_product_level(self):
        """
        Acceptance Criteria (GH eval run39): at the "Product" interaction
        level (this harness's own default), a "product"-category blocker
        escalates straight to the human User (should_escalate_blocker_to_
        user, agents/scrum_team/helpers.py) - this scripted harness has no
        human to answer it, so it must stop immediately, not wait a sprint.
        """
        sprint_result = {
            "product_backlog": [_story("US-0001", {"category": "product", "question": "which color?"})]
        }
        result = _sprint_needs_human_this_harness_cannot_provide(sprint_result, set())
        assert result is not None
        story_id, blocked, reason = result
        assert story_id == "US-0001"
        assert reason == "needs_human"

    def test_needs_human_for_a_product_category_blocker_even_at_eval_interaction_level(self):
        """
        Acceptance Criteria (GH #414): the real eval harness runs at
        INTERACTION_LEVEL=EVAL (set by _configure_env), not "Product" -
        should_escalate_blocker_to_user's own category check used to be
        gated on the REAL configured level, so a "product"-category blocker
        never escalated immediately in EVAL mode no matter how clearly only
        a human could answer it, always paying the "unresolved_across_
        sprint" grace below first instead. This harness always has no
        human to ask, in any mode - it must escalate immediately here too.
        """
        os.environ["INTERACTION_LEVEL"] = "EVAL"
        sprint_result = {
            "product_backlog": [_story("US-0001", {"category": "product", "question": "which color?"})]
        }
        result = _sprint_needs_human_this_harness_cannot_provide(sprint_result, set())
        assert result is not None
        story_id, blocked, reason = result
        assert story_id == "US-0001"
        assert reason == "needs_human"

    def test_mechanically_detected_blocker_stops_immediately_without_waiting_a_sprint(self):
        """
        Acceptance Criteria (GH #414): a blocker raised by one of agent.py's
        own loop breakers (_detect_transfer_loop/_detect_repeated_call_loop,
        marked mechanically_detected=True by _mark_blocker_mechanically_
        detected) already proved the team couldn't make progress through
        repeated attempts - treated as an immediate stop, same as
        "needs_human", regardless of category and without waiting for the
        "unresolved_across_sprint" grace a fresh ordinary blocker gets.
        """
        sprint_result = {
            "product_backlog": [_story("US-0001", {
                "category": "technical", "question": "why?", "mechanically_detected": True,
            })]
        }
        result = _sprint_needs_human_this_harness_cannot_provide(sprint_result, set())
        assert result is not None
        story_id, blocked, reason = result
        assert story_id == "US-0001"
        assert reason == "mechanically_detected"

    def test_no_immediate_stop_for_a_fresh_technical_blocker(self):
        """A "technical" blocker never escalates to the human (Architect
        always owns it) - freshly blocked this sprint, it gets a full
        sprint's grace before this stops the run."""
        sprint_result = {
            "product_backlog": [_story("US-0001", {"category": "technical", "question": "why?"})]
        }
        assert _sprint_needs_human_this_harness_cannot_provide(sprint_result, set()) is None

    def test_stops_when_a_technical_blocker_persists_across_a_sprint_boundary(self):
        """
        Acceptance Criteria (GH eval run39): US-0001 was blocked by the
        mechanical loop-breaker in sprint 2 and sat blocked through sprints
        3-5 with no resolution - a full sprint's own budget didn't move it,
        so further sprints are the same bet with no new information.
        """
        sprint_result = {
            "product_backlog": [_story("US-0001", {"category": "technical", "question": "why?"})]
        }
        result = _sprint_needs_human_this_harness_cannot_provide(sprint_result, {"US-0001"})
        assert result is not None
        story_id, blocked, reason = result
        assert story_id == "US-0001"
        assert reason == "unresolved_across_sprint"

    def test_does_not_stop_when_a_previously_blocked_story_was_resolved(self):
        sprint_result = {"product_backlog": [_story("US-0001")]}  # no longer blocked
        assert _sprint_needs_human_this_harness_cannot_provide(sprint_result, {"US-0001"}) is None

    def test_does_not_stop_when_a_different_story_is_newly_blocked(self):
        """Only the SAME story persisting across the boundary counts - a
        different story blocked for the first time this sprint gets its
        own full sprint's grace, same as any other fresh technical
        blocker."""
        sprint_result = {
            "product_backlog": [_story("US-0002", {"category": "technical", "question": "why?"})]
        }
        assert _sprint_needs_human_this_harness_cannot_provide(sprint_result, {"US-0001"}) is None


class TestHostSideSprintReportBackstop:
    """
    Acceptance Criteria (GH eval run41): a real run's SPRINT CLOSE SEQUENCE
    grace made genuine progress (retro logged, KPIs computed) but got cut
    off by this harness's own max_events cap one turn before ProductOwner's
    create_sprint_report - agent.py's own safety net only fires once a
    grace-eligible role's turn ALSO exceeds its own (shrunk) grace
    allowance, which never happened here, so no report was ever produced.
    _run_one_sprint now calls the same mechanism directly as a backstop
    whenever the sprint ends with critical_halt_notified set but no report.
    """

    def test_calls_the_backstop_when_critical_halt_left_no_report(self):
        state = {"critical_halt_notified": True, "sprint_number": 1}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context, reason=None):
            tool_context.state["sprint_report"] = "# Fallback Report"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_called_once()
        assert result["stop_reason"] == "max_events_reached"
        assert result["sprint_report"] == "# Fallback Report"

    def test_does_not_call_the_backstop_when_this_invocation_already_produced_a_real_report(self):
        """
        GH #413: sprint_report is compared against a baseline snapshotted
        BEFORE this invocation started, not read bare - a report that was
        already sitting in state before anything ran this call (e.g. a
        genuinely stale leftover from a previous sprint the team hasn't
        started a new one over yet) must NOT be mistaken for "this
        invocation's own work produced one". This test instead simulates
        the real case the backstop is meant to skip: THIS invocation's own
        activity (the event loop below) is what produced the report.
        """
        state = {"critical_halt_notified": True, "sprint_number": 1}
        session_service = _StatefulSessionService(state)

        class _ProducesReportThenHaltsRunner:
            async def run_async(self, user_id, session_id, new_message, state_delta=None):
                state["sprint_report"] = "# Real Report"
                yield _FakeEvent()

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt") as mock_ensure:
            result = _run(_run_one_sprint(
                _ProducesReportThenHaltsRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_not_called()
        assert result["sprint_report"] == "# Real Report"

    def test_backstop_still_fires_for_a_genuinely_stale_leftover_report(self):
        """
        Sibling/contrast to the test above: a report already sitting in
        state BEFORE this invocation did anything (the team hasn't called
        start_sprint for a new sprint yet, so the previous sprint's own
        report is still there) must not suppress the backstop - this
        invocation's own activity produced nothing new.
        """
        state = {"critical_halt_notified": True, "sprint_number": 1, "sprint_report": "# Stale Report From Last Sprint"}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context, reason=None):
            tool_context.state["sprint_report"] = "# Fallback Report"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_called_once()
        assert result["sprint_report"] == "# Fallback Report"

    def test_does_not_call_the_backstop_when_no_sprint_has_actually_started(self):
        """No sprint_number set at all means start_sprint was never even
        called this invocation - there's no real sprint for the backstop to
        close out, regardless of stop_reason."""
        state = {}
        session_service = _StatefulSessionService(state)

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt") as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_not_called()
        assert result["sprint_report"] is None

    def test_calls_the_backstop_when_the_nudge_ceiling_is_exhausted_even_without_a_critical_halt(self):
        """
        Acceptance Criteria (SPEC-ISSUE-0077, 0.1.0-run76): Sprint 4 stopped
        with stop_reason "max_nudges_exhausted" at only 636K of its 5M token
        budget - genuinely not budget-constrained, so critical_halt_notified
        was never set. The backstop used to only ever fire on that flag,
        so this sprint got NO report and NO transcript committed at all
        (confirmed missing: specs/reports/SPRINT-REPORT-004.md never
        existed). It must now fire for any genuinely-started sprint
        (sprint_number set) that ends with no report, whatever the reason.
        """
        state = {"sprint_number": 4}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context, reason=None):
            tool_context.state["sprint_report"] = f"# Fallback Report (reason={reason})"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _NoProgressRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_called_once()
        assert mock_ensure.call_args.kwargs.get("reason") == "nudges"
        assert result["stop_reason"] == "max_nudges_exhausted"
        assert result["sprint_report"] == "# Fallback Report (reason=nudges)"

    def test_backstop_passes_the_budget_reason_when_a_critical_halt_was_notified(self):
        """The original, still-most-common case must keep passing
        reason="budget" explicitly - not just happen to default to it."""
        state = {"sprint_number": 2, "critical_halt_notified": True}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context, reason=None):
            tool_context.state["sprint_report"] = f"# Fallback Report (reason={reason})"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        assert mock_ensure.call_args.kwargs.get("reason") == "budget"
        assert result["sprint_report"] == "# Fallback Report (reason=budget)"

    def test_backstop_passes_the_host_cap_reason_when_the_event_cap_was_hit_without_a_critical_halt(self):
        """
        Acceptance Criteria (SPEC-ISSUE-0080, 0.1.0-run78): Sprint 1 hit
        max_events_reached right after accepting its last planned story -
        critical_halt_notified was never set (the team's own token/USD
        budget was never touched), and stop_reason is "max_events_reached",
        not "max_nudges_exhausted" - this must route to "host_cap", not the
        generic "other" bucket whose wording reads like an undiagnosed
        failure for what was actually a fully-successful sprint.
        """
        state = {"sprint_number": 1}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context, reason=None):
            tool_context.state["sprint_report"] = f"# Fallback Report (reason={reason})"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        assert mock_ensure.call_args.kwargs.get("reason") == "host_cap"
        assert result["stop_reason"] == "max_events_reached"
        assert result["sprint_report"] == "# Fallback Report (reason=host_cap)"

    def test_backstop_failure_is_non_fatal(self):
        state = {"critical_halt_notified": True, "sprint_number": 1}
        session_service = _StatefulSessionService(state)

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=RuntimeError("git hiccup")):
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        assert result["stop_reason"] == "max_events_reached"
        assert result["sprint_report"] is None


class _BumpsSprintNumberRunner:
    """Simulates start_sprint actually running mid-turn (mutates the shared
    state dict the same way the real tool call would)."""

    def __init__(self, state):
        self._state = state

    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        self._state["sprint_number"] = self._state.get("sprint_number", 0) + 1
        yield _FakeEvent()


class TestRunOneSprintTeamSprintNumberTracking:
    """
    Acceptance Criteria (SPEC-ISSUE-0080, 0.1.0-run78): the team's own
    sprint_number (bumped only by start_sprint) is a different counter from
    this harness's own per-loop-iteration sprint_number argument. A harness
    iteration that never calls start_sprint (team_sprint_number unchanged
    from the baseline captured at the start of this call) is only finishing
    the PREVIOUS iteration's close-out, not running a genuinely new sprint -
    run_eval_analysis.py needs this distinction surfaced in the manifest to
    stop presenting every harness iteration as an independent sprint.
    """

    def test_reports_continuation_when_start_sprint_was_never_called(self):
        state = {"sprint_number": 1}
        session_service = _StatefulSessionService(state)

        result = _run(_run_one_sprint(
            _NoProgressRunner(), session_service, "app", "user", "session-1",
            "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
        ))

        assert result["team_sprint_number"] == 1
        assert result["team_sprint_is_continuation"] is True

    def test_reports_a_genuinely_new_sprint_when_start_sprint_ran_this_iteration(self):
        state = {"sprint_number": 1}
        session_service = _StatefulSessionService(state)

        result = _run(_run_one_sprint(
            _BumpsSprintNumberRunner(state), session_service, "app", "user", "session-1",
            "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
        ))

        assert result["team_sprint_number"] == 2
        assert result["team_sprint_is_continuation"] is False

    def test_the_very_first_sprint_is_not_a_continuation(self):
        """No previous sprint exists for sprint_number=0 (unset) -> 1 to be
        a continuation of. Uses {"sprint_number": 0} rather than {} as the
        "unset" baseline - _FakeSession.__init__'s `state or {}` would
        silently replace a genuinely empty dict with a brand new one,
        detaching it from the reference _BumpsSprintNumberRunner mutates."""
        state = {"sprint_number": 0}
        session_service = _StatefulSessionService(state)

        result = _run(_run_one_sprint(
            _BumpsSprintNumberRunner(state), session_service, "app", "user", "session-1",
            "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
        ))

        assert result["team_sprint_number"] == 1
        assert result["team_sprint_is_continuation"] is False


class _FakePart:
    def __init__(self, function_response=None):
        self.text = None
        self.function_call = None
        self.function_response = function_response


class _FakeFunctionResponse:
    def __init__(self, name, response):
        self.name = name
        self.response = response


class _FakeContent:
    def __init__(self, parts):
        self.parts = parts


class _FakeEventWithContent:
    def __init__(self, author, parts):
        self.author = author
        self.content = _FakeContent(parts)


class _CreateReleasePrSuccessRunner:
    """Yields one event representing a create_release_pr tool call that
    just succeeded - simulates ProductOwner's mid-sprint recovery after
    start_sprint's own new_sprint_item_blocked gate (SPEC-ISSUE-0058)."""

    async def run_async(self, user_id, session_id, new_message, state_delta=None):
        yield _FakeEventWithContent("ProductOwner", [
            _FakePart(function_response=_FakeFunctionResponse("create_release_pr", {"status": "ok"})),
        ])


class TestRunOneSprintMidSprintReleasePrMerge:
    """
    Acceptance Criteria (SPEC-ISSUE-0058): a create_release_pr call that succeeds
    mid-sprint must have a real chance to actually unblock a subsequent
    start_sprint retry within that same sprint - the harness's merge sweep
    otherwise only runs once, after _run_one_sprint returns, which can never
    help a mid-sprint recovery attempt.
    """

    def test_triggers_a_merge_attempt_when_create_release_pr_succeeds(self):
        with patch("agents.scrum_team.scripts.run_eval._merge_open_prs") as mock_merge:
            mock_merge.return_value = [{"number": 541, "merged": True, "message": "ok"}]
            result = _run(_run_one_sprint(
                _CreateReleasePrSuccessRunner(), _FakeSessionService(), "app", "user", "session-1",
                "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
                local_path="/tmp/fake-repo", base_branch="main",
            ))

        mock_merge.assert_called_once_with("/tmp/fake-repo", "main")
        assert result["mid_sprint_pr_merges"] == [{"number": 541, "merged": True, "message": "ok"}]

    def test_does_not_trigger_without_local_path_and_base_branch(self):
        """Backward compatible: every existing caller/test that doesn't
        pass these must see identical behavior to before this fix."""
        with patch("agents.scrum_team.scripts.run_eval._merge_open_prs") as mock_merge:
            result = _run(_run_one_sprint(
                _CreateReleasePrSuccessRunner(), _FakeSessionService(), "app", "user", "session-1",
                "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_merge.assert_not_called()
        assert result["mid_sprint_pr_merges"] == []

    def test_does_not_trigger_for_an_unrelated_tool_success(self):
        class _UnrelatedToolRunner:
            async def run_async(self, user_id, session_id, new_message, state_delta=None):
                yield _FakeEventWithContent("DevTeam", [
                    _FakePart(function_response=_FakeFunctionResponse("write_file", {"status": "ok"})),
                ])

        with patch("agents.scrum_team.scripts.run_eval._merge_open_prs") as mock_merge:
            _run(_run_one_sprint(
                _UnrelatedToolRunner(), _FakeSessionService(), "app", "user", "session-1",
                "hello", max_events=300, deadline=time.monotonic() + 60, max_nudges=0,
                local_path="/tmp/fake-repo", base_branch="main",
            ))

        mock_merge.assert_not_called()


class TestUnresolvableReleasePrConflict:
    """
    Acceptance Criteria (SPEC-ISSUE-0058): a release PR merge failure must be
    distinguished by whether it's genuinely unresolvable (a real git merge
    conflict - no tool exists to fix this) versus something that might
    still resolve on its own (not yet mergeable, pending checks).
    """

    def test_detects_a_genuine_merge_conflict(self):
        merges = [{
            "number": 541,
            "merged": False,
            "message": "X Pull request .../541 is not mergeable: the merge commit cannot be cleanly created.",
        }]

        assert _unresolvable_release_pr_conflict(merges) == merges[0]

    def test_ignores_a_not_yet_ready_failure(self):
        merges = [{
            "number": 541,
            "merged": False,
            "message": "pull request #541 is not ready for merge: 2 required status checks are pending",
        }]

        assert _unresolvable_release_pr_conflict(merges) is None

    def test_ignores_a_clean_merge(self):
        merges = [{"number": 541, "merged": True, "message": ""}]

        assert _unresolvable_release_pr_conflict(merges) is None

    def test_ignores_an_empty_list(self):
        assert _unresolvable_release_pr_conflict([]) is None


def test_sprint_message_names_start_sprint_explicitly():
    """
    SPEC-ISSUE-0056: a real eval run (0.1.0-run64) never called start_sprint
    again after Sprint 1 - the only place that mechanically resets the
    sprint's own token budget - because the scripted per-sprint message
    never named it at all, unlike create_sprint_report/create_release_pr
    which it already names explicitly. Cheap, falsifiable stopgap: the
    message must name the one call that keeps budget/phase in sync.
    """
    text = _sprint_message(2, 5)

    assert "start_sprint" in text


class TestStartFreshSprintSession:
    """
    Acceptance Criteria (SPEC-ISSUE-0055): starting each new sprint on a fresh
    ADK session - rather than reusing one session's ever-growing event
    history for the whole run - must carry the real state forward (backlog,
    budgets, etc.) while actually dropping the old session, and must read
    the CURRENT state via get_session rather than trusting a stale session
    object reference.
    """

    def test_carries_state_forward_onto_a_new_session_id(self):
        old_session = _FakeSession({"product_backlog": [{"id": "US-0001"}], "sprint_number": 2}, session_id="session-1")
        service = _FullFakeSessionService(old_session.state)

        new_session = _run(_start_fresh_sprint_session(service, "app", "user", old_session))

        assert new_session.id != "session-1"
        assert new_session.state == {"product_backlog": [{"id": "US-0001"}], "sprint_number": 2}

    def test_deletes_the_old_session(self):
        old_session = _FakeSession({"sprint_number": 1}, session_id="session-1")
        service = _FullFakeSessionService(old_session.state)

        _run(_start_fresh_sprint_session(service, "app", "user", old_session))

        assert service.deleted == ["session-1"]

    def test_refetches_state_rather_than_trusting_the_passed_in_session_object(self):
        """ADK's get_session returns a fresh merged copy on every call, not
        a live-updated reference - the stale object created at the start of
        the run never reflects what actually happened in the session since,
        so the fix must re-fetch rather than read session.state directly."""
        stale_session = _FakeSession({"sprint_number": 1}, session_id="session-1")
        service = _FullFakeSessionService({"sprint_number": 2, "product_backlog": ["real progress"]})

        new_session = _run(_start_fresh_sprint_session(service, "app", "user", stale_session))

        assert new_session.state == {"sprint_number": 2, "product_backlog": ["real progress"]}
