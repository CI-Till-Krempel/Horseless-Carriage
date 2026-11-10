# agents/scrum_team/tests/test_announcements.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.announcements import (
    draft_announcement_from_changelog,
    generate_announcement,
)
from agents.scrum_team.tools.changelog import CHANGELOG_PATH
from agents.scrum_team.state import ScrumState


class TestDraftAnnouncementFromChangelog(unittest.TestCase):
    """
    Acceptance Criteria (US-0016): internal-only changelog items (refactors,
    test additions) are excluded from the customer-facing draft.
    """

    def test_user_facing_items_are_included(self):
        entry = "## Sprint 1 - 2026-01-01\n\n- US-0001: Add login\n"
        draft = draft_announcement_from_changelog(entry, "Sprint 1")
        self.assertIn("US-0001: Add login", draft)
        self.assertIn("Release: Sprint 1", draft)

    def test_internal_only_items_are_excluded(self):
        entry = (
            "## Sprint 1 - 2026-01-01\n\n"
            "- US-0001: Add login\n"
            "- US-0002: Refactor auth internals\n"
            "- US-0003: Add tests for login flow\n"
        )
        draft = draft_announcement_from_changelog(entry, "Sprint 1")
        self.assertIn("US-0001: Add login", draft)
        self.assertNotIn("Refactor auth internals", draft)
        self.assertNotIn("Add tests for login flow", draft)

    def test_all_internal_only_produces_honest_minimal_draft(self):
        entry = "## Sprint 1 - 2026-01-01\n\n- US-0002: Refactor auth internals\n"
        draft = draft_announcement_from_changelog(entry, "Sprint 1")
        self.assertIn("No user-facing changes", draft)


class TestGenerateAnnouncement(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp())
        patcher = patch("agents.scrum_team.tools.announcements._configured_repo_root", return_value=self.repo_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher2 = patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=self.repo_root)
        patcher2.start()
        self.addCleanup(patcher2.stop)
        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()
        self.tool_context.agent_name = "ProductOwner"

    def test_no_changelog_yet_reports_error_instead_of_fabricating(self):
        result = generate_announcement(tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("CHANGELOG.md", result["message"])

    def test_drafts_from_existing_changelog(self):
        (self.repo_root / CHANGELOG_PATH).write_text(
            "# Changelog\n\n## Sprint 1 - 2026-01-01\n\n- US-0001: Add login\n",
            encoding="utf-8",
        )
        self.tool_context.state["sprint_number"] = 1

        result = generate_announcement(tool_context=self.tool_context)

        self.assertEqual(result["status"], "ok")
        written = (self.repo_root / "specs/announcements/ANNOUNCEMENT-sprint-1.md").read_text(encoding="utf-8")
        self.assertIn("US-0001: Add login", written)
