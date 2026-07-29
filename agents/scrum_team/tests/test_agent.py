# agents/scrum_team/tests/test_agent.py
import unittest
from unittest.mock import MagicMock, patch

import requests

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.base_tool import BaseTool
import agents.scrum_team.agent as agent_module
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
    log_tool_invocation_callback,
    _stories_ready_for_next_stage_count,
    ensure_state_initialized_callback,
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

    def test_check_cost_budget_callback_skips_usd_check_for_local_provider(self):
        """
        Acceptance Criteria (GH issue #75): self-hosted Ollama models have no
        real per-token price, so LiteLLM's spend tracking for them is always
        ~$0 and the remote USD check would pass trivially forever. With
        LLM_LOCAL_PROVIDER=true, that check (and its network round-trip) is
        skipped outright - the token budget in step 1 remains the guardrail
        that actually applies.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        state = ScrumState()
        state.budgets.total = 1000
        state.budgets.total_usd = 10.0
        state.litellm_keys["DevTeam"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        with patch.dict("os.environ", {
            "LITELLM_MASTER_KEY": "test-master-key",
            "LITELLM_PROXY_API_BASE": "http://litellm:4000",
            "LLM_LOCAL_PROVIDER": "true",
        }):
            with patch("requests.post") as mock_post:
                result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNone(result)
        mock_post.assert_not_called()
        self.assertTrue(mock_context.state["_local_provider_usd_notice_shown"])

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


class TestLogToolInvocationCallback(unittest.TestCase):
    """
    Acceptance Criteria (chat-visibility follow-up): every tool call must be
    visible somewhere a human watching a foreground session would see it -
    not just in the final model text turn a frontend happens to render.
    ADK's own `adk run` CLI REPL only echoes events with `.text` content, so
    a pure function_call event was previously invisible in CLI mode
    entirely.
    """

    def test_prints_agent_and_tool_name_to_stderr(self):
        tool = BaseTool(name="write_file", description="Write a file to the repo.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"

        with patch("builtins.print") as mock_print:
            result = log_tool_invocation_callback(tool, {"path": "x.py", "content": "..."}, tool_context)

        self.assertIsNone(result)
        mock_print.assert_called_once()
        printed_text = mock_print.call_args[0][0]
        self.assertIn("DevTeam", printed_text)
        self.assertIn("write_file", printed_text)
        self.assertIn("path", printed_text)
        self.assertEqual(mock_print.call_args.kwargs.get("file"), agent_module.sys.stderr)

    def test_does_not_leak_argument_values(self):
        """
        Only argument *names* are logged, never values - tool args can carry
        large file contents or PR bodies, which must not end up dumped into
        logs (noise at best, a leak at worst).
        """
        tool = BaseTool(name="write_file", description="Write a file to the repo.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        secret_value = "SUPER-SECRET-FILE-CONTENT"

        with patch("builtins.print") as mock_print:
            log_tool_invocation_callback(tool, {"content": secret_value}, tool_context)

        printed_text = mock_print.call_args[0][0]
        self.assertNotIn(secret_value, printed_text)

    def test_registered_on_every_agent(self):
        for agent in (product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian, root_agent):
            self.assertEqual(agent.before_tool_callback, log_tool_invocation_callback)


class TestCriticalHaltNotifications(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #53): each budget-halt below is exactly
    the "critical tool error" case an unsupervised run needs pushed to a
    human as a blocking interaction, not just left as a chat message in a
    session nobody may be watching.

    _sync_roadmap_on_exhaustion_once is patched away wherever the halt
    branch under test calls it - it's pre-existing, unrelated behavior
    (syncing/pushing the roadmap once budget's exhausted) that does real
    git operations against whatever _configured_repo_root resolves to;
    neutralizing it here keeps these tests scoped to the notification
    wiring this issue actually adds.
    """

    def test_token_budget_exceeded_records_blocking_interaction(self):
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 100
        state.token_usage.total = 150
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
            result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNotNone(result)
        interactions = mock_context.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "critical_error")
        self.assertIn("TOKEN BUDGET EXCEEDED", interactions[0]["summary"])

    def test_no_usd_budget_configured_records_blocking_interaction(self):
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 1000000
        state.budgets.total_usd = 0.0
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch.dict("os.environ", {"SPRINT_USD_BUDGET": "0"}, clear=True):
            result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNotNone(result)
        interactions = mock_context.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "critical_error")
        self.assertIn("CONFIGURATION ERROR", interactions[0]["summary"])

    def test_usd_budget_exceeded_records_blocking_interaction(self):
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 1000000
        state.budgets.total_usd = 5.0
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "mk", "LITELLM_PROXY_API_BASE": "http://proxy"}, clear=True):
            with patch("requests.post") as mock_post:
                mock_post.return_value.json.return_value = [{"spend": 6.0}]
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNotNone(result)
        interactions = mock_context.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "critical_error")
        self.assertIn("USD BUDGET EXCEEDED", interactions[0]["summary"])

    def test_budget_check_request_exception_records_blocking_interaction(self):
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 1000000
        state.budgets.total_usd = 10.0
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "mk", "LITELLM_PROXY_API_BASE": "http://proxy"}, clear=True):
            with patch("requests.post", side_effect=requests.RequestException("boom")):
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNotNone(result)
        interactions = mock_context.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "critical_error")
        self.assertIn("BUDGET ERROR", interactions[0]["summary"])


