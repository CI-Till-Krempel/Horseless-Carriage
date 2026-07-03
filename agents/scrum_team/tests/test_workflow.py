# agents/scrum_team/tests/test_workflow.py
import unittest
from unittest.mock import MagicMock, patch

from ..tools.workflow import (
    generate_workflow_diagram,
    gather_workflow_improvement_proposals,
)
from ..state import ScrumState


class TestWorkflowTools(unittest.TestCase):
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_generate_workflow_diagram(self, mock_open):
        """
        Acceptance Criteria:
        - A workflow diagram is generated and saved to a file.
        """
        state = ScrumState()
        result = generate_workflow_diagram(state)
        self.assertEqual(result, "Workflow diagram generated at docs/workflow.puml")
        mock_open.assert_called_with("docs/workflow.puml", "w")

    def test_gather_workflow_improvement_proposals(self):
        """
        Acceptance Criteria:
        - A list of workflow improvement proposals is returned.
        """
        state = ScrumState()
        proposals = gather_workflow_improvement_proposals(state)
        self.assertIsInstance(proposals, list)
        self.assertGreater(len(proposals), 0)


if __name__ == "__main__":
    unittest.main()