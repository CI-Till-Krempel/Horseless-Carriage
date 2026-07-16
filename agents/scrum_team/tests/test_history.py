# agents/scrum_team/tests/test_history.py
import unittest
from unittest.mock import MagicMock
from google.genai import types
from agents.scrum_team.agent import (
    history_management_callback,
    history_management_after_callback,
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

if __name__ == "__main__":
    unittest.main()
