"""
Acceptance Criteria (GH issue #125): the rendered evaluation report must
document which Horseless Carriage commit produced the run, so results are
comparable across HC commits/fixes over time, not just across eval-repo
branches.

Acceptance Criteria (GH issue #124): the rendered evaluation report must
also plot Say-Do Ratio, Velocity, Quality, issues fixed, stories
implemented, Test Coverage, and Testplan Scenarios as a time series across
sprints.
"""

from agents.scrum_team.scripts.run_eval_analysis import (
    _kpi_time_series,
    _render_kpi_graphs,
    _render_report,
    _collect_blockers,
    _collect_general_blockers,
    _render_blockers_section,
    _render_stopped_early_callout,
)

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


def _sprint(number, backlog=None, kpis=None):
    return {
        "sprint_number": number,
        "sprint_backlog": backlog or [],
        "sprint_report_kpis": kpis,
    }


def _story(stages_completed, item_type="User Story", acceptance_criteria=None):
    return {
        "type": item_type,
        "stages_completed": stages_completed,
        "acceptance_criteria": acceptance_criteria or [],
    }


class TestKpiTimeSeries:
    def test_derives_velocity_issues_and_stories_from_backlog(self):
        backlog = [
            _story(["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"], "User Story", ["Given a, when b, then c"]),
            _story(["Draft", "Ready", "Implemented"], "User Story"),
            _story(["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"], "Issue", ["Given x, when y, then z", "Given p, when q, then r"]),
        ]
        manifest = {"sprints": [_sprint(1, backlog=backlog)]}

        series = _kpi_time_series(manifest)

        assert series["Velocity (items accepted)"] == [(1, 2)]
        assert series["Issues fixed"] == [(1, 1)]
        # Both User Story items reached "Implemented" (one went on to Accepted,
        # but Implemented is still in its stages_completed) - the Issue item
        # doesn't count here regardless of its own stage.
        assert series["Stories implemented"] == [(1, 2)]
        assert series["Testplan scenarios"] == [(1, 3)]

    def test_stories_implemented_counts_any_non_issue_item_that_reached_implemented(self):
        backlog = [_story(["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"])]
        manifest = {"sprints": [_sprint(1, backlog=backlog)]}

        series = _kpi_time_series(manifest)

        assert series["Stories implemented"] == [(1, 1)]

    def test_sprint_report_kpis_missing_omits_the_data_point(self):
        manifest = {"sprints": [_sprint(1, kpis=None), _sprint(2, kpis={})]}

        series = _kpi_time_series(manifest)

        assert series["Say-Do Ratio"] == []
        assert series["Quality (defect escape rate)"] == []
        assert series["Test Coverage"] == []

    def test_sprint_report_kpis_present_is_captured_per_sprint(self):
        kpis_sprint_1 = {
            "team_effectiveness": {"say_do_ratio": 0.8},
            "result_quality": {"defect_escape_rate": 0.05},
            "maintainability": {"test_coverage": 0.72},
        }
        manifest = {"sprints": [_sprint(1, kpis=kpis_sprint_1), _sprint(2, kpis=None)]}

        series = _kpi_time_series(manifest)

        assert series["Say-Do Ratio"] == [(1, 0.8)]
        assert series["Quality (defect escape rate)"] == [(1, 0.05)]
        assert series["Test Coverage"] == [(1, 0.72)]


class TestRenderKpiGraphs:
    def test_fewer_than_two_sprints_skips_graphs(self):
        manifest = {"sprints": [_sprint(1)]}

        rendered = _render_kpi_graphs(manifest)

        assert "Fewer than 2 completed sprints" in rendered
        assert "```mermaid" not in rendered

    def test_renders_a_mermaid_xychart_per_kpi_with_data(self):
        kpis = {
            "team_effectiveness": {"say_do_ratio": 0.8},
            "result_quality": {"defect_escape_rate": 0.05},
            "maintainability": {"test_coverage": 0.72},
        }
        manifest = {"sprints": [_sprint(1, kpis=kpis), _sprint(2, kpis=kpis)]}

        rendered = _render_kpi_graphs(manifest)

        assert rendered.count("```mermaid") == 7
        assert "xychart-beta" in rendered
        assert '"Sprint 1"' in rendered and '"Sprint 2"' in rendered
        assert "line [0.8, 0.8]" in rendered

    def test_partial_kpi_data_reports_missing_count_and_table_shows_na(self):
        kpis = {"team_effectiveness": {"say_do_ratio": 0.9}}
        manifest = {"sprints": [_sprint(1, kpis=kpis), _sprint(2, kpis=None)]}

        rendered = _render_kpi_graphs(manifest)

        assert "1 of 2 sprints have no data point" in rendered
        assert "| Say-Do Ratio | 0.9 | n/a |" in rendered

    def test_kpi_never_computed_reports_unavailable_instead_of_a_chart(self):
        manifest = {"sprints": [_sprint(1, kpis=None), _sprint(2, kpis=None)]}

        rendered = _render_kpi_graphs(manifest)

        assert "### Say-Do Ratio" in rendered
        assert "No data available for this run" in rendered


