# agents/scrum_team/tests/test_workflow.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.workflow import (
    generate_workflow_diagram,
    gather_workflow_improvement_proposals,
)
from agents.scrum_team.state import ScrumState


class TestWorkflowTools(unittest.TestCase):
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_generate_workflow_diagram(self, mock_write_file):
        """
        Acceptance Criteria:
        - A workflow diagram is generated and saved to a file.
        """
        mock_write_file.return_value = {"status": "ok", "path": "specs/workflow.puml"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        result = generate_workflow_diagram(tool_context=tool_context)
        self.assertEqual(result["path"], "specs/workflow.puml")
        mock_write_file.assert_called_with("specs/workflow.puml", unittest.mock.ANY, overwrite=True, tool_context=tool_context)

    def test_gather_workflow_improvement_proposals(self):
        """
        Acceptance Criteria:
        - A list of workflow improvement proposals is returned.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        proposals = gather_workflow_improvement_proposals()
        self.assertIsInstance(proposals, list)
        self.assertGreater(len(proposals), 0)

    def test_gather_workflow_improvement_proposals_empty_state_is_a_real_message_not_a_placeholder(self):
        """
        Acceptance Criteria:
        - With no retro_actions/impediment_log at all, the single returned
          proposal says so explicitly rather than fabricating generic advice
          (the old hardcoded "Proposal 1: Automate..." strings this replaced).
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        proposals = gather_workflow_improvement_proposals(tool_context=tool_context)
        self.assertEqual(len(proposals), 1)
        self.assertIn("No open retro actions or impediments", proposals[0])

    def test_gather_workflow_improvement_proposals_reflects_real_retro_data(self):
        """
        Acceptance Criteria:
        - Open retro_actions and open impediment_log entries are both
          surfaced, each naming its own action/description and owner.
        - Entries already marked resolved/closed are excluded.
        """
        tool_context = MagicMock()
        state = ScrumState().model_dump()
        state["retro_actions"] = [
            {"action": "Tag Architect before marking data-model stories Ready", "owner": "ProductOwner",
             "success_metric": "0 rework stories next sprint", "status": "open"},
            {"action": "This one is already done", "owner": "QA", "success_metric": "n/a", "status": "resolved"},
        ]
        state["impediment_log"] = [
            {"description": "CI runner had no Docker socket access", "owner": "DevTeam", "status": "open"},
        ]
        tool_context.state = state

        proposals = gather_workflow_improvement_proposals(tool_context=tool_context)

        self.assertEqual(len(proposals), 2)
        self.assertTrue(any("Tag Architect" in p and "ProductOwner" in p for p in proposals))
        self.assertTrue(any("Unblock: CI runner had no Docker socket access" in p and "DevTeam" in p for p in proposals))
        self.assertFalse(any("already done" in p for p in proposals))


if __name__ == "__main__":
    unittest.main()