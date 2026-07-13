# agents/scrum_team/tests/test_agent.py
import unittest
from unittest.mock import MagicMock, patch

from google.adk.agents.llm_agent import LlmAgent
from ..agent import (
    product_owner,
    scrum_master,
    dev_team,
    qa_agent,
    architect,
    root_agent,
    enforce_budget_callback,
    check_model_budget_callback,
    update_token_usage_callback,
)
from ..state import ScrumState


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
        mock_context.state = ScrumState().dict()

        # Test enforce_budget_callback
        result = enforce_budget_callback(mock_context)
        self.assertIsNone(result)

        # Test check_model_budget_callback
        mock_llm_request = MagicMock()
        result = check_model_budget_callback(mock_context, mock_llm_request)
        self.assertIsNone(result)

        # Test update_token_usage_callback
        mock_llm_response = MagicMock()
        mock_llm_response.usage_metadata.total_token_count = 100
        result = update_token_usage_callback(mock_context, mock_llm_response)
        self.assertIsNone(result)
        self.assertEqual(mock_context.state["token_usage"]["total"], 100)


if __name__ == "__main__":
    unittest.main()