def test_render_report_includes_kpi_trends_section():
    manifest = {
        "run_id": "test-run",
        "branch": "eval/test-run/main",
        "model": "scrum-eval-cheap",
        "sprints_requested": 2,
        "sprints": [_sprint(1), _sprint(2)],
        "pr_merges": [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T01:00:00+00:00",
    }

    report = _render_report(manifest, _JUDGMENT)

    assert "## KPI Trends" in report


def _sprint_with_backlog(number, product_backlog=None, sprint_backlog=None, general_blockers=None):
    return {
        "sprint_number": number,
        "product_backlog": product_backlog or [],
        "sprint_backlog": sprint_backlog or [],
        "sprint_report_kpis": None,
        "general_blockers": general_blockers or [],
    }


class TestCollectBlockers:
    def test_empty_with_no_sprints(self):
        assert _collect_blockers({"sprints": []}) == []

    def test_finds_blocked_stories_in_the_last_sprint(self):
        manifest = {
            "sprints": [
                _sprint_with_backlog(1, product_backlog=[{"id": "US-0001"}]),
                _sprint_with_backlog(2, product_backlog=[
                    {"id": "US-0001", "title": "Add login", "blocked": {"category": "technical", "question": "why?", "raised_by": "Architect"}},
                    {"id": "US-0002"},
                ]),
            ]
        }
        blockers = _collect_blockers(manifest)
        assert len(blockers) == 1
        story_id, title, blocked = blockers[0]
        assert story_id == "US-0001"
        assert blocked["category"] == "technical"

    def test_only_looks_at_the_last_sprint_not_earlier_ones(self):
        """product_backlog is the full, cumulative backlog each sprint - an
        earlier sprint's now-resolved blocker must not still show up just
        because it was blocked at some point in the past."""
        manifest = {
            "sprints": [
                _sprint_with_backlog(1, product_backlog=[
                    {"id": "US-0001", "blocked": {"category": "technical", "question": "why?"}},
                ]),
                _sprint_with_backlog(2, product_backlog=[
                    {"id": "US-0001", "blocked": None},  # resolved since sprint 1
                ]),
            ]
        }
        assert _collect_blockers(manifest) == []


class TestCollectGeneralBlockers:
    """GH issue #342: unresolved "human"-category retro/impediment findings
    (add_retro_action/add_impediment's category param) - not tied to any
    one story, unlike _collect_blockers above."""

    def test_empty_with_no_sprints(self):
        assert _collect_general_blockers({"sprints": []}) == []

    def test_finds_unresolved_general_blockers_in_the_last_sprint(self):
        manifest = {"sprints": [_sprint_with_backlog(1, general_blockers=[
            {"description": "Need a product decision", "priority": "high", "resolved": False, "raised_by": "ScrumMaster"},
            {"description": "Already handled", "priority": "normal", "resolved": True, "raised_by": "ScrumMaster"},
        ])]}
        result = _collect_general_blockers(manifest)
        assert len(result) == 1
        assert result[0]["description"] == "Need a product decision"


class TestRenderBlockersSection:
    def test_no_blockers_message_when_nothing_is_blocked(self):
        manifest = {"sprints": [_sprint_with_backlog(1)]}
        rendered = "\n".join(_render_blockers_section(manifest))
        assert "## Blockers" in rendered
        assert "No stories are blocked" in rendered

    def test_lists_each_blocked_story(self):
        manifest = {
            "sprints": [
                _sprint_with_backlog(1, product_backlog=[
                    {"id": "US-0001", "title": "Add login", "blocked": {"category": "product", "question": "which color?", "raised_by": "ProductOwner"}},
                ]),
            ]
        }
        rendered = "\n".join(_render_blockers_section(manifest))
        assert "US-0001" in rendered
        assert "which color?" in rendered
        assert "ProductOwner" in rendered

    def test_lists_general_blockers_alongside_story_blockers(self):
        manifest = {
            "sprints": [
                _sprint_with_backlog(
                    1,
                    product_backlog=[{"id": "US-0001", "blocked": {"category": "technical", "question": "why?", "raised_by": "Architect"}}],
                    general_blockers=[{"description": "Need a product decision on auth provider", "priority": "high", "resolved": False, "raised_by": "ScrumMaster"}],
                ),
            ]
        }
        rendered = "\n".join(_render_blockers_section(manifest))
        assert "US-0001" in rendered
        assert "Need a product decision on auth provider" in rendered
        assert "high priority" in rendered


class TestRenderStoppedEarlyCallout:
    def test_empty_when_the_run_completed_normally(self):
        assert _render_stopped_early_callout({"stopped_early": False}) == []

    def test_surfaces_the_stop_reason_prominently(self):
        manifest = {"stopped_early": True, "stop_reason": "budget_critical_halt", "sprints_requested": 5, "sprints": [1]}
        rendered = "\n".join(_render_stopped_early_callout(manifest))
        assert "⚠️ Evaluation Stopped Early" in rendered
        assert "budget_critical_halt" in rendered
        assert "1" in rendered and "5" in rendered

    def test_surfaces_the_blocked_story_when_present(self):
        manifest = {
            "stopped_early": True,
            "stop_reason": "blocked_needs_human",
            "sprints_requested": 5,
            "sprints": [1],
            "blocked_story": {"id": "US-0003", "blocked": {"category": "product", "question": "which color?", "raised_by": "ProductOwner"}},
        }
        rendered = "\n".join(_render_stopped_early_callout(manifest))
        assert "US-0003" in rendered
        assert "which color?" in rendered

    def test_surfaces_crash_details_when_present(self):
        manifest = {
            "stopped_early": True,
            "stop_reason": "crashed",
            "sprints_requested": 5,
            "sprints": [1],
            "crash_sprint": 2,
            "crash_error": "RateLimitError: rate limited",
        }
        rendered = "\n".join(_render_stopped_early_callout(manifest))
        assert "2" in rendered
        assert "RateLimitError" in rendered

    def test_surfaces_the_general_blocker_when_present(self):
        """GH issue #342: a high-priority 'human'-category retro finding
        stopping the run must be as visible as a BLOCKED story's own stop
        reasons already are."""
        manifest = {
            "stopped_early": True,
            "stop_reason": "human_blocker_unresolved",
            "sprints_requested": 5,
            "sprints": [1],
            "general_blocker": {"description": "Need a product decision on auth provider", "priority": "high", "resolved": False, "raised_by": "ScrumMaster"},
        }
        rendered = "\n".join(_render_stopped_early_callout(manifest))
        assert "human_blocker_unresolved" in rendered
        assert "Need a product decision on auth provider" in rendered


def test_render_report_includes_stopped_early_callout_and_blockers_section():
    manifest = {
        "run_id": "test-run",
        "branch": "eval/test-run/main",
        "model": "scrum-eval-cheap",
        "sprints_requested": 5,
        "stopped_early": True,
        "stop_reason": "budget_critical_halt",
        "sprints": [_sprint_with_backlog(1, product_backlog=[
            {"id": "US-0001", "title": "Add login", "blocked": {"category": "technical", "question": "why?", "raised_by": "Architect"}},
        ])],
        "pr_merges": [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T01:00:00+00:00",
    }

    report = _render_report(manifest, _JUDGMENT)

    assert "## ⚠️ Evaluation Stopped Early" in report
    assert "## Blockers" in report
    assert "US-0001" in report
    # The callout/blockers must appear before the methodology note - the
    # single most important fact about a cancelled run belongs at the top,
    # not buried after everything else.
    assert report.index("Evaluation Stopped Early") < report.index("Methodology note")
