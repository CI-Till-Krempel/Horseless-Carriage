# agents/scrum_team/tests/test_versioning.py
"""
Unit tests for GH-501 (US-0025): SemVer increment enforcement and the
'-snapshot' development-suffix convention for the target product's own
version (agents/scrum_team/tools/versioning.py).
"""
import unittest
from unittest.mock import MagicMock

from agents.scrum_team.tools.versioning import (
    parse_version,
    version_core,
    is_snapshot,
    with_snapshot_suffix,
    strip_snapshot_suffix,
    classify_item_change_type,
    required_change_type,
    expected_bump,
    compute_expected_version_core,
    validate_version_increment,
    validate_snapshot_suffix,
    validate_version_bump,
    propose_version_bump,
    finalize_release_version,
    release_version_advisory,
    SNAPSHOT_SUFFIX,
)
from agents.scrum_team.state import ScrumState


class TestParseVersion(unittest.TestCase):
    def test_parses_bare_core(self):
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3, None))

    def test_parses_with_prerelease_suffix(self):
        self.assertEqual(parse_version("1.2.3-snapshot"), (1, 2, 3, "snapshot"))

    def test_parses_zero_version(self):
        self.assertEqual(parse_version("0.0.0"), (0, 0, 0, None))

    def test_rejects_missing_component(self):
        with self.assertRaises(ValueError):
            parse_version("1.2")

    def test_rejects_non_numeric_component(self):
        with self.assertRaises(ValueError):
            parse_version("1.x.3")

    def test_rejects_leading_zero(self):
        with self.assertRaises(ValueError):
            parse_version("01.2.3")

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            parse_version("")

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            parse_version(None)


class TestVersionCoreAndSnapshotHelpers(unittest.TestCase):
    def test_version_core_strips_suffix(self):
        self.assertEqual(version_core("1.2.3-snapshot"), "1.2.3")

    def test_version_core_passes_through_bare_core(self):
        self.assertEqual(version_core("1.2.3"), "1.2.3")

    def test_is_snapshot_true(self):
        self.assertTrue(is_snapshot("1.2.3-snapshot"))

    def test_is_snapshot_case_insensitive(self):
        self.assertTrue(is_snapshot("1.2.3-SNAPSHOT"))

    def test_is_snapshot_false_for_bare_core(self):
        self.assertFalse(is_snapshot("1.2.3"))

    def test_is_snapshot_false_for_other_prerelease(self):
        self.assertFalse(is_snapshot("1.2.3-rc.1"))

    def test_is_snapshot_false_for_malformed_version(self):
        self.assertFalse(is_snapshot("not-a-version"))

    def test_with_snapshot_suffix(self):
        self.assertEqual(with_snapshot_suffix("1.2.3"), "1.2.3-snapshot")

    def test_with_snapshot_suffix_rejects_existing_suffix(self):
        with self.assertRaises(ValueError):
            with_snapshot_suffix("1.2.3-rc.1")

    def test_strip_snapshot_suffix(self):
        self.assertEqual(strip_snapshot_suffix("1.2.3-snapshot"), "1.2.3")

    def test_strip_snapshot_suffix_noop_on_bare_core(self):
        self.assertEqual(strip_snapshot_suffix("1.2.3"), "1.2.3")


