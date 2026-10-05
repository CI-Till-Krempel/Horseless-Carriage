# agents/scrum_team/tests/test_budget.py
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.budget import (
    update_budgets,
    get_budget_status,
    log_token_usage,
    log_story_tokens,
    calculate_cost_breakdown,
    recommend_sprint_budget,
    optimize_process_for_budget,
    create_sprint_report,
    render_fallback_sprint_report,
    reset_sprint_budget,
    sprint_budget_reset_state_delta,
    _write_conversation_transcript,
    _file_retro_items_as_issues,
    estimate_sprint_capacity,
    sprint_capacity_advisory,
    _render_retro_doc,
    _render_steering_doc,
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

    def test_reset_sprint_budget_clears_every_grace_and_safety_net_guard(self):
        """
        ISSUE-0049 / 0.1.0-run33: a prior sprint's halt can leave
        critical_halt_notified/sprint_report_safety_net_fired stuck True -
        reset_sprint_budget must clear all of them, not just token_usage,
        or the NEXT sprint's own final halt silently produces no report.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["token_usage"] = {"total": 6032161, "agents": {"DevTeam": 1604179}}
        tool_context.state["budget_exhaustion_synced"] = True
        tool_context.state["budget_reset_since_last_sprint_start"] = False
        tool_context.state["critical_halt_notified"] = True
        tool_context.state["sprint_report_safety_net_fired"] = True
        tool_context.state["sprint_report_path"] = "specs/reports/SPRINT-REPORT-004.md"
        tool_context.state["transcript_path"] = "specs/reports/TRANSCRIPT-004.md"

        reset_sprint_budget(tool_context=tool_context)

        self.assertEqual(tool_context.state["token_usage"], {"total": 0, "agents": {}})
        self.assertFalse(tool_context.state["budget_exhaustion_synced"])
        self.assertTrue(tool_context.state["budget_reset_since_last_sprint_start"])
        self.assertFalse(tool_context.state["critical_halt_notified"])
        self.assertFalse(tool_context.state["sprint_report_safety_net_fired"])
        self.assertEqual(tool_context.state["sprint_report_path"], "", "a new sprint must allocate its own fresh report number, not reuse the previous sprint's")
        self.assertEqual(tool_context.state["transcript_path"], "", "a new sprint must allocate its own fresh transcript number, not reuse the previous sprint's")

    def test_sprint_budget_reset_state_delta_returns_independent_copies(self):
        """Two calls must not share the same nested dict - a caller
        mutating its own copy (e.g. merging in extra keys) must never leak
        into another caller's."""
        first = sprint_budget_reset_state_delta()
        first["token_usage"]["agents"]["DevTeam"] = 999
        second = sprint_budget_reset_state_delta()
        self.assertEqual(second["token_usage"]["agents"], {})

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

    def test_log_story_tokens(self):
        """
        Acceptance Criteria:
        - actual_tokens is recorded alongside the story's existing estimate.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["story_estimates"] = {"US-0001": {"estimate": 120}}
        result = log_story_tokens("US-0001", 110, tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["story_estimates"]["US-0001"]["actual"], 110)
        self.assertEqual(tool_context.state["story_estimates"]["US-0001"]["estimate"], 120)

    def test_log_story_tokens_rejects_value_matching_the_estimate(self):
        """
        Acceptance Criteria (GH issue #211): log_story_tokens must refuse an
        actual_tokens value that exactly equals the story's own estimate - a
        real eval run logged actual_tokens=30 for three different stories,
        each time exactly matching that story's plan_sprint_backlog_item
        estimate, i.e. the estimate copy-pasted back rather than measured.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["story_estimates"] = {"US-0004": {"estimate": 30}}

        result = log_story_tokens("US-0004", 30, tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertNotIn("actual", tool_context.state["story_estimates"]["US-0004"])

    def test_log_story_tokens_allows_a_value_that_differs_from_the_estimate(self):
        """A real, distinct actual value must not trip the GH issue #211
        gate just because it happens to be close to the estimate."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["story_estimates"] = {"US-0004": {"estimate": 30}}

        result = log_story_tokens("US-0004", 29, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["story_estimates"]["US-0004"]["actual"], 29)

    def test_log_story_tokens_allows_any_value_with_no_prior_estimate(self):
        """A story with no recorded estimate has nothing to compare
        against - must not be rejected."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = log_story_tokens("US-0009", 30, tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["story_estimates"]["US-0009"]["actual"], 30)

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
    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-999.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_reuses_this_sprints_already_allocated_path(self, mock_write_file, mock_next_path, mock_getenv):
        """GH issue (0.1.0-run42): if render_fallback_sprint_report already
        fired earlier this sprint (e.g. a transient grace-exhaustion halt
        the sprint then recovered from) and recorded a numbered path, this
        real report must land on that SAME file, not burn a fresh one -
        otherwise the sprint ends up with two committed report files
        (fallback + real) for what should be one."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["sprint_report_path"] = "specs/reports/SPRINT-REPORT-002.md"

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        mock_next_path.assert_not_called()
        self.assertEqual(report["path"], "specs/reports/SPRINT-REPORT-002.md")
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/SPRINT-REPORT-002.md", written_paths)
        self.assertNotIn("specs/reports/SPRINT-REPORT-999.md", written_paths)

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

    @patch.dict(os.environ, {}, clear=False)
    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_warns_on_suspicious_zero_spend(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (GH issue #298): $0.00 actual spend after a lot
        of real token usage almost always means a configured model id isn't
        in LiteLLM's bundled pricing table, not that usage was free - the
        report must flag this rather than let it read as good news.
        """
        os.environ.pop("LLM_LOCAL_PROVIDER", None)
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["budgets"]["total_usd"] = 10.0
        tool_context.state["budgets"]["current_usd_spend"] = 0
        tool_context.state["token_usage"]["total"] = 75_000

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertIn("SAFETY WARNING", report["report"])
        self.assertIn("75,000 tokens", report["report"])

    @patch.dict(os.environ, {}, clear=False)
    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_no_warning_below_token_threshold(self, mock_write_file, mock_getenv):
        """A sprint that just started genuinely has $0 spend and few
        tokens - must not be flagged as suspicious."""
        os.environ.pop("LLM_LOCAL_PROVIDER", None)
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["budgets"]["total_usd"] = 10.0
        tool_context.state["budgets"]["current_usd_spend"] = 0
        tool_context.state["token_usage"]["total"] = 100

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertNotIn("SAFETY WARNING", report["report"])

    @patch.dict(os.environ, {"LLM_LOCAL_PROVIDER": "true"})
    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_no_warning_for_local_provider(self, mock_write_file, mock_getenv):
        """Ollama/local sessions genuinely have no cost to report - $0
        spend there is correct, not a misconfiguration."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["budgets"]["total_usd"] = 10.0
        tool_context.state["budgets"]["current_usd_spend"] = 0
        tool_context.state["token_usage"]["total"] = 75_000

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertNotIn("SAFETY WARNING", report["report"])

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
    def test_create_sprint_report_renders_retro_and_impediment_category(self, mock_write_file, mock_getenv):
        """GH issue #354: category was previously invisible in the sprint
        report even though it drives real mechanical consequences (Issue
        auto-filing, the steering-proposal gate) - a human reviewing the
        report should be able to audit the classification directly."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            # status "resolved" (not "open") so the open-steering-finding gate
            # (which requires a fresh propose_steering_change) doesn't trip -
            # this test is only about rendering, not that other gate.
            {"action": "QA keeps skipping local test runs", "owner": "QA", "status": "resolved", "category": "steering"},
        ]
        tool_context.state["impediment_log"] = [
            {"description": "Need a product decision on auth provider", "owner": "ProductOwner", "status": "open", "category": "human"},
        ]
        tool_context.state["kpi_update_count"] = 1

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("Category: steering", report)
        self.assertIn("Category: human", report)

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
    def test_create_sprint_report_rejects_accomplishments_not_actually_accepted(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (GH issue #210): create_sprint_report must
        refuse to close the sprint if summary/accomplishments claim a
        story is delivered while it hasn't reached Accepted yet - a real
        eval run's Sprint 1 report claimed "Delivered full To-Do List Web
        App MVP ... covering US-0001 through US-0006" while only 1/6 had
        actually reached Accepted.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["product_backlog"] = [
            {"id": "US-0001", "title": "Create List", "stages_completed": ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]},
            {"id": "US-0002", "title": "Add Task", "stages_completed": ["Draft", "Ready"]},
        ]

        result = create_sprint_report(
            "Sprint wrap-up",
            ["Delivered US-0001 through US-0002"],
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("US-0002", result["message"])
        self.assertNotIn("US-0001", result["message"])
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_accepts_claims_that_match_real_state(self, mock_write_file, mock_getenv):
        """A story genuinely Accepted may be claimed as delivered without
        tripping the GH issue #210 gate."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["product_backlog"] = [
            {"id": "US-0001", "title": "Create List", "stages_completed": ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]},
        ]

        result = create_sprint_report("Sprint wrap-up", ["Delivered US-0001"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_escalates_message_after_repeated_overclaim_rejections(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (GH issue #248): the GH issue #210 overclaim
        guard is a literal story-ID substring match, so an agent can dodge
        it by rewording summary/accomplishments without changing the
        underlying (still-undelivered) claim - real eval transcripts show
        exactly this retry-via-rewording pattern, 5/4/2 attempts per
        sprint. After repeated rejections for the same story, the error
        message must make explicit that rewording will not help and name
        the rejected story ID(s), rather than allowing indefinite silent
        retries with the same generic message.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["product_backlog"] = [
            {"id": "US-0002", "title": "Add Task", "stages_completed": ["Draft", "Ready"]},
        ]

        first = create_sprint_report("Sprint 2 successful delivery", ["Delivered US-0002"], tool_context=tool_context)
        second = create_sprint_report("Sprint 2 focused on core work", ["Delivered US-0002"], tool_context=tool_context)
        third = create_sprint_report("Sprint 2 completed by the team", ["Delivered US-0002"], tool_context=tool_context)

        self.assertEqual(first["status"], "error")
        self.assertNotIn("Rewording", first["message"])
        self.assertEqual(second["status"], "error")
        self.assertNotIn("Rewording", second["message"])
        self.assertEqual(third["status"], "error")
        self.assertIn("US-0002", third["message"])
        self.assertIn("Rewording", third["message"])
        self.assertIn("Accepted", third["message"])
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_resets_overclaim_counter_after_success(self, mock_write_file, mock_getenv):
        """Once a report actually succeeds, a later overclaim on the same
        story ID (e.g. next sprint) must start counting from zero again -
        not inherit a stale count left over from a prior, already-resolved
        rejection streak."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["product_backlog"] = [
            {"id": "US-0002", "title": "Add Task", "stages_completed": ["Draft", "Ready"]},
        ]

        create_sprint_report("Sprint 2 attempt one", ["Delivered US-0002"], tool_context=tool_context)
        create_sprint_report("Sprint 2 attempt two", ["Delivered US-0002"], tool_context=tool_context)

        # Succeed without mentioning the undelivered story at all.
        ok = create_sprint_report(
            "Sprint 2 wrap-up, no deliveries this cycle", ["Team focused on infrastructure work"],
            tool_context=tool_context,
        )
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(tool_context.state.get("overclaim_rejection_counts"), {})

        # Fresh retro/kpi signal is required again after a successful report.
        tool_context.state["retro_actions"].append({"action": "test2", "owner": "SM", "status": "open"})
        tool_context.state["kpi_update_count"] += 1

        again = create_sprint_report("Sprint 3 summary", ["Delivered US-0002"], tool_context=tool_context)

        self.assertEqual(again["status"], "error")
        self.assertNotIn("Rewording", again["message"])

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
    def test_create_sprint_report_rejects_open_steering_finding_with_no_fresh_proposal(self, mock_write_file, mock_getenv):
        """GH issue #342: an open "steering"-category retro finding demands
        a fresh propose_steering_change call since the last report, mirroring
        the retro/KPI gates right above - without this, a steering finding
        could be logged once and never actually acted on."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            {"action": "QA keeps skipping local test runs", "owner": "SM", "status": "open", "category": "steering"},
        ]
        tool_context.state["kpi_update_count"] = 1

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("propose_steering_change", result["message"])
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_succeeds_once_steering_change_is_proposed(self, mock_write_file, mock_getenv):
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            {"action": "QA keeps skipping local test runs", "owner": "SM", "status": "open", "category": "steering"},
        ]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["steering_proposal_count"] = 1

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["steering_baseline"], 1)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_not_blocked_by_steering_gate_without_a_steering_finding(self, mock_write_file, mock_getenv):
        """A purely "technical"-category retro action must not trip the
        steering gate at all."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            {"action": "Fix the flaky coverage setup", "owner": "SM", "status": "open", "category": "technical"},
        ]
        tool_context.state["kpi_update_count"] = 1

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

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
    def test_create_sprint_report_renders_per_agent_prompt_context_usage(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (real PR review comment): the percentage of a
        role's model's context window occupied by its concatenated system
        prompt must be tracked on a per-agent basis in the sprint report,
        at full detail (granular per-role numbers, same gating as
        Per-Agent Token Usage) - not rendered at all at coarser levels.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["sprint_report_kpis"] = {
            "team_effectiveness": {"say_do_ratio": 0.8, "commitment_reliability": 1.0},
            "result_quality": {"defect_escape_rate": 0.05, "customer_satisfaction": 4.5},
            "maintainability": {"test_coverage_available": False},
            "security": {"vulnerability_scan_available": False},
            "prompt_context_usage": {
                "ProductOwner": {
                    "model": "scrum-po", "prompt_tokens": 5318,
                    "context_window_tokens": 1048576, "usage_percent": 0.51, "available": True,
                },
                "ScrumMaster": {
                    "model": "scrum-sm", "prompt_tokens": 3092,
                    "context_window_tokens": None, "usage_percent": None, "available": False,
                    "note": "Could not determine this model's context window.",
                },
            },
        }

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("### Per-Agent Prompt Context Usage", report)
        self.assertIn("ProductOwner (scrum-po): 5,318 / 1,048,576 tokens (0.51%)", report)
        self.assertIn("ScrumMaster (scrum-sm): not available", report)

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

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_rejects_when_build_checked_but_no_story_tested(self, mock_write_file, mock_getenv):
        """
        Acceptance Criteria (GH issue #246): create_sprint_report must
        refuse to close the sprint if check_build() last passed and QA has
        left a fresh PR review since the last story reached Tested, but no
        story has actually been advanced to Tested - mirrors the retro/kpi
        gates above. Across every real eval run past Sprint 1, QA's
        check_build() -> gh_pr_comment() -> transfer_to_agent() pattern
        never once included the mandatory advance_story_stage(..., "Tested")
        call, so no story could ever reach Accepted and Velocity flatlined.
        """
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        tool_context.state["pr_review_calls"] = {"QA": 1}
        # qa_review_baseline stays at its default of 0, so pr_review_calls
        # shows a fresh QA review call - and qa_tested_baseline stays 0 with
        # no story anywhere near Tested.

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Tested", result["message"])
        self.assertIn("QA", result["message"])
        mock_write_file.assert_not_called()

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_succeeds_when_qa_advanced_a_story_to_tested(self, mock_write_file, mock_getenv):
        """A story that genuinely reached Tested this interval must satisfy
        the GH issue #246 gate - the normal, correct QA hand-off."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        tool_context.state["pr_review_calls"] = {"QA": 1}
        tool_context.state["product_backlog"] = [
            {"id": "US-0001", "title": "Create List", "stages_completed": ["Draft", "Ready", "Implemented", "Reviewed", "Tested"]},
        ]

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(tool_context.state["qa_tested_baseline"], 1)

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_not_blocked_when_no_build_checked(self, mock_write_file, mock_getenv):
        """No check_build() call has happened at all (last_check_build is
        still its default None) - nothing to gate on, so the sprint report
        must not be blocked waiting on work that was never started."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        # last_check_build and pr_review_calls both stay at their defaults.

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_not_blocked_when_build_is_broken(self, mock_write_file, mock_getenv):
        """A sprint where QA correctly determines the build is broken (and
        so no story is ready to test yet) must not be blocked forever -
        only a *passing* build with no Tested story is evidence of a
        skipped hand-off."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["last_check_build"] = {"checked": "requirements.txt", "passing": False}
        tool_context.state["pr_review_calls"] = {"QA": 1}

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_not_blocked_when_qa_has_not_reviewed_yet(self, mock_write_file, mock_getenv):
        """A passing check_build() with no QA PR review at all yet (e.g. QA
        hasn't been transferred to this sprint) is not evidence of a skipped
        hand-off either - there's no reviewed PR to have advanced."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        # pr_review_calls stays at its default empty dict - qa_review_count
        # (0) is not greater than qa_review_baseline (0).

        result = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)

        self.assertEqual(result["status"], "ok")

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_requires_a_fresh_tested_story_each_sprint(self, mock_write_file, mock_getenv):
        """Same "stale entry from a prior sprint must not satisfy this
        sprint's requirement forever after" property as
        test_create_sprint_report_requires_new_signal_each_sprint, for the
        GH issue #246 QA "Tested" gate specifically."""
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "sprint 1 retro", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["last_check_build"] = {"checked": "requirements.txt", "passing": True}
        tool_context.state["pr_review_calls"] = {"QA": 1}
        tool_context.state["product_backlog"] = [
            {"id": "US-0001", "title": "Create List", "stages_completed": ["Draft", "Ready", "Implemented", "Reviewed", "Tested"]},
        ]

        first = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(tool_context.state["qa_tested_baseline"], 1)

        # Sprint 2: retro/kpi satisfied fresh, QA reviews again and the
        # build still passes, but no NEW story reaches Tested - US-0001 is
        # the same story that already satisfied sprint 1's report.
        tool_context.state["retro_actions"].append({"action": "sprint 2 retro", "owner": "SM", "status": "open"})
        tool_context.state["kpi_update_count"] = 2
        tool_context.state["pr_review_calls"] = {"QA": 2}

        second = create_sprint_report("summary 2", ["accomplishment 2"], tool_context=tool_context)
        self.assertEqual(second["status"], "error")
        self.assertIn("Tested", second["message"])

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

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_lists_blocked_stories(self, mock_write_file):
        """
        Acceptance Criteria: a story still BLOCKED (raise_story_blocker) when
        the sprint closes must show up in the report as an "Open Questions
        for Stakeholder" item, so whoever reads the report can give
        feedback/guidance on it before the next sprint - the mechanical
        hand-off point between "the team couldn't resolve it this sprint"
        and human review.
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


class TestEstimateSprintCapacity(unittest.TestCase):
    """Acceptance Criteria (GH issue #294): the historical actual-tokens-
    per-story rate, computed from every story_estimates[*].actual logged so
    far - independent of token_usage/sprint_backlog, which reset per sprint."""

    def test_none_when_nothing_logged_yet(self):
        self.assertIsNone(estimate_sprint_capacity({}))
        self.assertIsNone(estimate_sprint_capacity({"story_estimates": {}}))

    def test_none_when_only_estimates_exist_with_no_actuals(self):
        s = {"story_estimates": {"US-0001": {"estimate": 1000}}}
        self.assertIsNone(estimate_sprint_capacity(s))

    def test_averages_actuals_across_stories(self):
        s = {"story_estimates": {
            "US-0001": {"estimate": 1000, "actual": 800},
            "US-0002": {"estimate": 1000, "actual": 1200},
        }}
        self.assertEqual(estimate_sprint_capacity(s), 1000)

    def test_ignores_entries_missing_an_actual(self):
        s = {"story_estimates": {
            "US-0001": {"estimate": 1000, "actual": 900},
            "US-0002": {"estimate": 1000},
        }}
        self.assertEqual(estimate_sprint_capacity(s), 900)


class TestSprintCapacityAdvisory(unittest.TestCase):
    """Acceptance Criteria (GH issue #294): advisory-only nudge when the
    planned sprint backlog looks clearly under-sized relative to the token
    budget - the opposite failure mode from _sprint_length_feedback's
    "budget too small" branch."""

    def test_none_with_empty_backlog(self):
        self.assertIsNone(sprint_capacity_advisory({"sprint_backlog": []}))
        self.assertIsNone(sprint_capacity_advisory({}))

    def test_none_when_no_estimate_data_available_at_all(self):
        s = {
            "sprint_backlog": [{"id": "US-0001", "title": "Add login"}],
            "budgets": {"total": 1_000_000},
        }
        self.assertIsNone(sprint_capacity_advisory(s))

    def test_none_when_backlog_already_well_sized(self):
        s = {
            "sprint_backlog": [{"id": "US-0001"}, {"id": "US-0002"}],
            "story_estimates": {
                "US-0001": {"estimate": 400_000},
                "US-0002": {"estimate": 400_000},
            },
            "budgets": {"total": 1_000_000},
        }
        self.assertIsNone(sprint_capacity_advisory(s))

    def test_flags_undersized_backlog_using_explicit_estimates(self):
        s = {
            "sprint_backlog": [{"id": "US-0001"}, {"id": "US-0002"}],
            "story_estimates": {
                "US-0001": {"estimate": 100_000},
                "US-0002": {"estimate": 100_000},
            },
            "budgets": {"total": 1_000_000},
        }
        advisory = sprint_capacity_advisory(s)
        self.assertIsNotNone(advisory)
        self.assertIn("under this sprint's 1,000,000 token budget", advisory)
        self.assertIn("more would fit", advisory)

    def test_falls_back_to_historical_actuals_when_a_story_has_no_estimate(self):
        s = {
            "sprint_backlog": [{"id": "US-0003"}],
            "story_estimates": {
                "US-0001": {"estimate": 100_000, "actual": 100_000},
                "US-0002": {"estimate": 100_000, "actual": 100_000},
                # US-0003 (this sprint's own story) has no estimate at all yet.
            },
            "budgets": {"total": 1_000_000},
        }
        advisory = sprint_capacity_advisory(s)
        self.assertIsNotNone(advisory)
        self.assertIn("1 stories", advisory)

    def test_epics_excluded_from_backlog_size(self):
        s = {
            "sprint_backlog": [{"id": "EP-0001", "type": "Epic"}],
            "budgets": {"total": 1_000_000},
        }
        self.assertIsNone(sprint_capacity_advisory(s))

    def test_ignores_env_budget_fallback_when_state_budget_is_zero_and_none_configured(self):
        s = {
            "sprint_backlog": [{"id": "US-0001"}],
            "story_estimates": {"US-0001": {"estimate": 100}},
            "budgets": {"total": 0},
        }
        with patch.dict("os.environ", {"SPRINT_TOKEN_BUDGET": "1000000"}, clear=True):
            advisory = sprint_capacity_advisory(s)
        self.assertIsNotNone(advisory)


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

    @patch("agents.scrum_team.tools.budget._next_transcript_path", return_value="specs/reports/TRANSCRIPT-999.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_reuses_this_sprints_already_allocated_path(self, mock_write_file, mock_next_path):
        """GH issue #345 (same bug as #341's sprint_report_path fix):
        called unconditionally from both create_sprint_report and
        create_release_pr - without reuse, a single sprint wrote two
        separate numbered TRANSCRIPT-*.md files for what should be one."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["transcript_path"] = "specs/reports/TRANSCRIPT-001.md"
        tool_context.state["transcript"] = [{"agent_name": "DevTeam", "role": "model", "content": "hi"}]

        result = _write_conversation_transcript(tool_context)

        mock_next_path.assert_not_called()
        self.assertEqual(result["path"], "specs/reports/TRANSCRIPT-001.md")
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/TRANSCRIPT-001.md", written_paths)
        self.assertNotIn("specs/reports/TRANSCRIPT-999.md", written_paths)

    @patch("agents.scrum_team.tools.budget._next_transcript_path", return_value="specs/reports/TRANSCRIPT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_first_call_this_sprint_allocates_and_records_a_fresh_path(self, mock_write_file, mock_next_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["transcript"] = [{"agent_name": "DevTeam", "role": "model", "content": "hi"}]

        result = _write_conversation_transcript(tool_context)

        mock_next_path.assert_called_once()
        self.assertEqual(tool_context.state["transcript_path"], "specs/reports/TRANSCRIPT-001.md")
        self.assertEqual(result["path"], "specs/reports/TRANSCRIPT-001.md")

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_handles_empty_transcript(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        result = _write_conversation_transcript(tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entries"], 0)
        written_content = mock_write_file.call_args_list[0].args[1]
        self.assertIn("No transcript recorded yet", written_content)

    @patch.dict("os.environ", {"SESSION_ID": "run32"})
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_references_full_untruncated_session_log_path(self, mock_write_file):
        """
        Acceptance Criteria (GH issue #250): state.transcript is a rolling
        window over the whole run (TRANSCRIPT_MAX_ENTRIES in agent.py), so a
        numbered TRANSCRIPT-NNN.md is not guaranteed to hold a sprint's
        complete history. The rendered file must point a reviewer at the
        untruncated per-run session log instead of leaving that to be
        discovered/assumed.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["transcript"] = [
            {"agent_name": "DevTeam", "role": "model", "content": "hi"}
        ]

        _write_conversation_transcript(tool_context)

        written_content = mock_write_file.call_args_list[0].args[1]
        self.assertIn("/app/sessions/transcript-run32.log", written_content)
        # The pointer belongs near the top of the file, not buried after
        # the transcript body.
        self.assertLess(
            written_content.index("/app/sessions/transcript-run32.log"),
            written_content.index("## DevTeam"),
        )


class TestRenderFallbackSprintReport(unittest.TestCase):
    """
    Acceptance Criteria: render_fallback_sprint_report is the deterministic,
    non-LLM stand-in _ensure_sprint_report_on_final_halt_once (agent.py)
    calls when the sprint budget runs out before Product Owner ever
    successfully calls the real create_sprint_report - it must render
    something real from state alone (no summary/accomplishments text to
    author), and it must never clobber an already-real report.
    """

    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_renders_from_state_when_no_report_exists_yet(self, mock_write_file, mock_next_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["product_backlog"] = [
            {"id": "US-0001", "title": "Do the thing", "type": "User Story",
             "stages_completed": ["Draft", "Ready", "Implemented"]},
            {"id": "EP-0001", "title": "An epic", "type": "Epic"},
        ]
        tool_context.state["retro_actions"] = [{"action": "improve X", "owner": "SM", "status": "open"}]

        result = render_fallback_sprint_report(tool_context=tool_context)

        self.assertEqual(result["status"], "ok")
        self.assertIn("Automatically Generated Fallback Report", result["report"])
        self.assertIn("US-0001", result["report"])
        self.assertNotIn("EP-0001", result["report"], "epics are not stories - must not appear in Story Status")
        self.assertIn("improve X", result["report"])
        self.assertEqual(tool_context.state["sprint_report"], result["report"])
        self.assertTrue(tool_context.state["sprint_report_pending_release"])
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/SPRINT-REPORT-001.md", written_paths)
        self.assertIn("specs/reports/SPRINT-REPORT-LATEST.md", written_paths)

    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_renders_retro_and_impediment_category(self, mock_write_file, mock_next_path):
        """GH issue #354: category was previously invisible here too."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            {"action": "QA keeps skipping local test runs", "owner": "QA", "status": "open", "category": "steering"},
        ]
        tool_context.state["impediment_log"] = [
            {"description": "Need a product decision on auth provider", "owner": "ProductOwner", "status": "open", "category": "human"},
        ]

        result = render_fallback_sprint_report(tool_context=tool_context)

        self.assertIn("Category: steering", result["report"])
        self.assertIn("Category: human", result["report"])

    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-002.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_reuses_a_real_report_instead_of_overwriting_it(self, mock_write_file, mock_next_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        real_report = "# Sprint Review Report\n\nEverything shipped, authored for real by Product Owner.\n"
        tool_context.state["sprint_report"] = real_report

        result = render_fallback_sprint_report(tool_context=tool_context)

        self.assertEqual(result["report"], real_report)
        self.assertNotIn("Automatically Generated Fallback Report", result["report"])
        # Re-written to disk (in case the real one was never committed), but
        # the pending-release bookkeeping is create_sprint_report's own job,
        # not re-triggered here for content that already existed.
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/SPRINT-REPORT-LATEST.md", written_paths)

    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-002.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_reusing_a_real_report_does_not_burn_a_new_report_number(self, mock_write_file, mock_next_path):
        """GH issue (0.1.0-run42): create_release_pr calls this
        unconditionally to land the report on develop before opening the
        PR - even when create_sprint_report already succeeded this sprint.
        Without reusing the already-allocated path, every single sprint
        wrote a second, duplicate numbered file with identical content,
        turning a 5-sprint run into 12+ SPRINT-REPORT-NNN.md files."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        real_report = "# Sprint Review Report\n\nEverything shipped, authored for real by Product Owner.\n"
        tool_context.state["sprint_report"] = real_report
        tool_context.state["sprint_report_path"] = "specs/reports/SPRINT-REPORT-001.md"

        result = render_fallback_sprint_report(tool_context=tool_context)

        mock_next_path.assert_not_called()
        self.assertEqual(result["path"], "specs/reports/SPRINT-REPORT-001.md")
        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/SPRINT-REPORT-001.md", written_paths)
        self.assertNotIn("specs/reports/SPRINT-REPORT-002.md", written_paths)

    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_first_call_this_sprint_allocates_and_records_a_fresh_path(self, mock_write_file, mock_next_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "improve X", "owner": "SM", "status": "open"}]

        result = render_fallback_sprint_report(tool_context=tool_context)

        mock_next_path.assert_called_once()
        self.assertEqual(tool_context.state["sprint_report_path"], "specs/reports/SPRINT-REPORT-001.md")
        self.assertEqual(result["path"], "specs/reports/SPRINT-REPORT-001.md")

    @patch.dict(os.environ, {}, clear=False)
    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-003.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_warns_on_suspicious_zero_spend(self, mock_write_file, mock_next_path):
        """Same GH issue #298 safety warning as create_sprint_report - the
        fallback path renders straight from state, so it needs its own
        wiring of the shared helper, not automatic coverage from the real
        report's tests."""
        os.environ.pop("LLM_LOCAL_PROVIDER", None)
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["budgets"]["total_usd"] = 10.0
        tool_context.state["budgets"]["current_usd_spend"] = 0
        tool_context.state["token_usage"]["total"] = 75_000

        result = render_fallback_sprint_report(tool_context=tool_context)

        self.assertIn("SAFETY WARNING", result["report"])

    @patch("agents.scrum_team.tools.budget._next_retro_doc_path", return_value="specs/reports/RETRO-001.md")
    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_always_writes_the_retro_doc_but_not_the_steering_doc_when_empty(self, mock_write_file, mock_next_report_path, mock_next_retro_path):
        """GH issue #356: RETRO-NNN.md is written every time regardless;
        STEERING-NNN.md is only written once a real proposal exists."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "improve X", "owner": "SM", "status": "open", "category": "technical"}]

        render_fallback_sprint_report(tool_context=tool_context)

        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/RETRO-001.md", written_paths)
        self.assertFalse(any(p.startswith("specs/reports/STEERING-") for p in written_paths))
        self.assertEqual(tool_context.state["retro_doc_path"], "specs/reports/RETRO-001.md")

    @patch("agents.scrum_team.tools.budget._next_steering_doc_path", return_value="specs/reports/STEERING-001.md")
    @patch("agents.scrum_team.tools.budget._next_retro_doc_path", return_value="specs/reports/RETRO-001.md")
    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-001.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_writes_the_steering_doc_once_a_proposal_exists(self, mock_write_file, mock_next_report_path, mock_next_retro_path, mock_next_steering_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "improve X", "owner": "SM", "status": "open", "category": "technical"}]
        tool_context.state["steering_proposals"] = [{
            "role": "QA", "proposed_by": "ScrumMaster", "rationale": "QA keeps skipping local test runs.",
            "new_content": "# QA\n\nRun the full local test suite before marking Tested.\n",
            "pr_url": "https://github.com/example/example/pull/42", "branch": "steering/qa-identity-20261001000000",
        }]

        render_fallback_sprint_report(tool_context=tool_context)

        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/STEERING-001.md", written_paths)
        self.assertEqual(tool_context.state["steering_doc_path"], "specs/reports/STEERING-001.md")
        steering_call = next(c for c in mock_write_file.call_args_list if c.args[0] == "specs/reports/STEERING-001.md")
        self.assertIn("QA", steering_call.args[1])
        self.assertIn("https://github.com/example/example/pull/42", steering_call.args[1])

    @patch("agents.scrum_team.tools.budget._next_retro_doc_path", return_value="specs/reports/RETRO-002.md")
    @patch("agents.scrum_team.tools.budget._next_sprint_report_path", return_value="specs/reports/SPRINT-REPORT-002.md")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_reuses_the_retro_doc_path_already_allocated_this_sprint(self, mock_write_file, mock_next_report_path, mock_next_retro_path):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_doc_path"] = "specs/reports/RETRO-001.md"
        tool_context.state["retro_actions"] = [{"action": "improve X", "owner": "SM", "status": "open", "category": "technical"}]

        render_fallback_sprint_report(tool_context=tool_context)

        written_paths = [c.args[0] for c in mock_write_file.call_args_list]
        self.assertIn("specs/reports/RETRO-001.md", written_paths)
        mock_next_retro_path.assert_not_called()


class TestRenderRetroDoc(unittest.TestCase):
    """GH issue #356: TEMPLATE-RETRO-ITEM.md's structure, rendered from
    state - a durable, full-detail record alongside the terse one-liners in
    the sprint report itself."""

    def test_empty_state_renders_a_plain_no_data_message(self):
        self.assertIn("No retro actions or impediments logged yet", _render_retro_doc({}))

    def test_renders_retro_action_detail(self):
        s = {
            "retro_actions": [{
                "action": "Keep feature branches synchronized with develop",
                "owner": "DevTeam", "status": "open", "category": "steering",
                "success_metric": "No stale branches at sprint end", "issue_id": "ISSUE-0012",
            }],
            "impediment_log": [],
        }
        doc = _render_retro_doc(s)
        self.assertIn("Keep feature branches synchronized with develop", doc)
        self.assertIn("Category: steering", doc)
        self.assertIn("Owner: DevTeam", doc)
        self.assertIn("No stale branches at sprint end", doc)
        self.assertIn("ISSUE-0012", doc)

    def test_renders_impediment_detail(self):
        s = {
            "retro_actions": [],
            "impediment_log": [{
                "description": "Need a product decision on auth provider",
                "owner": "ProductOwner", "status": "open", "category": "human",
            }],
        }
        doc = _render_retro_doc(s)
        self.assertIn("Need a product decision on auth provider", doc)
        self.assertIn("Category: human", doc)


class TestRenderSteeringDoc(unittest.TestCase):
    """GH issue #356: TEMPLATE-STEERING-PROPOSAL.md's structure - no file
    should be written when nothing has been proposed yet."""

    def test_no_proposals_renders_nothing(self):
        self.assertIsNone(_render_steering_doc({}))

    def test_renders_proposal_detail(self):
        s = {"steering_proposals": [{
            "role": "QA", "proposed_by": "ScrumMaster",
            "rationale": "QA keeps skipping local test runs.",
            "new_content": "# QA\n\nRun the full local test suite before marking Tested.\n",
            "pr_url": "https://github.com/example/example/pull/42",
        }]}
        doc = _render_steering_doc(s)
        self.assertIn("QA", doc)
        self.assertIn("QA keeps skipping local test runs.", doc)
        self.assertIn("https://github.com/example/example/pull/42", doc)
        self.assertIn("Run the full local test suite before marking Tested.", doc)


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

    def test_only_files_technical_category_items(self):
        """GH issue #342: a "steering"/"human" finding isn't a code task the
        team can plan into a sprint - filing it as an Issue here would just
        be a different flavor of the same "never actually acted on"
        failure this function exists to prevent."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [
            {"action": "Fix the flaky coverage setup", "owner": "SM", "status": "open", "category": "technical"},
            {"action": "QA keeps skipping local test runs", "owner": "SM", "status": "open", "category": "steering"},
        ]
        tool_context.state["impediment_log"] = [
            {"description": "Need a product decision on auth provider", "owner": "SM", "status": "open", "category": "human"},
        ]

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            filed = _file_retro_items_as_issues(tool_context)

        self.assertEqual(len(filed), 1)
        self.assertTrue(tool_context.state["retro_actions"][0]["issue_id"])
        self.assertNotIn("issue_id", tool_context.state["retro_actions"][1])
        self.assertNotIn("issue_id", tool_context.state["impediment_log"][0])

    def test_missing_category_defaults_to_technical(self):
        """Backward compatibility: data logged before this triage existed
        has no category field at all - must still get filed as before."""
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["retro_actions"] = [{"action": "pre-existing retro item", "owner": "SM", "status": "open"}]

        with patch.dict("os.environ", {"INTERACTION_LEVEL": "EVAL"}, clear=True):
            filed = _file_retro_items_as_issues(tool_context)

        self.assertEqual(len(filed), 1)


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