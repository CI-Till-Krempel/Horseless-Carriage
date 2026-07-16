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
        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)
        self.assertIn("Process Overhead: 15.0%", report["report"])

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
        tool_context.state["transcript"] = [
            {"agent_name": "ProductOwner", "role": "model", "content": "Prioritized the backlog."},
            {"agent_name": "DevTeam", "role": "model", "content": "Implemented the feature."},
            {"agent_name": "DevTeam", "role": "model", "content": "Fixed a bug found in review."},
        ]

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("3 entries", report)
        self.assertIn(".hc/state.json", report)
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

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("No transcript available yet", report)


if __name__ == "__main__":
    unittest.main()