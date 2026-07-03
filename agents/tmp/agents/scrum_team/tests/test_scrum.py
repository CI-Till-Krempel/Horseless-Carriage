# agents/scrum_team/tests/test_scrum.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.scrum import (
    init_scrum_state,
    upsert_story,
    upsert_epic,
    plan_backlog_item,
    set_priority,
    add_impediment,
    add_retro_action,
    plan_sprint_backlog_item,
)
from ..state import ScrumState


class TestScrumTools(unittest.TestCase):
    def test_init_scrum_state(self):
        """
        Acceptance Criteria:
        - Calling init_scrum_state should return a new ScrumState object.
        """
        state = init_scrum_state()
        self.assertIsInstance(state, ScrumState)

    def test_upsert_story(self):
        """
        Acceptance Criteria:
        - A new story is added to the backlog.
        - An existing story is updated.
        """
        state = ScrumState()
        story = {"id": "ST-1", "title": "New Story", "status": "new"}
        state = upsert_story(story, state)
        self.assertIn("ST-1", state.backlog)

        updated_story = {"id": "ST-1", "title": "Updated Story", "status": "in_progress"}
        state = upsert_story(updated_story, state)
        self.assertEqual(state.backlog["ST-1"]["title"], "Updated Story")

    def test_upsert_epic(self):
        """
        Acceptance Criteria:
        - A new epic is added to the epics list.
        - An existing epic is updated.
        """
        state = ScrumState()
        epic = {"id": "EP-1", "title": "New Epic", "status": "new"}
        state = upsert_epic(epic, state)
        self.assertIn("EP-1", state.epics)

        updated_epic = {"id": "EP-1", "title": "Updated Epic", "status": "in_progress"}
        state = upsert_epic(updated_epic, state)
        self.assertEqual(state.epics["EP-1"]["title"], "Updated Epic")

    def test_plan_backlog_item(self):
        """
        Acceptance Criteria:
        - A new item is added to the backlog with a 'planned' status.
        """
        state = ScrumState()
        item = {"id": "BL-1", "title": "New backlog item"}
        state = plan_backlog_item(item, state)
        self.assertIn("BL-1", state.backlog)
        self.assertEqual(state.backlog["BL-1"]["status"], "planned")

    def test_set_priority(self):
        """
        Acceptance Criteria:
        - The priority of a backlog item is updated.
        """
        state = ScrumState()
        story = {"id": "ST-1", "title": "New Story", "status": "new", "priority": "medium"}
        state = upsert_story(story, state)
        state = set_priority("ST-1", "high", state)
        self.assertEqual(state.backlog["ST-1"]["priority"], "high")

    def test_add_impediment(self):
        """
        Acceptance Criteria:
        - An impediment is added to the impediments list of a backlog item.
        """
        state = ScrumState()
        story = {"id": "ST-1", "title": "New Story", "status": "new"}
        state = upsert_story(story, state)
        state = add_impediment("ST-1", "This is an impediment", state)
        self.assertIn("This is an impediment", state.backlog["ST-1"]["impediments"])

    def test_add_retro_action(self):
        """
        Acceptance Criteria:
        - A new action item is added to the retrospective actions list.
        """
        state = ScrumState()
        state = add_retro_action("Improve testing", state)
        self.assertIn("Improve testing", state.retrospective_actions)

    def test_plan_sprint_backlog_item(self):
        """
        Acceptance Criteria:
        - A backlog item is moved to the sprint backlog.
        """
        state = ScrumState()
        story = {"id": "ST-1", "title": "New Story", "status": "new"}
        state = upsert_story(story, state)
        state = plan_sprint_backlog_item("ST-1", state)
        self.assertIn("ST-1", state.sprint_backlog)


if __name__ == "__main__":
    unittest.main()