# agents/scrum_team/tests/test_helpers.py
import unittest
from unittest.mock import patch

from agents.scrum_team.helpers import (
    get_interaction_level,
    required_pre_implementation_approval,
    required_pre_release_approval,
    report_detail_level,
    get_env_with_deprecated_fallback,
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


class TestGetEnvWithDeprecatedFallback(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #81): renaming a budget env var
    (SPRINT_USD_BUDGET -> TOTAL_USD_BUDGET) must never silently drop an
    existing .env's configured value in favor of a hardcoded default - that
    could mean a *higher*, unintended ceiling and unexpected cloud costs.
    """

    def setUp(self):
        import agents.scrum_team.helpers as helpers_module
        self._helpers_module = helpers_module
        helpers_module._deprecated_env_vars_warned.clear()

    def test_prefers_new_name_when_set(self):
        with patch.dict("os.environ", {"NEW_NAME": "5.0", "OLD_NAME": "1.0"}, clear=True):
            self.assertEqual(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"), "5.0")

    def test_falls_back_to_old_name_when_new_unset(self):
        with patch.dict("os.environ", {"OLD_NAME": "1.0"}, clear=True):
            self.assertEqual(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"), "1.0")

    def test_returns_none_when_neither_set(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"))

    def test_warns_once_per_process_when_falling_back(self):
        """The deprecation warning fires at most once for the same old_name,
        even across repeated calls - check_cost_budget_callback runs this on
        every single turn, so a per-call warning would spam stderr."""
        warnings = []
        with patch.dict("os.environ", {"OLD_NAME": "1.0"}, clear=True):
            with patch("builtins.print", side_effect=lambda *a, **k: warnings.append(a)):
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
        self.assertEqual(len(warnings), 1)

    def test_no_warning_when_new_name_is_used(self):
        warnings = []
        with patch.dict("os.environ", {"NEW_NAME": "5.0"}, clear=True):
            with patch("builtins.print", side_effect=lambda *a, **k: warnings.append(a)):
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
        self.assertEqual(len(warnings), 0)


if __name__ == "__main__":
    unittest.main()
