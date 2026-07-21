# agents/scrum_team/tests/test_migrations.py
import unittest
from unittest.mock import patch

from agents.scrum_team.tools.migrations import migrate_state, _version_lt


class TestMigrations(unittest.TestCase):
    def test_migrate_state_is_a_noop_today(self):
        """
        Acceptance Criteria (release process, see RELEASE.md "Migration
        scaffold"): no breaking ScrumState change exists yet, so
        MIGRATIONS is empty and migrate_state returns the state unchanged.
        """
        state = {"hc_version": "0.1.0", "product_backlog": ["untouched"]}
        self.assertEqual(migrate_state(state, "0.1.0"), state)

    def test_migrate_state_applies_registered_migrations_in_order(self):
        calls = []
        fake_migrations = [
            ("0.2.0", lambda s: calls.append("a") or {**s, "a": True}),
            ("0.3.0", lambda s: calls.append("b") or {**s, "b": True}),
        ]
        with patch("agents.scrum_team.tools.migrations.MIGRATIONS", fake_migrations):
            result = migrate_state({"hc_version": "0.1.0"}, "0.1.0")

        self.assertEqual(calls, ["a", "b"])
        self.assertTrue(result["a"])
        self.assertTrue(result["b"])

    def test_migrate_state_skips_migrations_not_newer_than_from_version(self):
        fake_migrations = [("0.1.0", lambda s: {**s, "should_not_run": True})]
        with patch("agents.scrum_team.tools.migrations.MIGRATIONS", fake_migrations):
            result = migrate_state({"hc_version": "0.1.0"}, "0.1.0")

        self.assertNotIn("should_not_run", result)

    def test_version_lt_compares_numerically_not_lexicographically(self):
        # A naive string comparison would get this backwards ("0.10.0" < "0.9.0").
        self.assertTrue(_version_lt("0.9.0", "0.10.0"))
        self.assertFalse(_version_lt("0.10.0", "0.9.0"))


if __name__ == "__main__":
    unittest.main()
