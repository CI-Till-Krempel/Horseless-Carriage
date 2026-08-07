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
    log_tool_result_callback,
    recover_fake_tool_call_callback,
    _stories_ready_for_next_stage_count,
    ensure_state_initialized_callback,
    inject_litellm_key_callback,
    _sync_and_commit_roadmap_on_exhaustion,
)
from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.budget import reset_sprint_budget
from google.genai import types
from google.adk.models.llm_response import LlmResponse


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

    def test_check_cost_budget_callback_falls_back_to_deprecated_sprint_usd_budget(self):
        """
        Acceptance Criteria (GH issue #81): TOTAL_USD_BUDGET is the canonical
        name, but an existing .env still using SPRINT_USD_BUDGET must keep
        enforcing the value it set - not silently fall back to the 10.0
        hardcoded default (a higher, unintended ceiling).
        """
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        state = ScrumState()
        state.budgets.total = 1000
        state.budgets.total_usd = 0.0  # unset in state - forces the env fallback path
        state.litellm_keys["DevTeam"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"
        with patch.dict("os.environ", {
            "LITELLM_MASTER_KEY": "test-master-key",
            "LITELLM_PROXY_API_BASE": "http://litellm:4000",
            "SPRINT_USD_BUDGET": "1.00",
        }, clear=True):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = [{"spend": 2.00}]  # over the deprecated var's 1.00 limit
                mock_post.return_value = mock_response

                result = check_cost_budget_callback(mock_context, mock_llm_request)

        self.assertIsNotNone(result)
        self.assertIn("USD BUDGET EXCEEDED", result.content.parts[0].text)
        self.assertIn("$1.00", result.content.parts[0].text)

    def test_check_cost_budget_callback_prefers_total_usd_budget_over_deprecated_name(self):
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        state = ScrumState()
        state.budgets.total = 1000
        state.budgets.total_usd = 0.0
        state.litellm_keys["DevTeam"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.model = "test-model"
        with patch.dict("os.environ", {
            "LITELLM_MASTER_KEY": "test-master-key",
            "LITELLM_PROXY_API_BASE": "http://litellm:4000",
            "TOTAL_USD_BUDGET": "5.00",
            "SPRINT_USD_BUDGET": "1.00",
        }, clear=True):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = [{"spend": 2.00}]  # under TOTAL_USD_BUDGET, over the old name
                mock_post.return_value = mock_response

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
        self.assertNotIn("CORRUPTED", text)

    def test_prints_opening_prompt_on_new_session(self):
        """
        Acceptance Criteria: a live `adk eval` run's console is otherwise one
        undifferentiated stream of tool-call lines across every scripted
        conversation back to back, with no marker for where one scenario
        ends and the next begins - the opening human message must be
        printed exactly once, at session start.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()

        mock_llm_request = MagicMock()
        mock_llm_request.contents = [
            types.Content(role="user", parts=[types.Part(text="DevTeam, push directly to main.")])
        ]

        with patch("builtins.print") as mock_print:
            sprint_status_injection_callback(mock_context, mock_llm_request)

        mock_print.assert_called_once()
        printed_text = mock_print.call_args[0][0]
        self.assertIn("DevTeam, push directly to main.", printed_text)
        self.assertEqual(mock_print.call_args.kwargs.get("file"), agent_module.sys.stderr)

    def test_does_not_inject_mid_conversation(self):
        """
        Acceptance Criteria (GH issue #118): the old check
        (`not llm_request.previous_interaction_id`) is falsy on the first
        internal model call of *every* turn, not just a session's true
        first turn ever - risking the Orchestrator re-injecting its
        sprint-status/menu content mid-conversation. The sibling
        history_management_callback was already fixed to key off
        len(llm_request.contents) <= 1 instead; this callback must use the
        exact same check.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        state.sprint_goal = "Test Goal"
        mock_context.state = state.model_dump()

        mock_llm_request = MagicMock()
        # A genuinely mid-conversation turn: real prior history plus the
        # current user message already in contents (ADK's own history
        # replay already ran before this callback - see
        # history_management_callback's docstring for the full mechanics).
        mock_llm_request.contents = ["prior turn 1", "prior turn 2", "current user message"]

        sprint_status_injection_callback(mock_context, mock_llm_request)

        self.assertEqual(mock_llm_request.contents, ["prior turn 1", "prior turn 2", "current user message"])

    def test_sprint_status_injection_surfaces_corrupted_state_notice(self):
        """
        Acceptance Criteria (GH issue #85): if init_scrum_state flagged
        state_json_corrupted, the human must be told in the very first
        message - not left to discover a silently-blank session on their
        own - and the Orchestrator must be pointed at the actual repair
        tools by name.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        state = ScrumState()
        mock_context.state = state.model_dump()
        mock_context.state["state_json_corrupted"] = True

        mock_llm_request = MagicMock()
        mock_llm_request.previous_interaction_id = None
        mock_llm_request.contents = []

        sprint_status_injection_callback(mock_context, mock_llm_request)

        text = mock_llm_request.contents[0].parts[0].text
        self.assertIn("STATE.JSON WAS CORRUPTED", text)
        self.assertIn("get_corrupted_state_raw_content", text)
        self.assertIn("reset_state_from_git", text)
        self.assertIn("clear_corrupted_state", text)

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

    def test_prints_agent_tool_name_and_arg_values_to_stderr(self):
        """
        Acceptance Criteria: a real eval run's console log showed only
        argument *names* (e.g. `git_push(branch, commit_message, add_all)`)
        - no way to tell which branch, which story, which agent a given
        call actually concerned without reading the full transcript. Actual
        values must now appear too (truncated - see
        test_truncates_long_argument_values_instead_of_hiding_them).
        """
        tool = BaseTool(name="write_file", description="Write a file to the repo.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"

        with patch("builtins.print") as mock_print:
            result = log_tool_invocation_callback(tool, {"path": "x.py", "content": "short"}, tool_context)

        self.assertIsNone(result)
        mock_print.assert_called_once()
        printed_text = mock_print.call_args[0][0]
        self.assertIn("DevTeam", printed_text)
        self.assertIn("write_file", printed_text)
        self.assertIn('path="x.py"', printed_text)
        self.assertIn('content="short"', printed_text)
        self.assertEqual(mock_print.call_args.kwargs.get("file"), agent_module.sys.stderr)

    def test_shows_the_actual_agent_name_for_transfer_to_agent(self):
        """transfer_to_agent's agent_name is just a normal argument value
        now (see the generic value-formatting above) - a real eval run's
        console was otherwise almost entirely indistinguishable
        `transfer_to_agent(agent_name)` lines."""
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ScrumOrchestrator"
        tool_context.state = ScrumState().model_dump()

        with patch("builtins.print") as mock_print:
            log_tool_invocation_callback(tool, {"agent_name": "QualityGuardian"}, tool_context)

        printed_text = mock_print.call_args[0][0]
        self.assertIn('transfer_to_agent(agent_name="QualityGuardian")', printed_text)

    def test_truncates_long_argument_values_instead_of_hiding_them(self):
        """
        Acceptance Criteria: values are shown to make log lines readable,
        but a tool argument can carry large file contents or PR bodies -
        truncating to TOOL_LOG_ARG_VALUE_MAX_LEN characters caps how much
        of any one value (including a would-be secret) can ever appear,
        without hiding it entirely the way the old names-only behavior did.
        """
        tool = BaseTool(name="write_file", description="Write a file to the repo.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        secret_value = "SUPER-SECRET-FILE-CONTENT-THAT-IS-QUITE-LONG"

        with patch("builtins.print") as mock_print:
            log_tool_invocation_callback(tool, {"content": secret_value}, tool_context)

        printed_text = mock_print.call_args[0][0]
        self.assertNotIn(secret_value, printed_text)
        self.assertIn(secret_value[:agent_module.TOOL_LOG_ARG_VALUE_MAX_LEN], printed_text)
        self.assertIn("...", printed_text)

    def test_registered_on_every_agent(self):
        for agent in (product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian, root_agent):
            self.assertEqual(agent.before_tool_callback, log_tool_invocation_callback)

    def test_appends_a_names_and_values_entry_to_the_shared_transcript(self):
        """
        Acceptance Criteria (GH issue #127): tool calls must show up
        per-subagent in the human-readable Markdown transcript alongside
        model turns - previously only model text was captured in
        state.transcript at all, so every tool call was invisible in any
        persisted record. Matches the console log's own truncated-value
        format (see test_truncates_long_argument_values_instead_of_hiding_them).
        """
        tool = BaseTool(name="git_push", description="Push changes.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        tool_context.state = ScrumState().model_dump()
        secret_value = "SUPER-SECRET-COMMIT-MESSAGE-THAT-IS-QUITE-LONG"

        log_tool_invocation_callback(tool, {"commit_message": secret_value}, tool_context)

        transcript = tool_context.state["transcript"]
        self.assertEqual(len(transcript), 1)
        self.assertEqual(transcript[0]["agent_name"], "DevTeam")
        self.assertEqual(transcript[0]["role"], "tool_call")
        self.assertIn("git_push(commit_message=", transcript[0]["content"])
        self.assertNotIn(secret_value, transcript[0]["content"])


class TestLogToolResultCallback(unittest.TestCase):
    """
    Acceptance Criteria: log_tool_invocation_callback's BEFORE-the-call line
    looks identical whether a call goes on to succeed or fail - a real eval
    run's console gave no way to tell, without reading the full transcript,
    which calls this repo's own code-level gates actually rejected. This
    AfterToolCallback prints a short, distinct warning for any tool response
    shaped {"status": "error", ...} - the convention every tool in
    tools/*.py already follows.
    """

    def test_prints_warning_for_error_response(self):
        tool = BaseTool(name="git_push", description="Push changes.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        response = {"status": "error", "message": "Refusing to push directly to 'main' - it's protected."}

        with patch("builtins.print") as mock_print:
            result = log_tool_result_callback(tool, {"branch": "main"}, tool_context, response)

        self.assertIsNone(result)
        mock_print.assert_called_once()
        printed_text = mock_print.call_args[0][0]
        self.assertIn("DevTeam", printed_text)
        self.assertIn("git_push", printed_text)
        self.assertIn("Refusing to push directly to 'main'", printed_text)
        self.assertEqual(mock_print.call_args.kwargs.get("file"), agent_module.sys.stderr)

    def test_does_not_print_anything_for_a_successful_response(self):
        tool = BaseTool(name="git_push", description="Push changes.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        response = {"status": "ok"}

        with patch("builtins.print") as mock_print:
            result = log_tool_result_callback(tool, {"branch": "feature/x"}, tool_context, response)

        self.assertIsNone(result)
        mock_print.assert_not_called()

    def test_truncates_long_error_messages(self):
        tool = BaseTool(name="create_release_pr", description="Open a release PR.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        long_message = "x" * 500
        response = {"status": "error", "message": long_message}

        with patch("builtins.print") as mock_print:
            log_tool_result_callback(tool, {}, tool_context, response)

        printed_text = mock_print.call_args[0][0]
        self.assertNotIn(long_message, printed_text)
        self.assertIn("x" * agent_module.TOOL_LOG_ERROR_MESSAGE_MAX_LEN, printed_text)

    def test_never_overrides_the_real_tool_response(self):
        """Purely observational - must always return None so the tool's
        genuine response reaches the model unchanged."""
        tool = BaseTool(name="git_push", description="Push changes.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"
        response = {"status": "error", "message": "boom"}

        result = log_tool_result_callback(tool, {}, tool_context, response)

        self.assertIsNone(result)

    def test_ignores_non_dict_responses(self):
        tool = BaseTool(name="some_tool", description="A tool.")
        tool_context = MagicMock()
        tool_context.agent_name = "DevTeam"

        with patch("builtins.print") as mock_print:
            result = log_tool_result_callback(tool, {}, tool_context, None)

        self.assertIsNone(result)
        mock_print.assert_not_called()

    def test_registered_on_every_agent(self):
        for agent in (product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian, root_agent):
            self.assertEqual(agent.after_tool_callback, log_tool_result_callback)


class TestLogToolInvocationCallbackBlocksSelfTransfer(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run against a local model repeatedly
    emitted transfer_to_agent(agent_name=<its own name>) - ADK's own
    transfer resolution (google/adk/workflow/utils/_transfer_utils.py)
    raises a bare ValueError("Agent '...' cannot transfer to itself") for
    exactly this shape, crashing the whole node. Unlike a hallucinated tool
    name (see TestOnToolErrorCallback), this happens in the runner's own
    transfer-resolution step *after* the tool call, so on_tool_error_callback
    never sees it - the only place to intercept it is before the real
    transfer_to_agent tool ever runs.
    """

    def test_blocks_transfer_to_self(self):
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()

        result = log_tool_invocation_callback(tool, {"agent_name": "ProductOwner"}, tool_context)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertIn("ProductOwner", result["message"])

    def test_does_not_block_transfer_to_a_different_agent(self):
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()

        result = log_tool_invocation_callback(tool, {"agent_name": "DevTeam"}, tool_context)

        self.assertIsNone(result)

    def test_repeated_self_transfer_escalates_to_the_loop_breaker(self):
        """
        Acceptance Criteria: a real eval run showed a model retrying the
        exact same blocked self-transfer turn after turn, burning tokens
        until the sprint budget ran out - the small per-call "you are
        already X" correction alone never escalates. _detect_transfer_loop
        must run first even for a self-transfer, so TRANSFER_LOOP_THRESHOLD
        consecutive attempts get the stronger loop-breaker message instead
        of silently repeating forever.
        """
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "Architect"
        tool_context.state = ScrumState().model_dump()

        results = [
            log_tool_invocation_callback(tool, {"agent_name": "Architect"}, tool_context)
            for _ in range(agent_module.TRANSFER_LOOP_THRESHOLD)
        ]

        # Every attempt is blocked, but the LAST one (having hit the
        # threshold) must be the loop-breaker's message, not the plain
        # per-call self-transfer correction.
        self.assertTrue(all(r is not None and r["status"] == "error" for r in results))
        self.assertIn("TRANSFER LOOP DETECTED", results[-1]["message"])

    def test_a_pair_that_already_broke_the_loop_is_blocked_immediately_next_time(self):
        """
        Acceptance Criteria: a real eval run showed a model retrying the
        exact same already-broken self-transfer again right after the
        counter reset to 0 - so it took another full TRANSFER_LOOP_THRESHOLD
        streak to break it a second time, burning the whole call budget in
        repeated bursts instead of actually stopping. Once a pair has broken
        the loop once this session, any further occurrence must be refused
        immediately - not after another fresh streak.
        """
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "Architect"
        tool_context.state = ScrumState().model_dump()

        for _ in range(agent_module.TRANSFER_LOOP_THRESHOLD):
            log_tool_invocation_callback(tool, {"agent_name": "Architect"}, tool_context)

        # A single real (non-transfer) tool call in between - proving this
        # isn't just the ordinary consecutive-streak counter still primed.
        other_tool = BaseTool(name="repo_status", description="Report repo status.")
        log_tool_invocation_callback(other_tool, {}, tool_context)

        result = log_tool_invocation_callback(tool, {"agent_name": "Architect"}, tool_context)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertIn("already broke this exact loop", result["message"])

    def test_transfer_loop_raises_a_story_blocker_for_the_story_in_progress(self):
        """
        Acceptance Criteria: a transfer_to_agent ping-pong has no story ID of
        its own in scope, unlike a stuck advance_story_stage retry - but the
        loop breaker should still turn it into a real BLOCKED story (see
        _current_story_in_progress, agents/scrum_team/tools/requirements.py)
        so one-story-at-a-time ordering can skip it and the team moves on,
        instead of just logging a "stalled" interaction with no story link.
        """
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()
        tool_context.state["product_backlog"] = [{
            "id": "US-0001",
            "title": "Add login flow",
            "type": "User Story",
            "stages_completed": ["Draft", "Ready"],
        }]

        for _ in range(agent_module.TRANSFER_LOOP_THRESHOLD):
            result = log_tool_invocation_callback(tool, {"agent_name": "ScrumMaster"}, tool_context)

        self.assertEqual(result["status"], "error")
        blocked = tool_context.state["product_backlog"][0].get("blocked")
        self.assertIsNotNone(blocked)
        self.assertIn("bounced transfer_to_agent", blocked["question"])

    def test_transfer_loop_falls_back_to_plain_stalled_interaction_with_no_story_in_progress(self):
        """No story in product_backlog (or every one already Accepted/
        BLOCKED) - must still record the original plain "stalled" blocking
        interaction, never silently drop the notification just because the
        richer story-linked path didn't apply."""
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()

        for _ in range(agent_module.TRANSFER_LOOP_THRESHOLD):
            result = log_tool_invocation_callback(tool, {"agent_name": "ScrumMaster"}, tool_context)

        self.assertEqual(result["status"], "error")
        interactions = tool_context.state.get("blocking_interactions", [])
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "stalled")


class TestLogToolInvocationCallbackBlocksRepeatedCalls(unittest.TestCase):
    """
    Acceptance Criteria: real eval runs showed non-transfer tools stuck in
    the same kind of unproductive loop transfer_to_agent already had a
    breaker for - QualityGuardian calling calculate_kpis()/
    update_sprint_report(kpis=...) back to back a dozen+ times even after
    each call *succeeded*, and ProductOwner calling
    advance_story_stage(title_or_id="US-0006", stage="Ready") with
    identical arguments repeatedly after the same rejection every time.
    _detect_repeated_call_loop must catch the exact-same-tool-exact-same-
    args case the same way _detect_transfer_loop catches the transfer case.
    """

    def test_blocks_identical_repeated_calls(self):
        tool = BaseTool(name="update_sprint_report", description="Update the sprint report.")
        tool_context = MagicMock()
        tool_context.agent_name = "QualityGuardian"
        tool_context.state = ScrumState().model_dump()

        results = [
            log_tool_invocation_callback(tool, {"kpis": "calculate_kpis"}, tool_context)
            for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD)
        ]

        self.assertTrue(all(r is None for r in results[:-1]))
        self.assertEqual(results[-1]["status"], "error")
        self.assertIn("REPEATED CALL DETECTED", results[-1]["message"])

    def test_a_signature_that_already_broke_the_loop_is_blocked_immediately_next_time(self):
        """
        Acceptance Criteria: a real eval run showed QualityGuardian retrying
        the exact same already-broken update_sprint_report call again right
        after the counter reset to 0 - so it took another full
        REPEATED_CALL_LOOP_THRESHOLD streak to break it a second time,
        burning the whole call budget in repeated bursts. Once this exact
        tool+args combination has broken the loop once this session, any
        further occurrence must be refused immediately.
        """
        tool = BaseTool(name="update_sprint_report", description="Update the sprint report.")
        tool_context = MagicMock()
        tool_context.agent_name = "QualityGuardian"
        tool_context.state = ScrumState().model_dump()

        for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD):
            log_tool_invocation_callback(tool, {"kpis": "calculate_kpis"}, tool_context)

        # A single distinct call in between - proving this isn't just the
        # ordinary consecutive-streak counter still primed.
        log_tool_invocation_callback(tool, {"kpis": "calculate_kpis()"}, tool_context)

        result = log_tool_invocation_callback(tool, {"kpis": "calculate_kpis"}, tool_context)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertIn("already broke this exact", result["message"])

    def test_does_not_block_when_arguments_differ(self):
        """A different story/branch/etc each call is real, distinct
        progress, even when the tool name repeats - must never be blocked."""
        tool = BaseTool(name="advance_story_stage", description="Advance a story's stage.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()

        results = [
            log_tool_invocation_callback(tool, {"title_or_id": f"US-000{i}", "stage": "Ready"}, tool_context)
            for i in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD + 2)
        ]

        self.assertTrue(all(r is None for r in results))

    def test_repeated_call_loop_raises_a_story_blocker_when_args_name_a_story(self):
        """
        Acceptance Criteria: a stuck advance_story_stage retry (this class's
        own docstring cites the exact real-eval case -
        advance_story_stage(title_or_id="US-0006", stage="Ready") repeated
        after the same rejection) names a story right in its own args -
        unlike the transfer-loop case, no separate lookup is needed. The
        loop breaker should turn this into a real BLOCKED story instead of
        just a "stalled" interaction with no story link.
        """
        tool = BaseTool(name="advance_story_stage", description="Advance a story's stage.")
        tool_context = MagicMock()
        tool_context.agent_name = "ProductOwner"
        tool_context.state = ScrumState().model_dump()
        tool_context.state["product_backlog"] = [{
            "id": "US-0006",
            "title": "Add checkout flow",
            "type": "User Story",
            "stages_completed": ["Draft"],
        }]

        result = None
        for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD):
            result = log_tool_invocation_callback(tool, {"title_or_id": "US-0006", "stage": "Ready"}, tool_context)

        self.assertEqual(result["status"], "error")
        blocked = tool_context.state["product_backlog"][0].get("blocked")
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["category"], "product")
        self.assertIn("repeated advance_story_stage", blocked["question"])

    def test_repeated_call_loop_falls_back_to_plain_stalled_interaction_with_no_story_named(self):
        """calculate_kpis()-style calls have no title_or_id at all - must
        still record the plain "stalled" interaction, same as before this
        story-linking existed."""
        tool = BaseTool(name="update_sprint_report", description="Update the sprint report.")
        tool_context = MagicMock()
        tool_context.agent_name = "QualityGuardian"
        tool_context.state = ScrumState().model_dump()

        result = None
        for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD):
            result = log_tool_invocation_callback(tool, {"kpis": "calculate_kpis"}, tool_context)

        self.assertEqual(result["status"], "error")
        interactions = tool_context.state.get("blocking_interactions", [])
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["kind"], "stalled")

    def test_a_different_tool_call_in_between_resets_the_streak(self):
        tool = BaseTool(name="calculate_kpis", description="Calculate KPIs.")
        other_tool = BaseTool(name="upsert_issue", description="Add or update an issue.")
        tool_context = MagicMock()
        tool_context.agent_name = "QualityGuardian"
        tool_context.state = ScrumState().model_dump()

        results = []
        for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD - 1):
            results.append(log_tool_invocation_callback(tool, {}, tool_context))
        results.append(log_tool_invocation_callback(other_tool, {"issue": {"title": "x"}}, tool_context))
        for _ in range(agent_module.REPEATED_CALL_LOOP_THRESHOLD - 1):
            results.append(log_tool_invocation_callback(tool, {}, tool_context))

        self.assertTrue(all(r is None for r in results))

    def test_transfer_to_agent_calls_are_not_subject_to_this_breaker(self):
        """transfer_to_agent has its own dedicated, pair-based breaker
        (TRANSFER_LOOP_THRESHOLD, see
        TestLogToolInvocationCallbackBlocksSelfTransfer) - _detect_repeated_
        call_loop must never additionally run for it. Stays under
        TRANSFER_LOOP_THRESHOLD so a block here can only be this (wrong)
        breaker firing, not the transfer one."""
        tool = BaseTool(name="transfer_to_agent", description="Transfer to another agent.")
        tool_context = MagicMock()
        tool_context.agent_name = "ScrumOrchestrator"
        tool_context.state = ScrumState().model_dump()

        results = [
            log_tool_invocation_callback(tool, {"agent_name": "DevTeam"}, tool_context)
            for _ in range(agent_module.TRANSFER_LOOP_THRESHOLD - 1)
        ]

        self.assertTrue(all(r is None for r in results))


class TestRecoverFakeToolCallCallback(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #89): a model reply that's plain TEXT
    shaped exactly like a tool call - `{"type": "function", "function":
    "<name>", "arguments": {...}}` - instead of a real ADK function_call
    part must be converted into an actual function_call part, mechanically,
    since a text warning alone did not reliably get a real model to
    self-correct (one reported session hit this 8 times in a row). Must
    never touch a genuine prose reply, even one that happens to be valid
    JSON or mentions a tool by name.
    """

    def _response_with_text(self, text):
        return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))

    def test_converts_exact_fake_tool_call_shape(self):
        response = self._response_with_text('{"type": "function", "function": "repo_status", "arguments": {}}')
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        parts = response.content.parts
        self.assertEqual(len(parts), 1)
        self.assertIsNone(parts[0].text)
        self.assertEqual(parts[0].function_call.name, "repo_status")
        self.assertEqual(parts[0].function_call.args, {})

    def test_converts_with_name_and_args_keys(self):
        """Some models use "name"/"args" instead of "function"/"arguments" -
        both key spellings must be recovered."""
        response = self._response_with_text('{"type": "function", "name": "start_sprint", "args": {"goal": "Refine the MVP scope"}}')
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "start_sprint")
        self.assertEqual(fc.args, {"goal": "Refine the MVP scope"})

    def test_converts_nested_function_object_shape(self):
        """
        Acceptance Criteria: a live eval run produced `{"type": "function",
        "function": {"name": "transfer_to_agent", "arguments": {...}}}` -
        "function" as a nested object (name/arguments one level deeper)
        rather than the tool name string directly - which the original
        exact-key match missed entirely, scoring that eval case a hard 0.
        """
        response = self._response_with_text(
            '{"type": "function", "function": {"name": "transfer_to_agent", "arguments": {"agent_name": "DevTeam"}}}'
        )
        callback_context = MagicMock()
        callback_context.agent_name = "ProductOwner"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "transfer_to_agent")
        self.assertEqual(fc.args, {"agent_name": "DevTeam"})

    def test_converts_function_name_key_shape(self):
        """
        Acceptance Criteria: a live eval run also produced
        `{"function_name": "update_sprint_report", "arguments": {...}}` - no
        "type"/"function"/"name" key at all, "function_name" instead - which
        also slipped through as raw text.
        """
        response = self._response_with_text(
            '{"function_name": "update_sprint_report", "arguments": {"kpis": "calculate_kpis"}}'
        )
        callback_context = MagicMock()
        callback_context.agent_name = "QualityGuardian"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "update_sprint_report")
        self.assertEqual(fc.args, {"kpis": "calculate_kpis"})

    def test_converts_parameters_key_shape(self):
        """
        Acceptance Criteria: a later eval run produced "parameters" instead
        of "arguments"/"args" - also a recognized args key now.
        """
        response = self._response_with_text(
            '{"type": "function", "name": "update_sprint_report", "parameters": {"kpis": "calculate_kpis"}}'
        )
        callback_context = MagicMock()
        callback_context.agent_name = "QualityGuardian"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "update_sprint_report")
        self.assertEqual(fc.args, {"kpis": "calculate_kpis"})

    def test_converts_python_repr_style_envelope(self):
        """
        Acceptance Criteria: a later eval run produced a Python repr()-style
        envelope - single-quoted, like str(some_dict) - instead of valid
        JSON. json.loads rejects single quotes outright, so this used to
        bail out of the whole function immediately, leaving the text
        unrecovered. ast.literal_eval is now tried as a fallback parser.
        """
        response = self._response_with_text(
            "{'type': 'function', 'name': 'update_sprint_report', 'parameters': {'kpis': 'calculate_kpis'}}"
        )
        callback_context = MagicMock()
        callback_context.agent_name = "QualityGuardian"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "update_sprint_report")
        self.assertEqual(fc.args, {"kpis": "calculate_kpis"})

    def test_does_not_touch_a_real_function_call(self):
        response = LlmResponse(content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="repo_status", args={}))],
        ))
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertEqual(response.content.parts[0].function_call.name, "repo_status")

    def test_does_not_touch_legitimate_prose(self):
        response = self._response_with_text("Here's the current sprint status and what I'd suggest next.")
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertEqual(response.content.parts[0].text, "Here's the current sprint status and what I'd suggest next.")
        self.assertIsNone(response.content.parts[0].function_call)

    def test_does_not_touch_json_that_is_not_the_fake_tool_call_shape(self):
        response = self._response_with_text('{"status": "ok", "note": "just some unrelated JSON"}')
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertEqual(response.content.parts[0].text, '{"status": "ok", "note": "just some unrelated JSON"}')
        self.assertIsNone(response.content.parts[0].function_call)

    def test_does_not_touch_multiple_text_parts(self):
        response = LlmResponse(content=types.Content(
            role="model",
            parts=[
                types.Part(text='{"type": "function", "function": "repo_status", "arguments": {}}'),
                types.Part(text="some more text"),
            ],
        ))
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertIsNone(response.content.parts[0].function_call)

    def test_handles_empty_content_without_raising(self):
        response = LlmResponse(content=None)
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"
        recover_fake_tool_call_callback(callback_context, response)  # must not raise

    def test_recovered_call_is_seen_as_a_real_tool_call_by_the_stall_detector(self):
        """Integration check: once recovered, the stall detector
        (_track_orchestrator_stall, run via history_management_after_callback)
        must see this as a real tool call and reset the streak, not count
        it as yet another stalled reply."""
        from agents.scrum_team.agent import history_management_after_callback

        response = self._response_with_text('{"type": "function", "function": "repo_status", "arguments": {}}')
        state = ScrumState()
        state.orchestrator_stall_count = 2
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"
        callback_context.state = state.model_dump()

        recover_fake_tool_call_callback(callback_context, response)
        history_management_after_callback(callback_context, response)

        self.assertEqual(callback_context.state["orchestrator_stall_count"], 0)

    def test_registered_on_every_agent(self):
        for agent in (product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian, root_agent):
            self.assertIn(recover_fake_tool_call_callback, agent.canonical_after_model_callbacks)

    def test_converts_looser_shape_with_no_type_and_properties_key(self):
        """GH issue #95: a local Ollama model replied with `{"function":
        "read_doc", "properties": {"path": "..."}}` - no "type" key, and
        "properties" instead of "arguments"/"args". Must still recover."""
        response = self._response_with_text('{"function": "read_doc", "properties": {"path": "retro/ProductVision.md"}}')
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        fc = response.content.parts[0].function_call
        self.assertEqual(fc.name, "read_doc")
        self.assertEqual(fc.args, {"path": "retro/ProductVision.md"})

    def test_does_not_touch_bare_name_with_no_type_and_no_args_key(self):
        """Without either a "type": "function" marker or an args-shaped key,
        a dict merely containing a "function"/"name" string is too weak a
        signal to treat as an attempted call - could just be incidental
        JSON that happens to mention one."""
        response = self._response_with_text('{"function": "read_doc", "note": "just discussing this tool"}')
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertIsNone(response.content.parts[0].function_call)

    def test_unwraps_json_envelope_reply(self):
        """GH issue #91: the same local model wrapped a genuine
        conversational reply in a JSON envelope - `{"response_type": "info",
        "message": "..."}` - instead of replying in plain text. This is not
        a tool-call attempt, so it must be unwrapped to plain text, not
        converted into a function_call."""
        response = self._response_with_text(
            '{"response_type": "info", "message": "The current sprint has not been initialized yet."}'
        )
        callback_context = MagicMock()
        callback_context.agent_name = "ScrumOrchestrator"

        recover_fake_tool_call_callback(callback_context, response)

        self.assertIsNone(response.content.parts[0].function_call)
        self.assertEqual(response.content.parts[0].text, "The current sprint has not been initialized yet.")


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

    def test_repeated_token_budget_exceeded_calls_notify_only_once(self):
        """
        Acceptance Criteria (GH issue #112): the canned halt response
        repeats on every turn once a budget is exhausted, so
        check_cost_budget_callback (and therefore _notify_critical_halt)
        gets called again on every subsequent turn for as long as the halt
        lasts - previously each of those re-invocations appended a new
        blocking_interactions entry and re-fired every configured notifier,
        an unbounded stream of duplicate alerts for the same exhaustion
        event. Must notify exactly once until reset_sprint_budget clears it.
        """
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 100
        state.token_usage.total = 150
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
            for _ in range(5):
                result = check_cost_budget_callback(mock_context, MagicMock(model=None))
                self.assertIsNotNone(result)

        interactions = mock_context.state["blocking_interactions"]
        self.assertEqual(len(interactions), 1)

    def test_reset_sprint_budget_allows_a_fresh_notification_next_sprint(self):
        mock_context = MagicMock()
        mock_context.agent_name = "TestAgent"
        state = ScrumState()
        state.budgets.total = 100
        state.token_usage.total = 150
        state.litellm_keys["TestAgent"] = "sk-test-agent-key"
        mock_context.state = state.model_dump()

        with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
            check_cost_budget_callback(mock_context, MagicMock(model=None))
            check_cost_budget_callback(mock_context, MagicMock(model=None))
        self.assertEqual(len(mock_context.state["blocking_interactions"]), 1)

        reset_sprint_budget(tool_context=mock_context)
        self.assertFalse(mock_context.state["critical_halt_notified"])
        # Simulate the new sprint ALSO exhausting its (freshly reset) budget.
        mock_context.state["token_usage"]["total"] = 150

        with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
            check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertEqual(len(mock_context.state["blocking_interactions"]), 2)


class TestSprintCloseoutGrace(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run produced no sprint report and no
    release PR at all on token-budget exhaustion, since every subsequent
    call for every agent was replaced with a canned halt response the
    instant the main budget tripped - nobody ever got a turn to run the
    SPRINT CLOSE SEQUENCE (retro -> create_sprint_report -> KPIs ->
    create_release_pr). ScrumMaster/ProductOwner/QualityGuardian/
    ScrumOrchestrator now get a small, bounded extra allowance
    (closeout_grace_percent, agents/scrum_team/helpers.py) specifically to
    finish that sequence for real; DevTeam/QA/Architect still hard-halt
    immediately, unconditionally - no more code should get written past the
    cap.
    """

    def _context(self, agent_name, token_total, token_usage):
        mock_context = MagicMock()
        mock_context.agent_name = agent_name
        state = ScrumState()
        state.budgets.total = token_total
        state.token_usage.total = token_usage
        if agent_name != "ScrumOrchestrator":
            state.litellm_keys[agent_name] = "sk-test-agent-key"
        mock_context.state = state.model_dump()
        return mock_context

    def test_grace_role_gets_a_real_call_through_within_the_grace_ceiling(self):
        # 100 main budget, 5% default grace -> ceiling 105; 104 is over the
        # main budget but still under grace.
        for agent_name in ("ScrumMaster", "ProductOwner", "QualityGuardian", "ScrumOrchestrator"):
            with self.subTest(agent_name=agent_name):
                mock_context = self._context(agent_name, 100, 104)
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))
                self.assertIsNone(result, f"{agent_name} should still get a real call within grace")

    def test_non_grace_role_hard_halts_immediately_with_no_grace_at_all(self):
        for agent_name in ("DevTeam", "QA", "Architect"):
            with self.subTest(agent_name=agent_name):
                mock_context = self._context(agent_name, 100, 104)
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))
                self.assertIsNotNone(result, f"{agent_name} must not get any grace")

    def test_non_grace_role_halt_redirects_to_product_owner_instead_of_freezing(self):
        # ISSUE-0045 / 0.1.0-run25: a non-grace role halted with a plain-text
        # canned reply has no tools left (including transfer_to_agent) - if
        # it's the *active* agent when the budget trips, the grace allowance
        # above can never actually be used by anyone, and every subsequent
        # "continue" nudge just re-invokes the same frozen agent forever. The
        # halt response must instead carry a real transfer_to_agent
        # function_call back to a grace-eligible role, so ADK actually hands
        # control off.
        for agent_name in ("DevTeam", "QA", "Architect"):
            with self.subTest(agent_name=agent_name):
                mock_context = self._context(agent_name, 100, 104)
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))
                function_calls = [p.function_call for p in result.content.parts if getattr(p, "function_call", None)]
                self.assertEqual(len(function_calls), 1, f"{agent_name}'s halt response must contain exactly one transfer")
                self.assertEqual(function_calls[0].name, "transfer_to_agent")
                self.assertEqual(function_calls[0].args, {"agent_name": "ProductOwner"})

    def test_grace_role_also_hard_halts_once_the_grace_ceiling_is_exceeded(self):
        # 100 main budget, 5% default grace -> ceiling 105; 106 is past both.
        mock_context = self._context("ProductOwner", 100, 106)
        with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
            result = check_cost_budget_callback(mock_context, MagicMock(model=None))
        self.assertIsNotNone(result)
        # Already a grace-eligible role with its own grace spent - nowhere
        # better to redirect to, so no synthetic transfer, just the plain
        # halt text (unlike the non-grace-role case above).
        self.assertFalse(any(getattr(p, "function_call", None) for p in result.content.parts))

    def test_grace_percent_is_configurable_via_env(self):
        mock_context = self._context("ProductOwner", 100, 104)
        with patch.dict("os.environ", {"SPRINT_CLOSEOUT_GRACE_PERCENT": "0"}):
            with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                result = check_cost_budget_callback(mock_context, MagicMock(model=None))
        self.assertIsNotNone(result, "0% grace should hard-halt exactly like today's unconditional behavior")

    def test_usd_budget_grants_the_same_grace(self):
        mock_context = self._context("ProductOwner", 1000000, 0)
        mock_context.state["budgets"]["total_usd"] = 10.0

        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "mk", "LITELLM_PROXY_API_BASE": "http://proxy"}, clear=True):
            with patch("requests.post") as mock_post:
                # 10.4 is over the $10 ceiling but under the 5% grace ceiling ($10.50).
                mock_post.return_value.json.return_value = [{"spend": 10.4}]
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNone(result)

    def test_usd_budget_hard_halts_a_non_grace_role_even_within_the_grace_window(self):
        mock_context = self._context("DevTeam", 1000000, 0)
        mock_context.state["budgets"]["total_usd"] = 10.0

        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "mk", "LITELLM_PROXY_API_BASE": "http://proxy"}, clear=True):
            with patch("requests.post") as mock_post:
                mock_post.return_value.json.return_value = [{"spend": 10.4}]
                with patch("agents.scrum_team.agent._sync_roadmap_on_exhaustion_once"):
                    result = check_cost_budget_callback(mock_context, MagicMock(model=None))

        self.assertIsNotNone(result)


class TestSyncAndCommitRoadmapOnExhaustion(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run hit exhaustion mid-story with a
    feature branch checked out - the roadmap sync commit landed there
    instead of develop, leaving develop's own specs/ROADMAP.md stale. Must
    check out the configured develop branch first, sync after (the sync
    renders from in-memory state, not disk), then push to develop - falling
    back to whatever's currently checked out only if switching to develop
    itself fails.
    """

    def _context(self):
        mock_context = MagicMock()
        mock_context.state = ScrumState().model_dump()
        return mock_context

    def test_checks_out_develop_before_syncing_and_pushes_there(self):
        mock_context = self._context()
        calls = []

        def fake_run(cmd, cwd=None, tool_context=None):
            calls.append(cmd)
            return {"status": "ok", "stdout": "", "stderr": ""}

        with patch("agents.scrum_team.agent._configured_repo_root", return_value="/repo"), \
             patch("agents.scrum_team.agent._develop_branch_name", return_value="develop"), \
             patch("agents.scrum_team.agent._run", side_effect=fake_run) as mock_run, \
             patch("agents.scrum_team.agent.sync_all_active_stories_to_roadmap") as mock_sync, \
             patch("agents.scrum_team.agent._git_push_impl") as mock_push:
            _sync_and_commit_roadmap_on_exhaustion(mock_context)

        self.assertIn(["git", "fetch", "origin", "develop"], calls)
        self.assertIn(["git", "checkout", "-B", "develop", "origin/develop"], calls)
        self.assertTrue(mock_sync.called)
        mock_push.assert_called_once_with(
            branch="develop",
            commit_message="chore: sync roadmap - sprint budget exhausted",
            allow_protected=True,
            tool_context=mock_context,
        )

    def test_falls_back_to_current_branch_if_develop_checkout_fails(self):
        mock_context = self._context()

        def fake_run(cmd, cwd=None, tool_context=None):
            if cmd[:2] == ["git", "fetch"] or cmd[:2] == ["git", "checkout"]:
                return {"status": "error", "stderr": "no network"}
            if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return {"status": "ok", "stdout": "feature/US-001-todo\n"}
            return {"status": "ok", "stdout": "", "stderr": ""}

        with patch("agents.scrum_team.agent._configured_repo_root", return_value="/repo"), \
             patch("agents.scrum_team.agent._develop_branch_name", return_value="develop"), \
             patch("agents.scrum_team.agent._run", side_effect=fake_run), \
             patch("agents.scrum_team.agent.sync_all_active_stories_to_roadmap"), \
             patch("agents.scrum_team.agent._git_push_impl") as mock_push:
            _sync_and_commit_roadmap_on_exhaustion(mock_context)

        mock_push.assert_called_once_with(
            branch="feature/US-001-todo",
            commit_message="chore: sync roadmap - sprint budget exhausted",
            allow_protected=True,
            tool_context=mock_context,
        )

    def test_never_raises_even_if_everything_fails(self):
        mock_context = self._context()
        with patch("agents.scrum_team.agent._configured_repo_root", side_effect=Exception("boom")):
            _sync_and_commit_roadmap_on_exhaustion(mock_context)  # must not raise


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


def _mock_context_with_model(agent_name, agent_key=None, additional_args=None):
    mock_context = MagicMock()
    mock_context.agent_name = agent_name
    mock_context.state = ScrumState().model_dump()
    if agent_key:
        mock_context.state["litellm_keys"][agent_name] = agent_key
    model = MagicMock()
    model._additional_args = additional_args if additional_args is not None else {}
    mock_context._invocation_context.agent.canonical_model = model
    return mock_context, model


class TestInjectLitellmKeyCallback(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #116): the resolved API key must be set
    as a per-agent-instance additional arg on that agent's own LiteLlm
    model object, not the process-wide litellm.api_key global - mutating a
    global raced across concurrent sessions/roles in `adk web` mode,
    letting one agent's request get billed against a different role's
    budget-capped virtual key.
    """

    def test_sets_agent_specific_key_on_the_agents_own_model(self):
        mock_context, model = _mock_context_with_model("DevTeam", agent_key="sk-devteam-key")

        inject_litellm_key_callback(mock_context, MagicMock())

        self.assertEqual(model._additional_args["api_key"], "sk-devteam-key")

    def test_falls_back_to_proxy_api_key_env_var_when_no_agent_key(self):
        mock_context, model = _mock_context_with_model("DevTeam")

        with patch.dict("os.environ", {"LITELLM_PROXY_API_KEY": "sk-fallback-key"}, clear=True):
            inject_litellm_key_callback(mock_context, MagicMock())

        self.assertEqual(model._additional_args["api_key"], "sk-fallback-key")

    def test_two_roles_own_models_do_not_clobber_each_others_key(self):
        """The actual bug: two agents' calls (here simulated as two
        sequential callback invocations, standing in for what would be
        concurrent coroutines in adk web mode) must never share mutable
        key state - each agent's own model instance holds its own key."""
        dev_context, dev_model = _mock_context_with_model("DevTeam", agent_key="sk-devteam-key")
        qa_context, qa_model = _mock_context_with_model("QA", agent_key="sk-qa-key")

        inject_litellm_key_callback(dev_context, MagicMock())
        inject_litellm_key_callback(qa_context, MagicMock())

        self.assertEqual(dev_model._additional_args["api_key"], "sk-devteam-key")
        self.assertEqual(qa_model._additional_args["api_key"], "sk-qa-key")

    def test_does_not_mutate_the_global_litellm_api_key(self):
        import litellm
        mock_context, _model = _mock_context_with_model("DevTeam", agent_key="sk-devteam-key")
        litellm.api_key = "sentinel-should-not-change"

        inject_litellm_key_callback(mock_context, MagicMock())

        self.assertEqual(litellm.api_key, "sentinel-should-not-change")

    def test_falls_back_to_proxy_api_key_when_agent_key_does_not_look_like_a_real_key(self):
        """
        Acceptance Criteria: a real eval run's evalset fixtures
        (eval/adk/scrum_team.evalset.json) pre-seed litellm_keys with
        non-"sk-" placeholder strings (e.g. "eval-fixture-key-devteam") to
        satisfy check_cost_budget_callback's "has a key at all" presence
        check - LiteLLM's own proxy auth requires a real key to start with
        "sk-", so shipping that placeholder as the actual Bearer token
        fails every call for that role with a confusing 401. This must
        fall back to LITELLM_PROXY_API_KEY the same way as "no agent key
        at all", instead of blindly trusting anything present.
        """
        mock_context, model = _mock_context_with_model("DevTeam", agent_key="eval-fixture-key-devteam")

        with patch.dict("os.environ", {"LITELLM_PROXY_API_KEY": "sk-fallback-key"}, clear=True):
            inject_litellm_key_callback(mock_context, MagicMock())

        self.assertEqual(model._additional_args["api_key"], "sk-fallback-key")

    def test_falls_back_to_global_when_model_has_no_additional_args(self):
        """Defensive fallback for an agent that doesn't expose a
        LiteLlm-shaped model (or a future ADK internals change) - better to
        fall back to the old (still-correct-if-not-concurrent) behavior
        than to silently inject no key at all."""
        import litellm
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        mock_context.state = ScrumState().model_dump()
        mock_context.state["litellm_keys"]["DevTeam"] = "sk-devteam-key"

        # A model with no _additional_args attribute at all.
        broken_model = object()
        mock_context._invocation_context.agent.canonical_model = broken_model

        inject_litellm_key_callback(mock_context, MagicMock())

        self.assertEqual(litellm.api_key, "sk-devteam-key")


class TestSetupLoggingRespectsLogLevel(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #128): the per-session log file handler
    used to be hardcoded to DEBUG regardless of LOG_LEVEL, so it always
    captured verbose upstream traces (httpx/openai/litellm/google.adk -
    which can include full request/response bodies and headers) even when
    a user explicitly chose a quieter LOG_LEVEL. The file handler must
    respect the same level as everything else.
    """

    def _file_handler_level(self):
        import logging
        root_logger = logging.getLogger()
        for h in root_logger.handlers:
            if isinstance(h, logging.FileHandler):
                return h.level
        return None

    def tearDown(self):
        # _setup_logging adds a new FileHandler to the root logger every
        # call - remove any added during this test so later tests/imports
        # in the same process aren't left with stale/duplicate handlers.
        import logging
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            if isinstance(h, logging.FileHandler):
                root_logger.removeHandler(h)
                h.close()

    def test_file_handler_respects_info_log_level(self):
        import logging
        import os
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}, clear=False):
            agent_module._setup_logging()
        self.assertEqual(self._file_handler_level(), logging.INFO)

    def test_file_handler_respects_debug_log_level(self):
        import logging
        import os
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
            agent_module._setup_logging()
        self.assertEqual(self._file_handler_level(), logging.DEBUG)


class TestPatchedAdkAcompletion(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #126): a transient LiteLLM proxy
    connection failure must degrade into a synthetic response, the same
    way an existing Gemini safety-block error already does - not
    propagate as a raw exception, which crashes the whole `adk run` CLI
    process on the very first message.
    """

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_connection_error_degrades_instead_of_raising(self):
        import litellm.exceptions

        async def _raise_connection_error(self, *args, **kwargs):
            raise litellm.exceptions.APIConnectionError(
                message="connection refused", llm_provider="gemini", model="scrum-eval-cheap"
            )

        with patch.object(agent_module, "_orig_adk_acompletion", _raise_connection_error):
            response = self._run(agent_module._patched_adk_acompletion(MagicMock(), model="scrum-eval-cheap"))

        self.assertIn("CONNECTION ERROR", response.choices[0].message.content)
        self.assertEqual(response.model, "scrum-eval-cheap")

    def test_internal_server_error_with_connection_message_degrades(self):
        """Mirrors the real GH issue #126 traceback: litellm's own
        exception mapper surfaces a bare proxy connection failure as
        InternalServerError, not APIConnectionError."""
        import litellm.exceptions

        async def _raise_internal_server_error(self, *args, **kwargs):
            raise litellm.exceptions.InternalServerError(
                message="Litellm_proxyException - Connection error.", llm_provider="gemini", model="scrum-eval-cheap"
            )

        with patch.object(agent_module, "_orig_adk_acompletion", _raise_internal_server_error):
            response = self._run(agent_module._patched_adk_acompletion(MagicMock(), model="scrum-eval-cheap"))

        self.assertIn("CONNECTION ERROR", response.choices[0].message.content)

    def test_unrelated_internal_server_error_still_raises(self):
        """An InternalServerError that isn't actually a connection failure
        (e.g. a real 500 from the provider) must still propagate - this
        patch only degrades the specific connection-failure shape."""
        import litellm.exceptions

        async def _raise_internal_server_error(self, *args, **kwargs):
            raise litellm.exceptions.InternalServerError(
                message="something else entirely broke", llm_provider="gemini", model="scrum-eval-cheap"
            )

        with patch.object(agent_module, "_orig_adk_acompletion", _raise_internal_server_error):
            with self.assertRaises(litellm.exceptions.InternalServerError):
                self._run(agent_module._patched_adk_acompletion(MagicMock(), model="scrum-eval-cheap"))

    def test_safety_block_still_degrades(self):
        """Regression guard: adding connection-error handling must not
        disturb the pre-existing safety-block degradation."""

        async def _raise_safety_block(self, *args, **kwargs):
            raise ValueError("Invalid response - no 'choices' field returned")

        with patch.object(agent_module, "_orig_adk_acompletion", _raise_safety_block):
            response = self._run(agent_module._patched_adk_acompletion(MagicMock(), model="scrum-eval-cheap"))

        self.assertIn("SAFETY BLOCK", response.choices[0].message.content)


if __name__ == "__main__":
    unittest.main()