class TestClassifyItemChangeType(unittest.TestCase):
    def test_explicit_change_type_wins(self):
        item = {"type": "User Story", "change_type": "breaking"}
        self.assertEqual(classify_item_change_type(item), "breaking")

    def test_explicit_change_type_is_case_insensitive(self):
        item = {"type": "Issue", "change_type": "FEATURE"}
        self.assertEqual(classify_item_change_type(item), "feature")

    def test_invalid_explicit_change_type_falls_through_to_default(self):
        item = {"type": "Issue", "change_type": "not-a-real-type"}
        self.assertEqual(classify_item_change_type(item), "fix")

    def test_breaking_change_flag_promotes_story(self):
        item = {"type": "User Story", "breaking_change": True}
        self.assertEqual(classify_item_change_type(item), "breaking")

    def test_issue_defaults_to_fix(self):
        item = {"type": "Issue"}
        self.assertEqual(classify_item_change_type(item), "fix")

    def test_user_story_defaults_to_feature(self):
        item = {"type": "User Story"}
        self.assertEqual(classify_item_change_type(item), "feature")

    def test_epic_defaults_to_feature(self):
        item = {"type": "Epic"}
        self.assertEqual(classify_item_change_type(item), "feature")

    def test_non_dict_item_defaults_to_feature(self):
        self.assertEqual(classify_item_change_type("not a dict"), "feature")


class TestRequiredChangeType(unittest.TestCase):
    def test_none_when_nothing_done(self):
        backlog = [{"type": "User Story", "status": "Draft"}]
        self.assertIsNone(required_change_type(backlog))

    def test_none_for_empty_backlog(self):
        self.assertIsNone(required_change_type([]))

    def test_single_fix(self):
        backlog = [{"type": "Issue", "status": "Accepted"}]
        self.assertEqual(required_change_type(backlog), "fix")

    def test_feature_outranks_fix(self):
        backlog = [
            {"type": "Issue", "status": "Accepted"},
            {"type": "User Story", "status": "Accepted"},
        ]
        self.assertEqual(required_change_type(backlog), "feature")

    def test_breaking_outranks_everything(self):
        backlog = [
            {"type": "Issue", "status": "Accepted"},
            {"type": "User Story", "status": "Accepted"},
            {"type": "User Story", "status": "Accepted", "breaking_change": True},
        ]
        self.assertEqual(required_change_type(backlog), "breaking")

    def test_not_done_items_are_excluded(self):
        backlog = [
            {"type": "User Story", "status": "Accepted", "change_type": "fix"},
            {"type": "User Story", "status": "Implemented", "breaking_change": True},
        ]
        self.assertEqual(required_change_type(backlog), "fix")

    def test_legacy_done_status_counts(self):
        backlog = [{"type": "Issue", "status": "Done"}]
        self.assertEqual(required_change_type(backlog), "fix")


class TestExpectedBumpAndComputeExpectedVersionCore(unittest.TestCase):
    def test_expected_bump_mapping(self):
        self.assertEqual(expected_bump("breaking"), "major")
        self.assertEqual(expected_bump("feature"), "minor")
        self.assertEqual(expected_bump("fix"), "patch")

    def test_expected_bump_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            expected_bump("something-else")

    def test_compute_expected_version_core_major(self):
        self.assertEqual(compute_expected_version_core("1.2.3", "breaking"), "2.0.0")

    def test_compute_expected_version_core_minor(self):
        self.assertEqual(compute_expected_version_core("1.2.3", "feature"), "1.3.0")

    def test_compute_expected_version_core_patch(self):
        self.assertEqual(compute_expected_version_core("1.2.3", "fix"), "1.2.4")


