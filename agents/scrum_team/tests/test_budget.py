# agents/scrum_team/tests/test_budget.py
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.budget import (
    update_budgets,
    get_budget_status,
    log_token_usage,
    calculate_cost_breakdown,
    recommend_sprint_budget,
    optimize_process_for_budget,
    create_sprint_report,
    _write_conversation_transcript,
    _file_retro_items_as_issues,
)
from agents.scrum_team.state import ScrumState


class TestBudgetTools(unittest.TestCase):
    def test_update_budgets(self):
        """
        Acceptance Criteria:
        - The total budget is updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        update_budgets(total_usd=100.0, tool_context=tool_context)
        self.assertEqual(tool_context.state["budgets"]["total_usd"], 100.0)

    def test_get_budget_status(self):
        """
        Acceptance Criteria:
        - The budget status is retrieved.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["budgets"]["total_usd"] = 100.0
        status = get_budget_status(tool_context=tool_context)
        self.assertEqual(status["budget_status"]["total_usd"], 100.0)

    def test_log_token_usage(self):
        """
        Acceptance Criteria:
        - Token usage is logged for a specific agent.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        log_token_usage(agent_name="ProductOwner", tokens=100, tool_context=tool_context)
        self.assertEqual(tool_context.state["token_usage"]["agents"]["ProductOwner"], 100)
        self.assertEqual(tool_context.state["token_usage"]["total"], 100)

    def test_calculate_cost_breakdown(self):
        """
        Acceptance Criteria:
        - The cost breakdown is calculated correctly.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["token_usage"]["total"] = 1000
        tool_context.state["token_usage"]["agents"] = {"DevTeam": 600, "ProductOwner": 200, "ScrumMaster": 200}
        breakdown = calculate_cost_breakdown(tool_context=tool_context)
        self.assertEqual(breakdown["cost_breakdown"]["per_role"], tool_context.state["token_usage"]["agents"])
        self.assertEqual(breakdown["cost_breakdown"]["feature_implementation_percentage"], 60.0)

    def test_recommend_sprint_budget(self):
        """
        Acceptance Criteria:
        - A sprint budget recommendation is returned.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        recommendation = recommend_sprint_budget(tool_context=tool_context)
        self.assertIsInstance(recommendation["recommended_budget"], float)
        self.assertGreater(recommendation["recommended_budget"], 0)

    @patch("os.getenv")
    def test_optimize_process_for_budget(self, mock_getenv):
        """
        Acceptance Criteria:
        - The process is optimized for a small budget.
        - The process is not optimized for a large budget.
        """
        mock_getenv.return_value = "10.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["budgets"]["total_usd"] = 10.0
        optimizations = optimize_process_for_budget(tool_context=tool_context)
        self.assertIn("Reduced number of meetings", optimizations["process_optimizations"])

        tool_context.state["budgets"]["total_usd"] = 30.0
        optimizations = optimize_process_for_budget(tool_context=tool_context)
        self.assertEqual(len(optimizations["process_optimizations"]), 0)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria:
        - The sprint report includes the process overhead percentage.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)
        self.assertIn("Process Overhead: 15.0%", report["report"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_shows_actual_usd_spend(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (GH issue #111): docs/BUDGET.md documents the
        sprint report as showing actual spend alongside the configured
        ceiling - previously only the ceiling was ever rendered, since the
        live spend value check_cost_budget_callback fetches from the
        LiteLLM proxy was never persisted anywhere the report could read it.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["budgets"]["total_usd"] = 10.0
        tool_context.state["budgets"]["current_usd_spend"] = 3.42

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertIn("USD Budget (LiteLLM): $10.00", report["report"])
        self.assertIn("Actual USD Spend (LiteLLM): $3.42", report["report"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_states_spend_unavailable_before_any_live_check(self, mock_write_file, mock_getenv):
        """No live proxy budget check has run yet this session (e.g. a
        purely local/Ollama sprint, or before the first model call) -
        must say so plainly rather than fabricating a $0.00 spend."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["budgets"]["total_usd"] = 10.0

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertIn("Actual USD Spend (LiteLLM): not yet available", report["report"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_includes_hc_version(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (release process, see RELEASE.md): the sprint
        report is traceable back to the Horseless Carriage version that
        produced it.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["hc_version"] = "0.1.0"
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("Generated by Horseless Carriage v0.1.0", report)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_does_not_fabricate_unknown_hc_version(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (release process edge case): an unrecorded
        hc_version is surfaced honestly, not fabricated as a fake version.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()  # hc_version defaults to "unknown"
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("Horseless Carriage (version unknown)", report)

    @patch("agents.scrum_team.tools.budget._configured_repo_root")
    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_includes_transcript_excerpt(self, mock_write_file, mock_getenv, mock_repo_root):
        """
        Acceptance Criteria (US-0003):
        - The report includes a link to the full transcript location and a
          condensed, per-agent excerpt (most recent turn per agent), not
          just a raw tail-N cut that could omit an earlier agent entirely.
        """
        mock_getenv.return_value = "15.0"
        mock_repo_root.return_value = Path("/fake/repo")
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["transcript"] = [
            {"agent_name": "ProductOwner", "role": "model", "content": "Prioritized the backlog."},
            {"agent_name": "DevTeam", "role": "model", "content": "Implemented the feature."},
            {"agent_name": "DevTeam", "role": "model", "content": "Fixed a bug found in review."},
        ]

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("3 entries", report)
        self.assertIn("specs/reports/TRANSCRIPT-", report)
        self.assertNotIn(".hc/state.json", report)
        self.assertIn("Prioritized the backlog.", report)
        # Only DevTeam's most recent entry should appear, not the superseded one.
        self.assertIn("Fixed a bug found in review.", report)
        self.assertNotIn("Implemented the feature.", report)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_handles_missing_transcript(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (US-0003 edge case):
        - No transcript yet -> report generation still succeeds, noting
          transcript unavailability rather than failing.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()  # transcript defaults to []
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("No transcript available yet", report)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_rejects_without_new_retro_or_impediment(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria: create_sprint_report must refuse to close the
        sprint unless a retro action or impediment was logged since the
        last successful report - a real eval run's Scrum Master went
        un-invoked for 5 sprints straight with nothing catching it.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertNotIn("report", result)
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_rejects_without_fresh_kpi_update(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (ISSUE-0046): create_sprint_report must refuse to
        close the sprint unless QualityGuardian's update_sprint_report was
        called since the last successful report, mirroring the retro gate
        immediately above - across every real eval run before this existed,
        QualityGuardian was never once transferred to (nothing in the SPRINT
        CLOSE SEQUENCE told anyone to), so every KPI trend came back "never
        computed".
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        # Retro satisfied, KPI update deliberately not - kpi_update_count
        # stays at its default of 0.

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("QualityGuardian", result["message"])
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_requires_fresh_kpi_update_each_sprint(self, mock_write_file, mock_getenv):
        """Same "stale entry from a prior sprint must not satisfy this
        sprint's requirement forever after" property as
        test_create_sprint_report_requires_new_signal_each_sprint, for the
        KPI gate specifically."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "sprint 1 retro", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        first = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(tool_context.state["kpi_baseline"], 1)

        # New retro action for sprint 2, but no new KPI update - must still
        # be rejected even though kpi_update_count is non-zero (it's the
        # same stale count that already satisfied sprint 1's report).
        tool_context.state["retro_actions"].append({"action": "sprint 2 retro", "owner": "SM", "status": "open"})
        second = create_sprint_report("summary 2", ["accomplishment 2"], tool_context=tool_context)
        self.assertEqual(second["status"], "error")
        self.assertIn("QualityGuardian", second["message"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_renders_kpi_dashboard(self, mock_write_file, mock_getenv):
        """Acceptance Criteria (ISSUE-0046): the KPI dashboard was computed
        and stored (sprint_report_kpis) but never actually rendered anywhere
        in the report document itself - QUALITY_GUARDIAN_PROMPT's own "YOU
        DO" says to include it, but create_sprint_report's code never did."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["sprint_report_kpis"] = {
            "team_effectiveness": {"say_do_ratio": 0.8, "commitment_reliability": 1.0},
            "result_quality": {"defect_escape_rate": 0.05, "customer_satisfaction": 4.5},
            "maintainability": {"test_coverage_available": True, "test_coverage": 0.9, "tests_run": 10, "tests_failed": 0},
            "security": {"vulnerability_scan_available": True, "vulnerability_scan_results": {"critical": 0}},
        }

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("## KPI Dashboard", report)
        self.assertIn("Say-Do Ratio: 0.8", report)
        self.assertIn("Test Coverage: 0.9", report)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_accepts_impediment_alone(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria: an impediment (not just a retro action)
        satisfies the requirement, and is rendered in its own section.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["impediment_log"] = [{"description": "Blocked on X", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertIn("Blocked on X", result["report"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_requires_new_signal_each_sprint(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria: retro_actions/impediment_log accumulate across
        the whole run, so a stale entry from a prior sprint must not
        trivially satisfy this sprint's requirement forever after.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "sprint 1 retro", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        first = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)
        self.assertEqual(first["status"], "ok")

        # No new retro action added for sprint 2 - must be rejected even
        # though retro_actions is non-empty (it's the same stale entry).
        second = create_sprint_report("summary 2", ["accomplishment 2"], tool_context=tool_context)
        self.assertEqual(second["status"], "error")

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_states_active_interaction_level(self, mock_write_file):
        """
        Acceptance Criteria (interaction levels, see docs/INTERACTION-LEVELS.md): the report is
        stamped with the level that generated it, so it's traceable for a CEO-level human relying
        on it as their only visibility into the sprint.
        """
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            tool_context = MagicMock()
            tool_context.state = ScrumState().model_dump()
            tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
            tool_context.state["kpi_update_count"] = 1

            report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

            self.assertIn("Interaction Level: CEO", report)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_bumps_baseline_for_the_active_levels_approval_type(self, mock_write_file):
        """
        Acceptance Criteria: at the CEO level, closing the sprint report snapshots the count of
        "budget" approvals (not "sprint"), since that's the type advance_story_stage's Implemented
        gate will require next.
        """
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            tool_context = MagicMock()
            tool_context.state = ScrumState().model_dump()
            tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
            tool_context.state["kpi_update_count"] = 1
            tool_context.state["human_approvals"] = [
                {"type": "sprint", "note": "irrelevant at CEO level"},
                {"type": "budget", "note": "approved"},
            ]

            result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(tool_context.state["sprint_approval_baseline"], 1)

    def _report_with_full_content(self, level):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["impediment_log"] = [{"description": "Blocked on X", "owner": "SM", "status": "open"}]
        tool_context.state["story_estimates"] = {"US-0001": {"estimate": 100, "actual": 90}}
        tool_context.state["token_usage"] = {"total": 100, "agents": {"DevTeam": 60, "QA": 40}}
        tool_context.state["transcript"] = [{"agent_name": "DevTeam", "content": "Implemented the thing"}]
        with patch.dict("os.environ", {"INTERACTION_LEVEL": level}, clear=True), \
             patch("agents.scrum_team.tools.docs.write_file"):
            return create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

    def test_create_sprint_report_full_detail_at_product_and_eval_levels(self):
        """
        Acceptance Criteria (interaction levels): Product and EVAL render every section, unchanged
        from the report's original, unconditional behavior.
        """
        for level in ("Product", "EVAL"):
            report = self._report_with_full_content(level)
            self.assertIn("### Per-Agent Token Usage", report)
            self.assertIn("- DevTeam: 60", report)
            self.assertIn("## Retrospective Actions", report)
            self.assertIn("## Impediments", report)
            self.assertIn("## Story Estimates vs Actual Tokens", report)
            self.assertIn("Most recent contribution per agent", report)
            self.assertNotIn("Full Process Detail", report)

    def test_create_sprint_report_business_detail_at_stakeholder_level(self):
        """
        Acceptance Criteria: Stakeholder keeps process/business content (retro, impediments,
        estimates) but drops internal technical numbers (per-agent usage, transcript excerpts).
        """
        report = self._report_with_full_content("Stakeholder")
        self.assertIn("## Retrospective Actions", report)
        self.assertIn("## Impediments", report)
        self.assertIn("## Story Estimates vs Actual Tokens", report)
        self.assertNotIn("### Per-Agent Token Usage", report)
        self.assertNotIn("Most recent contribution per agent", report)
        self.assertIn("## Full Process Detail", report)
        self.assertIn("Per-Agent Token Usage", report.split("## Full Process Detail")[1])

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_lists_blocked_stories(self, mock_write_file):
        """
        Acceptance Criteria: a story still BLOCKED (raise_story_blocker) when
        the sprint closes must show up in the report as an "Open Questions
        for Stakeholder" item, so the Stakeholder can give feedback/guidance
        on it before the next sprint - the mechanical hand-off point between
        "the team couldn't resolve it this sprint" and human review.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["product_backlog"] = [{
            "id": "US-0001",
            "title": "Add login flow",
            "blocked": {
                "question": "Which identity provider should this integrate with?",
                "category": "product",
                "raised_by": "DevTeam",
            },
        }]
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]
        self.assertIn("## Open Questions for Stakeholder", report)
        self.assertIn("US-0001", report)
        self.assertIn("Which identity provider should this integrate with", report)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_states_no_blocked_stories_when_none(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]
        self.assertIn("No stories are currently blocked.", report)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_deduplicates_blocked_stories_across_backlogs(self, mock_write_file):
        """A story blocked in both product_backlog and sprint_backlog (the
        normal case - raise_story_blocker writes both copies) must appear
        only once in the report."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        blocked = {"question": "Which identity provider?", "category": "product", "raised_by": "DevTeam"}
        tool_context.state["product_backlog"] = [{"id": "US-0001", "title": "Add login flow", "blocked": blocked}]
        tool_context.state["sprint_backlog"] = [{"id": "US-0001", "title": "Add login flow", "blocked": blocked}]
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]
        self.assertEqual(report.count("US-0001"), 1)

    def test_create_sprint_report_executive_detail_at_ceo_level(self):
        """
        Acceptance Criteria: CEO gets budget + headline outcomes only - retro/impediment/estimate/
        transcript detail is omitted, with a pointer to where it's still available.
        """
        report = self._report_with_full_content("CEO")
        self.assertNotIn("### Per-Agent Token Usage", report)
        self.assertNotIn("## Retrospective Actions", report)
        self.assertNotIn("## Impediments", report)
        self.assertNotIn("## Story Estimates vs Actual Tokens", report)
        self.assertNotIn("## Conversation Transcript", report)
        self.assertIn("## Budget and Usage", report)
        self.assertIn("Sprint Length Feedback", report)
        self.assertIn("## Full Process Detail", report)
        for section in ("Per-Agent Token Usage", "Retrospective Actions", "Impediments", "Story Estimates vs Actual Tokens", "Conversation Transcript"):
            self.assertIn(section, report.split("## Full Process Detail")[1])


class TestWriteConversationTranscript(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #127): the transcript rendered here is
    the human-readable Markdown artifact that replaces the raw JSON blob
    that used to be written straight into the target repo's git-committed
    .hc/state.json - grouped by agent, including tool calls (not just
    model text) so it documents what actually happened per sub-agent.
    """

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_groups_entries_by_agent_and_includes_tool_calls(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["transcript"] = [
            {"agent_name": "ProductOwner", "role": "model", "content": "Prioritized the backlog."},
            {"agent_name": "DevTeam", "role": "tool_call", "content": "git_push(branch, commit_message)"},
            {"agent_name": "DevTeam", "role": "model", "content": "Implemented the feature."},
        ]

        result = _write_conversation_transcript(tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entries"], 3)
        written_content = mock_write_file.call_args_list[0].args[1]
        self.assertIn("## ProductOwner", written_content)
        self.assertIn("Prioritized the backlog.", written_content)
        self.assertIn("## DevTeam", written_content)
        self.assertIn("git_push(branch, commit_message)", written_content)
        self.assertIn("Implemented the feature.", written_content)
        # ProductOwner's heading must come before DevTeam's (chronological).
        self.assertLess(written_content.index("## ProductOwner"), written_content.index("## DevTeam"))

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_writes_both_numbered_and_latest_paths(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["transcript"] = [{"agent_name": "DevTeam", "role": "model", "content": "hi"}]

        result = _write_conversation_transcript(tool_context)

        self.assertTrue(result["path"].startswith("specs/reports/TRANSCRIPT-"))
        self.assertEqual(result["latest_path"], "specs/reports/TRANSCRIPT-LATEST.md")
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn(result["path"], written_paths)
        self.assertIn(result["latest_path"], written_paths)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_handles_empty_transcript(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _write_conversation_transcript(tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entries"], 0)
        written_content = mock_write_file.call_args_list[0].args[1]
        self.assertIn("No transcript recorded yet", written_content)


class TestFileRetroItemsAsIssues(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #164): retro actions and impediments must
    become real, plannable backlog work (Issues), not just a text log
    nobody ever revisits - the exact failure mode a real eval run hit
    (a broken test setup logged as an impediment, but never turned into a
    prioritized fix, so it silently blocked every later sprint too).
    """

    def test_files_retro_actions_and_impediments_as_issues(self):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "pytest cannot generate coverage", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["impediment_log"] = [{"description": "CI missing coverage plugin", "owner": "SM", "status": "open"}]

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            filed = _file_retro_items_as_issues(tool_context)

        self.assertEqual(len(filed), 2)
        backlog_ids = {item["id"] for item in tool_context.state["product_backlog"]}
        self.assertEqual(set(filed), backlog_ids)
        self.assertTrue(tool_context.state["retro_actions"][0]["issue_id"])
        self.assertTrue(tool_context.state["impediment_log"][0]["issue_id"])

    def test_does_not_refile_an_already_filed_item(self):
        """Retro actions/impediments accumulate across the whole run - a
        second sprint's report closing must not re-file the same entry
        (which would duplicate it in product_backlog) just because it's
        still present in the log."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "same item", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            first = _file_retro_items_as_issues(tool_context)
            second = _file_retro_items_as_issues(tool_context)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(tool_context.state["product_backlog"]), 1)

    def test_product_level_leaves_priority_for_the_human(self):
        """At the "Product" interaction level, which impediments get
        tackled in what priority is the Product Owner's call, not
        something this automation should preempt."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "needs PO triage", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            _file_retro_items_as_issues(tool_context)

        item = tool_context.state["product_backlog"][0]
        self.assertIsNone(item.get("priority"))

    def test_non_product_levels_auto_prioritize_must(self):
        """At every other interaction level, there's no human review step
        to leave the prioritization decision to, so it's auto-prioritized
        instead of risking the same silent-starvation failure that was
        reported."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "auto-prioritize me", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            _file_retro_items_as_issues(tool_context)

        item = tool_context.state["product_backlog"][0]
        self.assertEqual(item.get("priority"), "Must")

    def test_ignores_blank_entries(self):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "  ", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        filed = _file_retro_items_as_issues(tool_context)

        self.assertEqual(filed, [])
        self.assertEqual(tool_context.state["product_backlog"], [])


class TestCreateSprintReportFilesRetroItems(unittest.TestCase):
    """Integration coverage: create_sprint_report itself must trigger the
    auto-filing (GH issue #164), not just the helper in isolation."""

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_report_names_the_filed_issue(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "fix coverage tooling", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        issue_id = tool_context.state["retro_actions"][0]["issue_id"]
        self.assertTrue(issue_id)
        self.assertIn(f"filed as {issue_id}", report)
        self.assertTrue(any(item["id"] == issue_id for item in tool_context.state["product_backlog"]))


if __name__ == "__main__":
    unittest.main()