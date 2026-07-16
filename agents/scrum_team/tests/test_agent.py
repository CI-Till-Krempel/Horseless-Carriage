# agents/scrum_team/tests/test_agent.py
import unittest
from unittest.mock import MagicMock, patch

from google.adk.agents.llm_agent import LlmAgent
from agents.scrum_team.agent import (
    product_owner,
    scrum_master,
    dev_team,
    qa_agent,
    architect,
    root_agent,
    check_cost_budget_callback,
    update_token_usage_callback,
    sprint_status_injection_callback,
)
from agents.scrum_team.state import ScrumState


class TestAgent(unittest.TestCase):
    def test_agent_creation(self):
        self.assertIsInstance(product_owner, LlmAgent)
        self.assertIsInstance(scrum_master, LlmAgent)
        self.assertIsInstance(dev_team, LlmAgent)
        self.assertIsInstance(qa_agent, LlmAgent)
        self.assertIsInstance(architect, LlmAgent)
        self.assertIsInstance(root_agent, LlmAgent)

    def test_budget_callbacks(self):
        # Create a mock callback context
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 1000
        state.budgets.total_usd = 10.0
        mock_context.state = state.model_dump()

        # Test check_cost_budget_callback
        mock_llm_request = MagicMock()
        # Mock requests.post to avoid actual API call and master key issues
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"spend": 0.0}]
            mock_post.return_value = mock_response
            
            result = check_cost_budget_callback(mock_context, mock_llm_request)
            self.assertIsNone(result)

        # Test update_token_usage_callback
        mock_llm_response = MagicMock()
        mock_llm_response.usage_metadata.total_token_count = 100
        result = update_token_usage_callback(mock_context, mock_llm_response)
        self.assertIsNone(result)
        self.assertEqual(mock_context.state["token_usage"]["total"], 100)

    def test_sprint_status_injection_callback(self):
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        state.sprint_goal = "Test Goal"
        state.budgets.total = 500000
        state.sprint_backlog = [{"id": "ST-1", "status": "done"}, {"id": "ST-2", "status": "todo"}]
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.previous_interaction_id = None
        mock_llm_request.contents = []

        sprint_status_injection_callback(mock_context, mock_llm_request)

        self.assertEqual(len(mock_llm_request.contents), 1)
        content = mock_llm_request.contents[0]
        self.assertEqual(content.role, "system")
        text = content.parts[0].text
        self.assertIn("Test Goal", text)
        self.assertIn("1/2 items completed", text)
        self.assertIn("500,000 tokens used", text)


if __name__ == "__main__":
    unittest.main()