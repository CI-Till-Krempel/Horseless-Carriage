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
    _start_fresh_sprint_session (ISSUE-0055) in isolation."""

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


def test_sprint_should_not_abort_when_no_critical_halt_occurred():
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "sprint_report_produced"}) is False
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "max_nudges_exhausted"}) is False


def test_sprint_should_not_abort_when_a_critical_halt_still_closed_out_cleanly():
    # ISSUE-0045 / 0.1.0-run25: check_cost_budget_callback's SPRINT CLOSE
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


def test_sprint_should_not_abort_without_a_critical_halt_even_if_release_is_still_pending():
    """A non-critical-halt sprint ending mid-close-out is an entirely
    different (and already-handled) signal - this function only ever fires
    on critical_halt in the first place."""
    assert _sprint_should_abort_run({
        "critical_halt": False, "stop_reason": "sprint_report_produced", "sprint_report_pending_release": True,
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
        state = {"critical_halt_notified": True}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context):
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
        state = {"critical_halt_notified": True}
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
        state = {"critical_halt_notified": True, "sprint_report": "# Stale Report From Last Sprint"}
        session_service = _StatefulSessionService(state)

        def _fake_ensure(tool_context):
            tool_context.state["sprint_report"] = "# Fallback Report"

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=_fake_ensure) as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_called_once()
        assert result["sprint_report"] == "# Fallback Report"

    def test_does_not_call_the_backstop_without_a_critical_halt(self):
        """A sprint that just ran out of max_events without ever hitting a
        budget halt has nothing for this backstop to fix - no report was
        ever going to exist regardless of how the sequence played out."""
        state = {}
        session_service = _StatefulSessionService(state)

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt") as mock_ensure:
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        mock_ensure.assert_not_called()
        assert result["sprint_report"] is None

    def test_backstop_failure_is_non_fatal(self):
        state = {"critical_halt_notified": True}
        session_service = _StatefulSessionService(state)

        with patch("agents.scrum_team.agent._ensure_sprint_report_on_final_halt", side_effect=RuntimeError("git hiccup")):
            result = _run(_run_one_sprint(
                _OneEventThenHaltRunner(), session_service, "app", "user", "session-1",
                "hello", max_events=1, deadline=time.monotonic() + 60, max_nudges=0,
            ))

        assert result["stop_reason"] == "max_events_reached"
        assert result["sprint_report"] is None


class TestStartFreshSprintSession:
    """
    Acceptance Criteria (ISSUE-0055): starting each new sprint on a fresh
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
