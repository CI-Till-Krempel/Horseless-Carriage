# agents/scrum_team/tests/test_helpers.py
import unittest
from unittest.mock import patch

from agents.scrum_team.helpers import (
    get_interaction_level,
    required_pre_implementation_approval,
    required_pre_release_approval,
    report_detail_level,
)


class TestInteractionLevel(unittest.TestCase):
    """Acceptance Criteria: INTERACTION_LEVEL is configurable and drives which
    human-approval gates are required - see docs/INTERACTION-LEVELS.md."""

    def test_defaults_to_product_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_interaction_level(), "Product")

    def test_defaults_to_product_when_unrecognized(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Manager"}, clear=True):
            self.assertEqual(get_interaction_level(), "Product")

    def test_case_insensitive(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "ceo"}, clear=True):
            self.assertEqual(get_interaction_level(), "CEO")
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "eval"}, clear=True):
            self.assertEqual(get_interaction_level(), "EVAL")

    def test_required_pre_implementation_approval_by_level(self):
        self.assertEqual(required_pre_implementation_approval("Product"), "sprint")
        self.assertEqual(required_pre_implementation_approval("Stakeholder"), "sprint")
        self.assertEqual(required_pre_implementation_approval("CEO"), "budget")
        self.assertIsNone(required_pre_implementation_approval("EVAL"))

    def test_required_pre_release_approval_by_level(self):
        self.assertEqual(required_pre_release_approval("Product"), "release")
        self.assertEqual(required_pre_release_approval("Stakeholder"), "release")
        self.assertIsNone(required_pre_release_approval("CEO"))
        self.assertIsNone(required_pre_release_approval("EVAL"))

    def test_reads_from_environment_when_level_not_given(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            self.assertEqual(required_pre_implementation_approval(), "budget")
            self.assertIsNone(required_pre_release_approval())

    def test_report_detail_level_by_level(self):
        self.assertEqual(report_detail_level("Product"), "full")
        self.assertEqual(report_detail_level("Stakeholder"), "business")
        self.assertEqual(report_detail_level("CEO"), "executive")
        self.assertEqual(report_detail_level("EVAL"), "full")


if __name__ == "__main__":
    unittest.main()
