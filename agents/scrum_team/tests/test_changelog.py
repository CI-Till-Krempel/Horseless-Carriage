# agents/scrum_team/tests/test_changelog.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.changelog import (
    derive_changelog_entries,
    generate_changelog_entry,
    generate_changelog,
    CHANGELOG_PATH,
)
from agents.scrum_team.state import ScrumState


class TestDeriveChangelogEntries(unittest.TestCase):
    """
    Acceptance Criteria (US-0013): changelog line items are derived from
    sprint_backlog - only Done/Accepted items surface, and an item missing
    a title falls back to its bare ID rather than being silently omitted.
    """

    def test_only_done_items_are_surfaced(self):
        sprint_backlog = [
            {"id": "US-0001", "title": "Add login", "status": "Accepted"},
            {"id": "US-0002", "title": "Add logout", "status": "Implemented"},
            {"id": "US-0003", "title": "Add signup", "status": "Draft"},
        ]
        entries = derive_changelog_entries(sprint_backlog)
        self.assertEqual(entries, ["- US-0001: Add login"])

    def test_legacy_done_status_also_counts(self):
        sprint_backlog = [{"id": "US-0001", "title": "Add login", "status": "Done"}]
        entries = derive_changelog_entries(sprint_backlog)
        self.assertEqual(entries, ["- US-0001: Add login"])

    def test_missing_title_falls_back_to_id(self):
        sprint_backlog = [{"id": "US-0004", "status": "Accepted"}]
        entries = derive_changelog_entries(sprint_backlog)
        self.assertEqual(entries, ["- US-0004"])

    def test_missing_id_falls_back_to_title(self):
        sprint_backlog = [{"title": "Add login", "status": "Accepted"}]
        entries = derive_changelog_entries(sprint_backlog)
        self.assertEqual(entries, ["- Add login"])

    def test_item_already_in_existing_changelog_text_is_skipped(self):
        """
        sprint_backlog is never reset between sprints, so without this a
        once-shipped item would be re-listed in every later release's
        entry forever.
        """
        sprint_backlog = [{"id": "US-0001", "title": "Add login", "status": "Accepted"}]
        existing = "## Sprint 1 - 2026-01-01\n\n- US-0001: Add login\n"
        entries = derive_changelog_entries(sprint_backlog, existing_changelog_text=existing)
        self.assertEqual(entries, [])


class TestGenerateChangelogEntry(unittest.TestCase):
    """Acceptance Criteria (US-0012): a dated, versioned entry, honest about an empty sprint."""

    def test_entry_has_sprint_and_date_header(self):
        entry = generate_changelog_entry(3, [{"id": "US-0001", "title": "Add login", "status": "Accepted"}])
        self.assertTrue(entry.startswith("## Sprint 3 - "))
        self.assertIn("- US-0001: Add login", entry)

    def test_empty_sprint_produces_honest_minimal_entry(self):
        entry = generate_changelog_entry(1, [])
        self.assertIn("No user-facing changes", entry)


class TestGenerateChangelog(unittest.TestCase):
    """
    Acceptance Criteria (US-0012): a new entry is prepended to CHANGELOG.md,
    creating it (with a standard header) on first use, in reverse-
    chronological order across releases.
    """

    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp())
        patcher = patch("agents.scrum_team.tools.changelog._configured_repo_root", return_value=self.repo_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher2 = patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=self.repo_root)
        patcher2.start()
        self.addCleanup(patcher2.stop)
        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()
        self.tool_context.agent_name = "ProductOwner"

    def test_creates_changelog_file_on_first_use(self):
        self.tool_context.state["sprint_number"] = 1
        self.tool_context.state["sprint_backlog"] = [
            {"id": "US-0001", "title": "Add login", "status": "Accepted"}
        ]
        result = generate_changelog(tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        content = (self.repo_root / CHANGELOG_PATH).read_text(encoding="utf-8")
        self.assertIn("# Changelog", content)
        self.assertIn("## Sprint 1", content)
        self.assertIn("US-0001: Add login", content)

    def test_second_release_prepends_in_reverse_chronological_order(self):
        self.tool_context.state["sprint_number"] = 1
        self.tool_context.state["sprint_backlog"] = [
            {"id": "US-0001", "title": "Add login", "status": "Accepted"}
        ]
        generate_changelog(tool_context=self.tool_context)

        self.tool_context.state["sprint_number"] = 2
        self.tool_context.state["sprint_backlog"].append(
            {"id": "US-0002", "title": "Add logout", "status": "Accepted"}
        )
        generate_changelog(tool_context=self.tool_context)

        content = (self.repo_root / CHANGELOG_PATH).read_text(encoding="utf-8")
        sprint1_idx = content.index("## Sprint 1")
        sprint2_idx = content.index("## Sprint 2")
        self.assertLess(sprint2_idx, sprint1_idx)
        # US-0001 shipped in sprint 1, not re-listed under sprint 2's own entry.
        self.assertEqual(content.count("US-0001: Add login"), 1)

    def test_records_touched_file_for_release_diff_verification(self):
        """US-0014: write_file already records this path in sprint_files_touched."""
        self.tool_context.state["sprint_number"] = 1
        generate_changelog(tool_context=self.tool_context)
        self.assertIn(CHANGELOG_PATH, self.tool_context.state["sprint_files_touched"])
