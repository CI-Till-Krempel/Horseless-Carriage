# agents/scrum_team/tests/test_history.py
import unittest
from unittest.mock import MagicMock
from google.genai import types
from agents.scrum_team.agent import (
    history_management_callback,
    history_management_after_callback,
    product_owner,
    scrum_master,
    dev_team,
    qa_agent,
    architect,
    quality_guardian,
)
from agents.scrum_team.state import ScrumState

class TestHistoryManagement(unittest.TestCase):
    def test_history_injection(self):
        # 1. Setup state with history
        state = ScrumState()
        state.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "model", "content": "Hi there!"}
        ]
        
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = state.model_dump()
        
        # 2. Setup first turn request (no previous_interaction_id)
        mock_request = MagicMock()
        mock_request.previous_interaction_id = None
        current_msg = types.Content(role="user", parts=[types.Part(text="Repeat the last message")])
        mock_request.contents = [current_msg]
        
        # 3. Call the callback
        history_management_callback(mock_context, mock_request)
        
        # 4. Verify injection
        self.assertEqual(len(mock_request.contents), 3)
        self.assertEqual(mock_request.contents[0].parts[0].text, "Hello")
        self.assertEqual(mock_request.contents[1].parts[0].text, "Hi there!")
        self.assertEqual(mock_request.contents[2].parts[0].text, "Repeat the last message")
        
        # Verify state synchronization
        self.assertEqual(len(mock_context.state["messages"]), 3)
        self.assertEqual(mock_context.state["messages"][2]["content"], "Repeat the last message")

    def test_history_saving_after_response(self):
        # 1. Setup state
        state = ScrumState()
        state.messages = [{"role": "user", "content": "Hello"}]
        
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = state.model_dump()
        
        # 2. Setup model response
        mock_response = MagicMock()
        mock_response.content = types.Content(role="model", parts=[types.Part(text="Hi!")])
        
        # 3. Call the callback
        history_management_after_callback(mock_context, mock_response)
        
        # 4. Verify save
        self.assertEqual(len(mock_context.state["messages"]), 2)
        self.assertEqual(mock_context.state["messages"][1]["role"], "model")
        self.assertEqual(mock_context.state["messages"][1]["content"], "Hi!")

    def test_no_injection_on_specialist_agents(self):
        # Specialists should NOT have history injected (they get fresh context for their tasks)
        state = ScrumState()
        state.messages = [{"role": "user", "content": "History"}]
        
        mock_context = MagicMock()
        mock_context.agent_name = "ProductOwner"
        mock_context.state = state.model_dump()
        
        mock_request = MagicMock()
        mock_request.previous_interaction_id = None
        mock_request.contents = [types.Content(role="user", parts=[types.Part(text="Task")])]
        
        history_management_callback(mock_context, mock_request)

        self.assertEqual(len(mock_request.contents), 1)
        self.assertEqual(mock_request.contents[0].parts[0].text, "Task")

    def test_transcript_captures_specialist_agent_turns(self):
        # US-0001: every sub-agent's turns must land in the shared transcript,
        # tagged by agent_name, not silently dropped.
        for agent_name in ["ProductOwner", "ScrumMaster", "DevTeam", "QA", "Architect", "QualityGuardian"]:
            state = ScrumState()
            mock_context = MagicMock()
            mock_context.agent_name = agent_name
            mock_context.state = state.model_dump()

            mock_response = MagicMock()
            mock_response.content = types.Content(role="model", parts=[types.Part(text=f"{agent_name} says hi")])

            history_management_after_callback(mock_context, mock_response)

            transcript = mock_context.state["transcript"]
            self.assertEqual(len(transcript), 1)
            self.assertEqual(transcript[0]["agent_name"], agent_name)
            self.assertEqual(transcript[0]["content"], f"{agent_name} says hi")
            # Specialists must NOT be written into the Orchestrator-only resumable history.
            self.assertEqual(mock_context.state["messages"], [])

    def test_transcript_captures_orchestrator_turns_too(self):
        # The Orchestrator's own turns should land in the shared transcript
        # in addition to (not instead of) the existing `messages` history.
        state = ScrumState()
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = state.model_dump()

        mock_response = MagicMock()
        mock_response.content = types.Content(role="model", parts=[types.Part(text="Orchestrator turn")])

        history_management_after_callback(mock_context, mock_response)

        self.assertEqual(len(mock_context.state["transcript"]), 1)
        self.assertEqual(mock_context.state["transcript"][0]["agent_name"], "ScrumOrchestrator")
        self.assertEqual(len(mock_context.state["messages"]), 1)

    def test_transcript_captures_nested_tool_call_steps(self):
        # Edge case: a sub-agent turn with nested tool calls triggers the
        # after_model_callback multiple times before finishing; each step
        # must be captured, not just the final response.
        state = ScrumState()
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        mock_context.state = state.model_dump()

        for step_text in ["Calling tool X", "Calling tool Y", "Final answer"]:
            mock_response = MagicMock()
            mock_response.content = types.Content(role="model", parts=[types.Part(text=step_text)])
            history_management_after_callback(mock_context, mock_response)

        transcript = mock_context.state["transcript"]
        self.assertEqual(len(transcript), 3)
        self.assertEqual([e["content"] for e in transcript], ["Calling tool X", "Calling tool Y", "Final answer"])
        self.assertTrue(all(e["agent_name"] == "DevTeam" for e in transcript))

    def test_history_after_callback_no_regression_without_content(self):
        mock_context = MagicMock()
        mock_context.agent_name = "ScrumOrchestrator"
        mock_context.state = ScrumState().model_dump()

        mock_response = MagicMock()
        mock_response.content = None

        result = history_management_after_callback(mock_context, mock_response)
        self.assertIsNone(result)

    def test_specialist_agents_are_wired_to_history_after_callback(self):
        # Registration check: specialists must actually have the callback
        # attached, or capture would never fire in practice.
        for agent in [product_owner, scrum_master, dev_team, qa_agent, architect, quality_guardian]:
            self.assertIn(history_management_after_callback, agent.after_model_callback)

if __name__ == "__main__":
    unittest.main()