class TestEnsureStateInitializedCallback(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #72 - "the orchestrator cannot access the
    config"): config (repo URL, budgets, interaction level) must be loaded
    from the environment/state repo mechanically, before the model ever
    gets a turn - not only if the Orchestrator happens to call
    init_scrum_state() itself first. Registered first in root_agent's
    before_model_callback list, ahead of check_cost_budget_callback, so a
    misconfigured/zero SPRINT_USD_BUDGET can't halt the very first turn
    before init_scrum_state()'s own guardrail (which replaces a 0/negative
    budget with a sane default) ever runs.
    """

    def test_calls_init_scrum_state_on_first_call(self):
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()

        with patch.object(agent_module, "init_scrum_state") as mock_init:
            ensure_state_initialized_callback(mock_context, MagicMock())

        mock_init.assert_called_once_with(tool_context=mock_context)
        self.assertTrue(mock_context.state["_state_auto_initialized"])

    def test_does_not_call_init_scrum_state_again_once_flagged(self):
        state = ScrumState().model_dump()
        state["_state_auto_initialized"] = True
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = state

        with patch.object(agent_module, "init_scrum_state") as mock_init:
            ensure_state_initialized_callback(mock_context, MagicMock())

        mock_init.assert_not_called()

    def test_skips_specialist_agents(self):
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        mock_context.state = ScrumState().model_dump()

        with patch.object(agent_module, "init_scrum_state") as mock_init:
            ensure_state_initialized_callback(mock_context, MagicMock())

        mock_init.assert_not_called()
        self.assertNotIn("_state_auto_initialized", mock_context.state)

    def test_init_scrum_state_failure_does_not_raise_and_still_sets_the_flag(self):
        """A persistently-failing init call (e.g. GitHub App auth down)
        must not be retried on every single turn forever - the flag is set
        regardless of success, same as the rest of this callback chain's
        best-effort error handling."""
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()

        with patch.object(agent_module, "init_scrum_state", side_effect=RuntimeError("boom")):
            ensure_state_initialized_callback(mock_context, MagicMock())  # must not raise

        self.assertTrue(mock_context.state["_state_auto_initialized"])

    def test_registered_first_in_root_agent_before_model_callbacks(self):
        self.assertEqual(root_agent.before_model_callback[0], ensure_state_initialized_callback)


if __name__ == "__main__":
    unittest.main()