class TestValidateVersionIncrement(unittest.TestCase):
    def test_correct_major_bump_for_breaking_change(self):
        result = validate_version_increment("1.2.3", "2.0.0", "breaking")
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["expected_increment"], "major")

    def test_correct_minor_bump_for_feature(self):
        result = validate_version_increment("1.2.3", "1.3.0", "feature")
        self.assertTrue(result["valid"])

    def test_correct_patch_bump_for_fix(self):
        result = validate_version_increment("1.2.3", "1.2.4", "fix")
        self.assertTrue(result["valid"])

    def test_minor_bump_rejected_for_breaking_change(self):
        result = validate_version_increment("1.2.3", "1.3.0", "breaking")
        self.assertFalse(result["valid"])
        self.assertEqual(result["expected_version_core"], "2.0.0")

    def test_patch_bump_rejected_for_feature(self):
        result = validate_version_increment("1.2.3", "1.2.4", "feature")
        self.assertFalse(result["valid"])
        self.assertEqual(result["expected_version_core"], "1.3.0")

    def test_major_bump_rejected_for_fix_only(self):
        result = validate_version_increment("1.2.3", "2.0.0", "fix")
        self.assertFalse(result["valid"])
        self.assertEqual(result["expected_version_core"], "1.2.4")

    def test_skipping_versions_rejected(self):
        # A fix bump must land exactly on the next patch, not an arbitrary
        # later one.
        result = validate_version_increment("1.2.3", "1.5.0", "fix")
        self.assertFalse(result["valid"])

    def test_no_change_type_only_requires_newer_version(self):
        result = validate_version_increment("1.2.3", "1.2.4", None)
        self.assertTrue(result["valid"])
        self.assertIsNone(result["expected_increment"])

    def test_no_change_type_rejects_non_newer_version(self):
        result = validate_version_increment("1.2.3", "1.2.3", None)
        self.assertFalse(result["valid"])

    def test_invalid_previous_version_is_rejected(self):
        result = validate_version_increment("not-a-version", "1.0.0", "fix")
        self.assertFalse(result["valid"])

    def test_invalid_proposed_version_is_rejected(self):
        result = validate_version_increment("1.0.0", "not-a-version", "fix")
        self.assertFalse(result["valid"])

    def test_ignores_prerelease_suffix_on_proposed_version(self):
        # Increment-only check: a '-snapshot' suffix on the proposed version
        # must not itself cause an increment mismatch - that's
        # validate_snapshot_suffix's job, not this function's.
        result = validate_version_increment("1.2.3", "1.3.0-snapshot", "feature")
        self.assertTrue(result["valid"])


class TestValidateSnapshotSuffix(unittest.TestCase):
    def test_requires_snapshot_suffix_when_expected(self):
        error = validate_snapshot_suffix("1.3.0", expect_snapshot=True)
        self.assertIsNotNone(error)
        self.assertIn("snapshot", error)

    def test_accepts_snapshot_suffix_when_expected(self):
        self.assertIsNone(validate_snapshot_suffix("1.3.0-snapshot", expect_snapshot=True))

    def test_rejects_non_snapshot_prerelease_when_snapshot_expected(self):
        error = validate_snapshot_suffix("1.3.0-rc.1", expect_snapshot=True)
        self.assertIsNotNone(error)

    def test_rejects_snapshot_suffix_at_release_time(self):
        error = validate_snapshot_suffix("1.3.0-snapshot", expect_snapshot=False)
        self.assertIsNotNone(error)

    def test_accepts_bare_core_at_release_time(self):
        self.assertIsNone(validate_snapshot_suffix("1.3.0", expect_snapshot=False))

    def test_rejects_malformed_version(self):
        error = validate_snapshot_suffix("not-a-version", expect_snapshot=True)
        self.assertIsNotNone(error)


class TestValidateVersionBump(unittest.TestCase):
    def test_valid_development_proposal(self):
        result = validate_version_bump("1.2.3", "1.3.0-snapshot", "feature", expect_snapshot=True)
        self.assertTrue(result["valid"])

    def test_wrong_increment_and_missing_suffix_both_reported(self):
        result = validate_version_bump("1.2.3", "1.2.4", "breaking", expect_snapshot=True)
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["errors"]), 2)

    def test_valid_release_time_version(self):
        result = validate_version_bump("1.2.3", "1.3.0", "feature", expect_snapshot=False)
        self.assertTrue(result["valid"])

    def test_release_time_rejects_lingering_snapshot_suffix(self):
        result = validate_version_bump("1.2.3", "1.3.0-snapshot", "feature", expect_snapshot=False)
        self.assertFalse(result["valid"])


def _state_with_sprint_backlog(backlog, **overrides):
    tool_context = MagicMock()
    state = ScrumState().model_dump()
    state["sprint_backlog"] = backlog
    state.update(overrides)
    tool_context.state = state
    return tool_context


