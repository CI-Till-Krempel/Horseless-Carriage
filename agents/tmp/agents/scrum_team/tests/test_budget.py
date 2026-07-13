# agents/scrum_team/tests/test_budget.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.budget import (
    update_budgets,
    get_budget_status,
    log_token_usage,
    calculate_cost_breakdown,
    recommend_sprint_budget,
    optimize_process_for_budget,
    create_sprint_report,
)
from ..state import ScrumState


class TestBudgetTools(unittest.TestCase):
    def test_update_budgets(self):
        """
        Acceptance Criteria:
        - The total budget is updated.
        - The budget for a specific agent is updated.
        """
        state = ScrumState()
        state = update_budgets(total=100.0, agent_budgets={"ProductOwner": 50.0}, state=state)
        self.assertEqual(state.budgets.total, 100.0)
        self.assertEqual(state.budgets.agents["ProductOwner"], 50.0)

    def test_get_budget_status(self):
        """
        Acceptance Criteria:
        - The budget status is retrieved.
        """
        state = ScrumState()
        state.budgets.total = 100.0
        state.token_usage.total = 50
        status = get_budget_status(state)
        self.assertIn("Total Budget: 100.0", status)
        self.assertIn("Total Usage: 50", status)

    def test_log_token_usage(self):
        """
        Acceptance Criteria:
        - Token usage is logged for a specific agent.
        """
        state = ScrumState()
        state = log_token_usage(agent_name="ProductOwner", tokens=100, state=state)
        self.assertEqual(state.token_usage.agents["ProductOwner"], 100)
        self.assertEqual(state.token_usage.total, 100)

    def test_calculate_cost_breakdown(self):
        """
        Acceptance Criteria:
        - The cost breakdown is calculated correctly.
        """
        state = ScrumState()
        state.token_usage.total = 1000
        state.token_usage.agents = {"DevTeam": 600, "ProductOwner": 200, "ScrumMaster": 200}
        breakdown = calculate_cost_breakdown(state)
        self.assertEqual(breakdown["per_role"], state.token_usage.agents)
        self.assertEqual(breakdown["feature_implementation_percentage"], 60.0)

    def test_recommend_sprint_budget(self):
        """
        Acceptance Criteria:
        - A sprint budget recommendation is returned.
        """
        state = ScrumState()
        recommendation = recommend_sprint_budget(state)
        self.assertIsInstance(recommendation, int)
        self.assertGreater(recommendation, 0)

    @patch("os.getenv")
    def test_optimize_process_for_budget(self, mock_getenv):
        """
        Acceptance Criteria:
        - The process is optimized for a small budget.
        - The process is not optimized for a large budget.
        """
        mock_getenv.return_value = "10.0"
        state = ScrumState()
        state.budgets.total = 100000
        optimizations = optimize_process_for_budget(state)
        self.assertIn("Reduced number of meetings", optimizations)

        state.budgets.total = 300000
        optimizations = optimize_process_for_budget(state)
        self.assertEqual(len(optimizations), 0)

    @patch("os.getenv")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_create_sprint_report(self, mock_open, mock_getenv):
        """
        Acceptance Criteria:
        - The sprint report includes the process overhead percentage.
        """
        mock_getenv.return_value = "15.0"
        state = ScrumState()
        report = create_sprint_report("summary", ["accomplishment"], state)
        self.assertIn("Process Overhead: 15.0%", report)


if __name__ == "__main__":
    unittest.main()