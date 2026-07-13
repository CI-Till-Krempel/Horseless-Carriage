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


if __name__ == "__main__":
    unittest.main()