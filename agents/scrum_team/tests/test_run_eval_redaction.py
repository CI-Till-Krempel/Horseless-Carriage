"""
Regression coverage for a real secret-leak gap: run_eval.py's own ADK event
capture (events -> manifest.json / transcript.md / the PR comment posted by
_post_sprint_transcript) is a separate path from ScrumState.messages/
transcript, which agent.py already redacts via _redact_secrets. Nothing
previously applied that same redaction here, so a real value returned by a
tool - e.g. the raw key create_litellm_virtual_key hands back
(agents/scrum_team/tools/budget.py) - would flow unredacted into those CI
artifacts and into a public PR comment on the eval target repo.
"""

from agents.scrum_team.scripts.run_eval import _redact_structure


def test_redacts_a_litellm_virtual_key_in_a_tool_response():
    response = {"status": "ok", "agent": "dev", "key": "sk-" + "a" * 32, "max_budget": 0.5}
    redacted = _redact_structure(response)
    assert "sk-" not in str(redacted["key"])
    assert redacted["key"] == "***REDACTED-KEY***"
    # Non-secret fields survive untouched, including the one field
    # _dominant_repeated_rejection/create_release_pr checks for control flow.
    assert redacted["status"] == "ok"
    assert redacted["agent"] == "dev"
    assert redacted["max_budget"] == 0.5


def test_redacts_a_github_token_nested_inside_lists_and_dicts():
    value = {"a": [{"b": "Bearer " + "gh" + "p_" + "1" * 30}]}
    redacted = _redact_structure(value)
    assert "gh" + "p_" not in redacted["a"][0]["b"]


def test_leaves_non_secret_structures_unchanged():
    value = {"path": "README.md", "count": 3, "ok": True, "nested": {"x": None}}
    assert _redact_structure(value) == value


def test_is_deterministic_so_cross_sprint_rejection_comparison_still_works():
    # _dominant_repeated_rejection (test_run_eval_dominant_rejection.py)
    # compares redacted tool-response messages for exact equality, both
    # within a sprint and across sprint boundaries - redaction must produce
    # the same output for the same input every time, or that detector would
    # silently misfire.
    response = {"status": "error", "message": "token sk-" + "b" * 32 + " rejected"}
    assert _redact_structure(response) == _redact_structure(response)
