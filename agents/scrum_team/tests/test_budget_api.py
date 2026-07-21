import unittest
from unittest.mock import MagicMock, patch
import requests
from agents.scrum_team.agent import check_cost_budget_callback
from agents.scrum_team.tools.budget import create_litellm_virtual_key
from agents.scrum_team.state import ScrumState

class TestBudgetAPI(unittest.TestCase):
    @patch("requests.post")
    @patch("os.environ.get")
    def test_check_cost_budget_callback_success(self, mock_env_get, mock_post):
        # Mock environment
        def side_effect(key, default=None):
            env = {
                "LITELLM_MASTER_KEY": "test-master-key",
                "LITELLM_PROXY_API_BASE": "http://litellm:4000"
            }
            return env.get(key, default)
        mock_env_get.side_effect = side_effect

        # Mock successful POST response for budget info
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"spend": 5.0}]
        mock_post.return_value = mock_response

        # Setup context
        mock_context = MagicMock()
        # ScrumOrchestrator is exempt from the virtual-key gate (see
        # test_agent.py) - this test is about the USD spend check itself.
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()
        mock_context.state["budgets"]["total_usd"] = 10.0
        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"

        # Execute
        result = check_cost_budget_callback(mock_context, mock_llm_request)

        # Verify
        self.assertIsNone(result)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "http://litellm:4000/budget/info")
        self.assertEqual(kwargs["json"], {"budgets": ["scrum-sprint-budget"]})

    @patch("requests.post")
    @patch("os.environ.get")
    def test_check_cost_budget_callback_exceeded(self, mock_env_get, mock_post):
        # Mock environment
        def side_effect(key, default=None):
            env = {
                "LITELLM_MASTER_KEY": "test-master-key",
                "LITELLM_PROXY_API_BASE": "http://litellm:4000"
            }
            return env.get(key, default)
        mock_env_get.side_effect = side_effect

        # Mock successful POST response with spend exceeding budget
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"spend": 15.0}]
        mock_post.return_value = mock_response

        # Setup context
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()
        mock_context.state["budgets"]["total_usd"] = 10.0
        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"

        # Execute
        result = check_cost_budget_callback(mock_context, mock_llm_request)

        # Verify
        self.assertIsNotNone(result)
        self.assertIn("BUDGET EXCEEDED", result.content.parts[0].text)

    @patch("requests.post")
    @patch("os.environ.get")
    def test_create_litellm_virtual_key_new_budget(self, mock_env_get, mock_post):
        # Mock environment
        def side_effect(key, default=None):
            env = {
                "LITELLM_MASTER_KEY": "test-master-key",
                "LITELLM_PROXY_API_BASE": "http://litellm:4000"
            }
            return env.get(key, default)
        mock_env_get.side_effect = side_effect

        # Mock 1: budget info returns empty list (budget doesn't exist)
        # Mock 2: budget new returns success
        # Mock 3: key generate returns success
        mock_info = MagicMock()
        mock_info.status_code = 200
        mock_info.json.return_value = []
        
        mock_new = MagicMock()
        mock_new.status_code = 200
        
        mock_gen = MagicMock()
        mock_gen.status_code = 200
        mock_gen.json.return_value = {"key": "sk-test-key"}
        
        mock_post.side_effect = [mock_info, mock_new, mock_gen]

        # Setup tool context
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        # Execute
        res = create_litellm_virtual_key("DevTeam", tool_context=tool_context)

        # Verify
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["key"], "sk-test-key")
        # Ensure budget/new was called
        new_call = mock_post.call_args_list[1]
        self.assertEqual(new_call[0][0], "http://litellm:4000/budget/new")

if __name__ == "__main__":
    unittest.main()
