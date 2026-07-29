# agents/scrum_team/tests/test_agent.py
import unittest
from unittest.mock import MagicMock, patch

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.base_tool import BaseTool
from agents.scrum_team.agent import (
    product_owner,
    scrum_master,
    dev_team,
    qa_agent,
    architect,
    quality_guardian,
    root_agent,
    check_cost_budget_callback,
    update_token_usage_callback,
    sprint_status_injection_callback,
    on_tool_error_callback,
    _stories_ready_for_next_stage_count,
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
        # A budget-capped virtual key must exist for a non-Orchestrator agent
        # to pass the check at all - see test_check_cost_budget_callback_blocks_
        # agent_without_virtual_key for the case where it's missing.
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
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

    def test_check_cost_budget_callback_blocks_agent_without_virtual_key(self):
        """
        Acceptance Criteria (budget audit ahead of v0.1.0):
        - In proxy mode, a sub-agent with no LiteLLM virtual key is blocked
          rather than silently falling back to an unscoped key whose spend
          the USD check below can't see (see inject_litellm_key_callback's
          fallback to LITELLM_PROXY_API_KEY).
        """
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        state = ScrumState()
        state.budgets.total_usd = 10.0
        mock_context.state = state.model_dump()  # litellm_keys is empty

        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"
        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "test-master-key", "LITELLM_PROXY_API_BASE": "http://litellm:4000"}):
            with patch("requests.post") as mock_post:
                result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNotNone(result)
        self.assertIn("NO BUDGET-CAPPED KEY", result.content.parts[0].text)
        # Blocked before any remote spend check is even attempted.
        mock_post.assert_not_called()

    def test_check_cost_budget_callback_exempts_orchestrator_bootstrap(self):
        """
        The Orchestrator has no virtual key of its own yet either (none
        exist until it runs the setup wizard), but it must be allowed to
        make that first bootstrap call - otherwise no key could ever be
        created.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        state.budgets.total_usd = 10.0
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "test-master-key", "LITELLM_PROXY_API_BASE": "http://litellm:4000"}):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = [{"spend": 0.0}]
                mock_post.return_value = mock_response

                result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNone(result)

    def test_check_cost_budget_callback_no_proxy_configured_skips_key_gate(self):
        """
        Without a LiteLLM proxy configured at all, there's no virtual-key
        mechanism to require - the pre-existing local-token-only check path
        still applies unchanged.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        state = ScrumState()
        state.budgets.total = 1000
        state.budgets.total_usd = 10.0
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        with patch.dict("os.environ", {}, clear=True):
            result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNone(result)

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

    def test_sprint_status_injection_surfaces_process_signals(self):
        """
        Acceptance Criteria (GH issue #58): the Orchestrator's first-message
        menu is picked from concrete state signals, not just sprint/budget
        numbers - product vision, sprint report status, open impediments/
        retro actions, and stories ready for the next pipeline stage must
        all be injected too.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        state.product_vision = "Build a great product"
        state.sprint_report = "Sprint 1 report..."
        state.impediment_log = [{"description": "CI was flaky all week", "owner": "DevTeam", "status": "open"}]
        state.retro_actions = [{"action": "Pair on reviews", "owner": "Architect", "success_metric": "fewer reverts", "status": "open"}]
        state.sprint_backlog = [{"id": "ST-1", "stages_completed": ["Ready"]}]
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.previous_interaction_id = None
        mock_llm_request.contents = []

        sprint_status_injection_callback(mock_context, mock_llm_request)

        text = mock_llm_request.contents[0].parts[0].text
        self.assertIn("Build a great product", text)
        self.assertIn("Sprint Report: already created", text)
        self.assertIn("Open Impediments: 1", text)
        self.assertIn("CI was flaky all week", text)
        self.assertIn("Retro Actions Logged: 1", text)
        self.assertIn("Pair on reviews", text)
        self.assertIn("Stories Ready For Next Pipeline Stage: 1", text)

    def test_sprint_status_injection_defaults_when_nothing_set_yet(self):
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.previous_interaction_id = None
        mock_llm_request.contents = []

        sprint_status_injection_callback(mock_context, mock_llm_request)

        text = mock_llm_request.contents[0].parts[0].text
        self.assertIn("Product Vision: Not yet defined", text)
        self.assertIn("Sprint Report: not yet created this sprint", text)
        self.assertIn("Open Impediments: 0", text)
        self.assertIn("Retro Actions Logged: 0", text)
        self.assertIn("Stories Ready For Next Pipeline Stage: 0", text)

    def test_resolved_impediments_and_retro_actions_are_not_counted_as_open(self):
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        state.impediment_log = [{"description": "Fixed already", "owner": "DevTeam", "status": "resolved"}]
        state.retro_actions = [{"action": "Done already", "owner": "SM", "success_metric": "x", "status": "resolved"}]
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.previous_interaction_id = None
        mock_llm_request.contents = []

        sprint_status_injection_callback(mock_context, mock_llm_request)

        text = mock_llm_request.contents[0].parts[0].text
        self.assertIn("Open Impediments: 0", text)
        self.assertIn("Retro Actions Logged: 0", text)


class TestStoriesReadyForNextStageCount(unittest.TestCase):
    def test_ready_but_not_implemented_counts(self):
        state = ScrumState()
        state.sprint_backlog = [{"id": "ST-1", "stages_completed": ["Ready"]}]
        self.assertEqual(_stories_ready_for_next_stage_count(state), 1)

    def test_fully_accepted_story_does_not_count(self):
        state = ScrumState()
        state.product_backlog = [{"id": "ST-1", "stages_completed": ["Ready", "Implemented", "Reviewed", "Tested", "Accepted"]}]
        self.assertEqual(_stories_ready_for_next_stage_count(state), 0)

    def test_counts_across_both_backlogs(self):
        state = ScrumState()
        state.sprint_backlog = [{"id": "ST-1", "stages_completed": ["Ready"]}]
        state.product_backlog = [{"id": "ST-2", "stages_completed": ["Ready", "Implemented", "Reviewed"]}]
        self.assertEqual(_stories_ready_for_next_stage_count(state), 2)

    def test_story_with_no_stages_completed_does_not_count(self):
        state = ScrumState()
        state.sprint_backlog = [{"id": "ST-1"}]
        self.assertEqual(_stories_ready_for_next_stage_count(state), 0)


class TestOnToolErrorCallback(unittest.TestCase):
    """
    Acceptance Criteria: a sub-agent calling a tool name that isn't in its own
    role's tools=[...] list must not crash the whole ADK run. ADK's own
    dispatch code (google.adk.flows.llm_flows.functions) raises a bare
    ValueError and synthesizes a placeholder BaseTool(description="Tool not
    found") for exactly this case before invoking on_tool_error_callback -
    this is a real production incident (ProductOwner hallucinating
    write_file, which only DevTeam/QualityGuardian have, aborted an entire
    eval run with a raw traceback instead of the agent recovering).
    """

    def test_returns_error_dict_for_tool_not_found_placeholder(self):
        tool = BaseTool(name="write_file", description="Tool not found")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        error = ValueError("Tool 'write_file' not found. Available tools: ...")

        result = on_tool_error_callback(tool, {}, tool_context, error)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertIn("write_file", result["message"])
        self.assertIn("ProductOwner", result["message"])
        self.assertIn("transfer_to_agent", result["message"])

    def test_returns_none_for_a_real_tool_execution_error(self):
        """
        A genuine bug inside an actual tool (its real description, not the
        "Tool not found" placeholder) must still propagate and fail loudly -
        this callback only softens dispatch-time "not found" errors, not
        real exceptions from legitimate tool calls.
        """
        tool = BaseTool(name="write_file", description="Write a file to the repo.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        error = RuntimeError("disk full")

        result = on_tool_error_callback(tool, {}, tool_context, error)

        self.assertIsNone(result)

    def test_registered_on_every_agent(self):
        for agent in (product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian, root_agent):
            self.assertEqual(agent.on_tool_error_callback, on_tool_error_callback)


if __name__ == "__main__":
    unittest.main()