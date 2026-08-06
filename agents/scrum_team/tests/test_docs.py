# agents/scrum_team/tests/test_docs.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.docs import (
    read_doc,
    write_file,
    upsert_prd,
    upsert_srs,
    upsert_adr,
    create_from_template,
    seed_repository,
)
from agents.scrum_team.state import ScrumState


class TestDocsTools(unittest.TestCase):
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_read_doc(self, mock_read_text, mock_exists):
        """
        Acceptance Criteria:
        - A document is read from the file system.
        """
        mock_exists.return_value = True
        mock_read_text.return_value = "This is a test document."
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        content = read_doc("spec-templates/test.md", tool_context=tool_context)
        self.assertEqual(content["content"], "This is a test document.")

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_upsert_prd(self, mock_write_file):
        """
        Acceptance Criteria:
        - A PRD is created or updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        upsert_prd("This is a PRD.", "test.md", tool_context=tool_context)
        mock_write_file.assert_called_with("specs/requirements/PRD-test.md", "This is a PRD.", overwrite=True, tool_context=tool_context)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_upsert_srs(self, mock_write_file):
        """
        Acceptance Criteria:
        - An SRS is created or updated.
        """
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        upsert_srs("This is an SRS.", "test.md", tool_context=tool_context)
        mock_write_file.assert_called_with("specs/requirements/SRS-test.md", "This is an SRS.", overwrite=True, tool_context=tool_context)


class TestSprintFilesTouched(unittest.TestCase):
    """
    Acceptance Criteria (US-0009):
    - Every write path in tools/docs.py (upsert_prd, upsert_srs, upsert_adr,
      create_from_template - all of which funnel through write_file)
      records the repo-relative path in ScrumState.sprint_files_touched.
    """

    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp())
        patcher = patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=self.repo_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool_context = MagicMock()
        self.tool_context.state = ScrumState().model_dump()
        self.tool_context.agent_name = "Architect"

    def test_write_file_records_touched_path(self):
        write_file("specs/requirements/PRD-test.md", "content", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["sprint_files_touched"], ["specs/requirements/PRD-test.md"])

    def test_write_file_does_not_duplicate_repeated_writes(self):
        write_file("specs/requirements/PRD-test.md", "v1", tool_context=self.tool_context)
        write_file("specs/requirements/PRD-test.md", "v2", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["sprint_files_touched"], ["specs/requirements/PRD-test.md"])

    def test_write_file_flags_overwrite_of_different_content(self):
        """
        Acceptance Criteria (ISSUE-0008): overwriting a file whose existing
        content differs is surfaced, not silently clobbered.
        """
        write_file("notes/foo.md", "original", tool_context=self.tool_context)
        result = write_file("notes/foo.md", "changed", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["overwrote_existing_content"])

    def test_write_file_overwrite_with_identical_content_is_not_flagged(self):
        write_file("notes/foo.md", "same", tool_context=self.tool_context)
        result = write_file("notes/foo.md", "same", overwrite=True, tool_context=self.tool_context)
        self.assertFalse(result["overwrote_existing_content"])

    def test_write_file_new_file_is_not_flagged(self):
        result = write_file("notes/new.md", "content", tool_context=self.tool_context)
        self.assertFalse(result["overwrote_existing_content"])

    def test_upsert_prd_records_touched_path(self):
        upsert_prd("This is a PRD.", "test.md", tool_context=self.tool_context)
        self.assertIn("specs/requirements/PRD-test.md", self.tool_context.state["sprint_files_touched"])

    def test_upsert_srs_records_touched_path(self):
        upsert_srs("This is an SRS.", "test.md", tool_context=self.tool_context)
        self.assertIn("specs/requirements/SRS-test.md", self.tool_context.state["sprint_files_touched"])

    def test_upsert_adr_records_touched_path(self):
        result = upsert_adr(
            title="Test Decision",
            context="ctx",
            decision="dec",
            consequences="cons",
            adr_id="ADR-0099",
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("specs/architecture/ADR-0099-Test-Decision.md", self.tool_context.state["sprint_files_touched"])

    def test_create_from_template_records_touched_path(self):
        result = create_from_template(
            template_path="spec-templates/stories/TEMPLATE-USER-STORY.md",
            destination_path="specs/stories/US-TEST.md",
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("specs/stories/US-TEST.md", self.tool_context.state["sprint_files_touched"])

    def test_no_writes_yet_touched_list_is_empty(self):
        """
        Acceptance Criteria (US-0009):
        - A sprint with no writes has sprint_files_touched as an empty
          list, not missing/undefined.
        """
        self.assertEqual(ScrumState().model_dump()["sprint_files_touched"], [])


class TestSeedRepositoryBranch(unittest.TestCase):
    """
    Acceptance Criteria (GitFlow): seed_repository's initial commit must
    land on the configured develop branch, not a hardcoded "develop" or
    the default/main branch, so an isolated eval run doesn't contaminate
    the eval repo's real develop/main - all work starts on develop, main
    stays at the pre-seed state until the first sprint PR merges.
    """

    @patch("agents.scrum_team.tools.github._git_push_impl")
    def test_seed_repository_pushes_to_configured_develop_branch(self, mock_git_push_impl):
        """seed_repository uses _git_push_impl (not the public git_push tool)
        since it's the one legitimate internal case that needs
        allow_protected=True - see git_push's own docstring."""
        mock_git_push_impl.return_value = {"status": "ok"}
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["repo"] = {"default_branch": "eval/run-1/main", "develop_branch": "eval/run-1/develop"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=Path(tmp_dir)):
                seed_repository(tool_context=tool_context)

        mock_git_push_impl.assert_called_once()
        self.assertEqual(mock_git_push_impl.call_args.kwargs["branch"], "eval/run-1/develop")
        self.assertEqual(mock_git_push_impl.call_args.kwargs["allow_protected"], True)


if __name__ == "__main__":
    unittest.main()