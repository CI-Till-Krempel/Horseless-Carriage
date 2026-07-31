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
    def test_create_sprint_report_accepts_impediment_alone(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria: an impediment (not just a retro action)
        satisfies the requirement, and is rendered in its own section.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["impediment_log"] = [{"description": "Blocked on X", "owner": "SM", "status": "open"}]

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


if __name__ == "__main__":
    unittest.main()