"""
Regression coverage for _run_one_sprint's handling of ADK-internal errors
raised mid-turn. No pytest-asyncio dependency - drives the coroutine
directly via asyncio.run() inside plain sync test functions.
"""

import asyncio
import time

from agents.scrum_team.scripts.run_eval import _run_one_sprint, _sprint_should_abort_run


class _FakeSession:
    def __init__(self, state=None):
        self.state = state or {}


class _FakeSessionService:
    async def get_session(self, app_name, user_id, session_id):
        return _FakeSession()


class _SelfTransferRunner:
    """Simulates ADK's resolve_and_derive_transfer_context rejecting a
    hallucinated agent-transfers-to-itself tool call mid-turn."""

    async def run_async(self, user_id, session_id, new_message, state_delta):
        raise ValueError("Agent 'DevTeam' cannot transfer to itself.")
        yield  # pragma: no cover - never reached; keeps this an async generator


class _OtherValueErrorRunner:
    async def run_async(self, user_id, session_id, new_message, state_delta):
        raise ValueError("some unrelated bug")
        yield  # pragma: no cover


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


def test_sprint_should_not_abort_when_no_critical_halt_occurred():
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "sprint_report_produced"}) is False
    assert _sprint_should_abort_run({"critical_halt": False, "stop_reason": "max_nudges_exhausted"}) is False


def test_sprint_should_not_abort_when_a_critical_halt_still_closed_out_cleanly():
    # ISSUE-0045 / 0.1.0-run25: check_cost_budget_callback's SPRINT CLOSE
    # SEQUENCE grace now redirects a frozen non-grace agent back to
    # ProductOwner (agent.py's _budget_halt_response) instead of leaving the
    # sprint permanently stuck - if that worked and a real sprint report came
    # out the other end, the sprint ended in a clean state and the whole
    # 5-sprint run shouldn't be abandoned over it.
    assert _sprint_should_abort_run({"critical_halt": True, "stop_reason": "sprint_report_produced"}) is False


def test_sprint_should_abort_when_a_critical_halt_left_no_clean_close_out():
    # The pre-fix behavior (and still correct) for the case the grace
    # allowance doesn't cover: a real run previously hit an unrelated
    # transfer-loop crash in the *next* sprint after silently continuing
    # from exactly this kind of unclean state (GH issue #167).
    assert _sprint_should_abort_run({"critical_halt": True, "stop_reason": "max_nudges_exhausted"}) is True
    assert _sprint_should_abort_run({"critical_halt": True, "stop_reason": "max_events_reached"}) is True
