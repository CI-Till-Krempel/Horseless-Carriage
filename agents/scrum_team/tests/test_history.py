# agents/scrum_team/tests/test_history.py
import os
import unittest
from unittest.mock import MagicMock, patch
from google.genai import types
from agents.scrum_team.agent import (
    history_management_callback,
    history_management_after_callback,
    _trim_transcript,
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

    def test_trim_transcript_leaves_under_threshold_untouched(self):
        transcript = [{"agent_name": "DevTeam", "role": "model", "content": f"entry {i}"} for i in range(5)]
        result = _trim_transcript(transcript, max_entries=10)
        self.assertEqual(result, transcript)

    def test_trim_transcript_exact_threshold_no_off_by_one(self):
        # US-0004 edge case: exactly at the threshold must not be trimmed.
        transcript = [{"agent_name": "DevTeam", "role": "model", "content": f"entry {i}"} for i in range(10)]
        result = _trim_transcript(transcript, max_entries=10)
        self.assertEqual(result, transcript)
        self.assertNotIn("omitted", str(result))

    def test_trim_transcript_over_threshold_keeps_most_recent(self):
        transcript = [{"agent_name": "DevTeam", "role": "model", "content": f"entry {i}"} for i in range(13)]
        result = _trim_transcript(transcript, max_entries=10)

        # Marker + 10 most recent retained entries.
        self.assertEqual(len(result), 11)
        self.assertEqual(result[0]["agent_name"], "system")
        self.assertIn("3 earlier transcript entries omitted", result[0]["content"])
        self.assertEqual([e["content"] for e in result[1:]], [f"entry {i}" for i in range(3, 13)])

    def test_transcript_is_trimmed_during_capture(self):
        # Integration through the actual callback: growth must be bounded as
        # entries are appended turn by turn, not just in the helper directly.
        state = ScrumState()
        mock_context = MagicMock()
        mock_context.agent_name = "DevTeam"
        mock_context.state = state.model_dump()

        with patch.dict(os.environ, {"TRANSCRIPT_MAX_ENTRIES": "3"}):
            for i in range(6):
                mock_response = MagicMock()
                mock_response.content = types.Content(role="model", parts=[types.Part(text=f"turn {i}")])
                history_management_after_callback(mock_context, mock_response)

        transcript = mock_context.state["transcript"]
        # Bounded to max_entries + 1 marker, never left to grow to 6.
        self.assertLessEqual(len(transcript), 4)
        # Most recent turns preserved over older ones.
        self.assertEqual(transcript[-1]["content"], "turn 5")

if __name__ == "__main__":
    unittest.main()
