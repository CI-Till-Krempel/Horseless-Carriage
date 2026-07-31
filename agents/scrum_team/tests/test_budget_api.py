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
    def test_check_cost_budget_callback_persists_current_spend(self, mock_env_get, mock_post):
        """
        Acceptance Criteria (GH issue #111): the live spend value fetched
        from the LiteLLM proxy must be persisted to state.budgets, not just
        held as a local variable - so create_sprint_report can actually
        show it (previously it never could, since the value was discarded
        as soon as this callback returned).
        """
        def side_effect(key, default=None):
            env = {
                "LITELLM_MASTER_KEY": "test-master-key",
                "LITELLM_PROXY_API_BASE": "http://litellm:4000"
            }
            return env.get(key, default)
        mock_env_get.side_effect = side_effect

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"spend": 5.0}]
        mock_post.return_value = mock_response

        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()
        mock_context.state["budgets"]["total_usd"] = 10.0
        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"

        result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNone(result)
        self.assertEqual(mock_context.state["budgets"]["current_usd_spend"], 5.0)

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

    @patch("requests.post")
    @patch("os.environ.get")
    def test_create_litellm_virtual_key_falls_back_to_deprecated_sprint_usd_budget(self, mock_env_get, mock_post):
        """
        Acceptance Criteria (GH issue #81): with no budget set in state,
        create_litellm_virtual_key's own env fallback must still honor the
        older SPRINT_USD_BUDGET name, not silently use the 10.0 hardcoded
        default - a real regression this call site has (agent.py's
        check_cost_budget_callback already covered separately).
        """
        def side_effect(key, default=None):
            env = {
                "LITELLM_MASTER_KEY": "test-master-key",
                "LITELLM_PROXY_API_BASE": "http://litellm:4000",
                "SPRINT_USD_BUDGET": "2.50",
            }
            return env.get(key, default)
        mock_env_get.side_effect = side_effect

        mock_info = MagicMock()
        mock_info.status_code = 200
        mock_info.json.return_value = []
        mock_new = MagicMock()
        mock_new.status_code = 200
        mock_gen = MagicMock()
        mock_gen.status_code = 200
        mock_gen.json.return_value = {"key": "sk-test-key"}
        mock_post.side_effect = [mock_info, mock_new, mock_gen]

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()  # budgets.total_usd unset - forces the env fallback

        create_litellm_virtual_key("DevTeam", tool_context=tool_context)

        new_call = mock_post.call_args_list[1]
        self.assertEqual(new_call[1]["json"]["max_budget"], 2.50)

    @patch("requests.post")
    @patch("os.environ.get")
    def test_create_litellm_virtual_key_default_models_follow_role_overrides(self, mock_env_get, mock_post):
        """
        Acceptance Criteria (GH issue #155): the eval harness
        (run_eval.py) points every role at scrum-eval-cheap via
        SCRUM_<ROLE>_MODEL env overrides before any specialist agent runs.
        A key generated with no explicit `models` list must reflect those
        overrides (deduplicated) instead of the hardcoded production route
        names, or the generated key can't call the model the agent is
        actually configured to use.
        """
        env = {
            "LITELLM_MASTER_KEY": "test-master-key",
            "LITELLM_PROXY_API_BASE": "http://litellm:4000",
            "SCRUM_PO_MODEL": "scrum-eval-cheap",
            "SCRUM_SM_MODEL": "scrum-eval-cheap",
            "SCRUM_DEV_MODEL": "scrum-eval-cheap",
            "SCRUM_QA_MODEL": "scrum-eval-cheap",
            "SCRUM_ARCH_MODEL": "scrum-eval-cheap",
            "SCRUM_ORCHESTRATOR_MODEL": "scrum-eval-cheap",
            "SCRUM_QUALITY_MODEL": "scrum-eval-cheap",
        }
        mock_env_get.side_effect = lambda key, default=None: env.get(key, default)

        mock_info = MagicMock()
        mock_info.status_code = 200
        mock_info.json.return_value = []
        mock_new = MagicMock()
        mock_new.status_code = 200
        mock_gen = MagicMock()
        mock_gen.status_code = 200
        mock_gen.json.return_value = {"key": "sk-test-key"}
        mock_post.side_effect = [mock_info, mock_new, mock_gen]

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        create_litellm_virtual_key("DevTeam", tool_context=tool_context)

        gen_call = mock_post.call_args_list[2]
        self.assertEqual(gen_call[1]["json"]["models"], ["scrum-eval-cheap"])

    @patch("requests.post")
    @patch("os.environ.get")
    def test_create_litellm_virtual_key_default_models_unchanged_without_overrides(self, mock_env_get, mock_post):
        """Without any SCRUM_<ROLE>_MODEL overrides, the default models
        list must still match the previous hardcoded production route
        names, one per role."""
        env = {
            "LITELLM_MASTER_KEY": "test-master-key",
            "LITELLM_PROXY_API_BASE": "http://litellm:4000",
        }
        mock_env_get.side_effect = lambda key, default=None: env.get(key, default)

        mock_info = MagicMock()
        mock_info.status_code = 200
        mock_info.json.return_value = []
        mock_new = MagicMock()
        mock_new.status_code = 200
        mock_gen = MagicMock()
        mock_gen.status_code = 200
        mock_gen.json.return_value = {"key": "sk-test-key"}
        mock_post.side_effect = [mock_info, mock_new, mock_gen]

        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        create_litellm_virtual_key("DevTeam", tool_context=tool_context)

        gen_call = mock_post.call_args_list[2]
        self.assertEqual(
            gen_call[1]["json"]["models"],
            ["scrum-po", "scrum-sm", "scrum-dev", "scrum-qa", "scrum-arch", "scrum-orchestrator", "scrum-quality"],
        )

if __name__ == "__main__":
    unittest.main()
