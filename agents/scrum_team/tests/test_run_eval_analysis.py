"""
Acceptance Criteria (GH issue #125): the rendered evaluation report must
document which Horseless Carriage commit produced the run, so results are
comparable across HC commits/fixes over time, not just across eval-repo
branches.
"""

from agents.scrum_team.scripts.run_eval_analysis import _render_report

_BASE_MANIFEST = {
    "run_id": "0.1.0-run42",
    "branch": "eval/0.1.0-run42/main",
    "model": "scrum-eval-cheap",
    "sprints_requested": 5,
    "sprints": [],
    "pr_merges": [],
    "started_at": "2026-01-01T00:00:00+00:00",
    "finished_at": "2026-01-01T01:00:00+00:00",
}

_JUDGMENT = {
    "code_quality": {"score": 3, "summary": "ok"},
    "requirements_quality": {"score": 3, "summary": "ok"},
    "team_efficiency": {"score": 3, "summary": "ok"},
    "top_problems": [],
}


def test_render_report_includes_hc_commit():
    manifest = dict(_BASE_MANIFEST, hc_commit="abc1234")
    report = _render_report(manifest, _JUDGMENT)
    assert "- Horseless Carriage commit: abc1234" in report


def test_render_report_defaults_hc_commit_to_unknown_when_absent():
    report = _render_report(dict(_BASE_MANIFEST), _JUDGMENT)
    assert "- Horseless Carriage commit: unknown" in report