class TestProposeVersionBump(unittest.TestCase):
    def test_accepts_correct_proposal_from_scratch(self):
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "User Story", "status": "Accepted"}]
        )
        result = propose_version_bump("0.1.0-snapshot", tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["change_type"], "feature")
        self.assertEqual(tool_context.state["product_version"], "0.1.0-snapshot")
        self.assertEqual(tool_context.state["product_version_change_type"], "feature")

    def test_rejects_wrong_increment(self):
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "User Story", "status": "Accepted", "breaking_change": True}],
            product_released_version="1.0.0",
        )
        result = propose_version_bump("1.1.0-snapshot", tool_context=tool_context)
        self.assertEqual(result["status"], "error")
        # Rejected proposal must not land in state - still whatever it was
        # before this call (the pristine ScrumState default).
        self.assertEqual(tool_context.state["product_version"], "")

    def test_rejects_missing_snapshot_suffix(self):
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "User Story", "status": "Accepted"}],
            product_released_version="1.0.0",
        )
        result = propose_version_bump("1.1.0", tool_context=tool_context)
        self.assertEqual(result["status"], "error")

    def test_requires_active_session(self):
        result = propose_version_bump("1.0.0-snapshot", tool_context=None)
        self.assertEqual(result["status"], "error")

    def test_defaults_previous_version_to_zero_when_never_released(self):
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "Issue", "status": "Accepted"}]
        )
        result = propose_version_bump("0.0.1-snapshot", tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["previous_version"], "0.0.0")


class TestFinalizeReleaseVersion(unittest.TestCase):
    def test_noop_when_nothing_proposed(self):
        tool_context = _state_with_sprint_backlog([])
        result = finalize_release_version(tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["finalized"])

    def test_strips_suffix_and_advances_baseline(self):
        tool_context = _state_with_sprint_backlog([], product_version="1.3.0-snapshot")
        result = finalize_release_version(tool_context=tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["finalized"])
        self.assertEqual(result["version"], "1.3.0")
        self.assertTrue(result["was_snapshot"])
        self.assertEqual(tool_context.state["product_version"], "1.3.0")
        self.assertEqual(tool_context.state["product_released_version"], "1.3.0")

    def test_noop_safe_with_no_session(self):
        result = finalize_release_version(tool_context=None)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["finalized"])

    def test_already_bare_core_is_finalized_as_is(self):
        tool_context = _state_with_sprint_backlog([], product_version="1.3.0")
        result = finalize_release_version(tool_context=tool_context)
        self.assertTrue(result["finalized"])
        self.assertFalse(result["was_snapshot"])
        self.assertEqual(result["version"], "1.3.0")


class TestReleaseVersionAdvisory(unittest.TestCase):
    def test_none_when_no_version_in_use(self):
        tool_context = _state_with_sprint_backlog([])
        self.assertIsNone(release_version_advisory(tool_context=tool_context))

    def test_none_when_increment_still_matches(self):
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "User Story", "status": "Accepted"}],
            product_version="0.1.0-snapshot",
            product_released_version="0.0.0",
        )
        self.assertIsNone(release_version_advisory(tool_context=tool_context))

    def test_flags_when_backlog_outgrew_proposed_version(self):
        # Proposed as a feature (minor) bump, but a breaking change landed
        # in the backlog afterward - now requires major.
        tool_context = _state_with_sprint_backlog(
            [{"id": "US-0001", "type": "User Story", "status": "Accepted", "breaking_change": True}],
            product_version="1.1.0-snapshot",
            product_released_version="1.0.0",
        )
        warning = release_version_advisory(tool_context=tool_context)
        self.assertIsNotNone(warning)
        self.assertIn("2.0.0", warning)

    def test_none_without_active_session(self):
        self.assertIsNone(release_version_advisory(tool_context=None))


if __name__ == "__main__":
    unittest.main()
