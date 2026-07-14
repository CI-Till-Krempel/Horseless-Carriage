# agents/scrum_team/tests/test_state_robustness.py
import unittest
from agents.scrum_team.agent import get_scrum_state
from agents.scrum_team.state import ScrumState

class MockStateWithToDict:
    def __init__(self, data):
        self._data = data
    def to_dict(self):
        return self._data

class MockStateWithKeyError(object):
    def __init__(self, data):
        self._data = data
    def __getitem__(self, key):
        # This will raise KeyError: 0 when dict() calls obj[0]
        return self._data[key]
    # No __iter__

class TestStateRobustness(unittest.TestCase):
    def test_get_scrum_state_with_dict(self):
        data = {"product_vision": "Test Vision"}
        state = get_scrum_state(data)
        self.assertIsInstance(state, ScrumState)
        self.assertEqual(state.product_vision, "Test Vision")

    def test_get_scrum_state_with_to_dict(self):
        data = {"product_vision": "To Dict Vision"}
        mock_state = MockStateWithToDict(data)
        state = get_scrum_state(mock_state)
        self.assertIsInstance(state, ScrumState)
        self.assertEqual(state.product_vision, "To Dict Vision")

    def test_get_scrum_state_with_key_error(self):
        # This mimics the behavior causing KeyError: 0
        mock_state = MockStateWithKeyError({"token_usage": {"total": 100}})
        try:
            state = get_scrum_state(mock_state)
            self.assertIsInstance(state, ScrumState)
        except KeyError as e:
            self.fail(f"get_scrum_state raised KeyError: {e}")

    def test_get_scrum_state_with_none(self):
        state = get_scrum_state(None)
        self.assertIsInstance(state, ScrumState)

if __name__ == "__main__":
    unittest.main()
