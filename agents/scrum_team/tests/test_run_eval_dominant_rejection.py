"""
Regression coverage for GH #409: a sprint whose own token/event budget
reset fresh every scripted harness message could get stuck on the exact
same rejection as the previous sprint, with no sprint report produced by
either - a real eval run (0.1.0-run53) hit this across two sprint
boundaries before it had to be cancelled manually, each reset just giving
the identical stuck pattern a fresh lease on life. _dominant_repeated_
rejection is the content-based signal _main_async checks across sprint
boundaries to catch this instead of resetting and trying again forever.
"""

from agents.scrum_team.scripts.run_eval import _dominant_repeated_rejection


def _event(tool_responses):
    return {"author": "ScrumMaster", "text": None, "tool_calls": [], "tool_responses": tool_responses}


def _error_response(name, message):
    return {"name": name, "response": {"status": "error", "message": message}}


def _ok_response(name):
    return {"name": name, "response": {"status": "ok"}}


def test_returns_none_with_no_events():
    assert _dominant_repeated_rejection({"events": []}) is None
    assert _dominant_repeated_rejection({}) is None


def test_returns_none_when_no_errors_at_all():
    sprint_result = {"events": [_event([_ok_response("create_sprint_report")])]}
    assert _dominant_repeated_rejection(sprint_result) is None


def test_returns_none_below_the_minimum_repeat_count():
    message = "Cannot close the sprint report: 2 retro/impediment findings read like role-behavior..."
    sprint_result = {"events": [
        _event([_error_response("create_sprint_report", message)]),
        _event([_error_response("create_sprint_report", message)]),
    ]}
    assert _dominant_repeated_rejection(sprint_result) is None


def test_returns_the_tool_and_message_once_it_repeats_enough():
    message = "Cannot close the sprint report: 2 retro/impediment findings read like role-behavior..."
    sprint_result = {"events": [
        _event([_error_response("create_sprint_report", message)]),
        _event([_error_response("create_sprint_report", message)]),
        _event([_error_response("create_sprint_report", message)]),
    ]}
    result = _dominant_repeated_rejection(sprint_result)
    assert result == ("create_sprint_report", message[:80])


def test_survives_other_calls_and_distinct_errors_interspersed():
    message = "Cannot close the sprint report: 2 retro/impediment findings read like role-behavior..."
    sprint_result = {"events": [
        _event([_error_response("create_sprint_report", message)]),
        _event([_ok_response("transfer_to_agent")]),
        _event([_error_response("create_sprint_report", message)]),
        _event([_error_response("propose_steering_change", "unrelated, one-off error")]),
        _event([_error_response("create_sprint_report", message)]),
    ]}
    result = _dominant_repeated_rejection(sprint_result)
    assert result == ("create_sprint_report", message[:80])


def test_picks_the_truly_dominant_pattern_among_several():
    frequent = "Cannot close the sprint report: 2 retro/impediment findings read like role-behavior..."
    rare = "overclaimed story US-0001"
    sprint_result = {"events": [
        _event([_error_response("create_sprint_report", frequent)]),
        _event([_error_response("create_sprint_report", frequent)]),
        _event([_error_response("create_sprint_report", frequent)]),
        _event([_error_response("create_sprint_report", frequent)]),
        _event([_error_response("create_sprint_report", rare)]),
        _event([_error_response("create_sprint_report", rare)]),
    ]}
    result = _dominant_repeated_rejection(sprint_result)
    assert result == ("create_sprint_report", frequent[:80])


def test_two_different_tools_with_the_same_message_text_are_tracked_separately():
    message = "something went wrong"
    sprint_result = {"events": [
        _event([_error_response("tool_a", message)]),
        _event([_error_response("tool_b", message)]),
        _event([_error_response("tool_a", message)]),
    ]}
    assert _dominant_repeated_rejection(sprint_result) is None


def test_ignores_malformed_or_non_dict_responses():
    sprint_result = {"events": [
        _event([{"name": "x", "response": "not a dict"}]),
        _event([{"name": "y", "response": None}]),
    ]}
    assert _dominant_repeated_rejection(sprint_result) is None
