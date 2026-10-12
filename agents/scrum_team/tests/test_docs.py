# agents/scrum_team/tests/test_docs.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.docs import (
    read_doc,
    write_file,
    delete_file,
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

    def test_write_file_increments_dependency_manifest_write_count_for_requirements_txt(self):
        write_file("requirements.txt", "flask\n", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["dependency_manifest_write_count"], 1)

    def test_write_file_increments_dependency_manifest_write_count_for_package_json(self):
        write_file("package.json", "{}", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["dependency_manifest_write_count"], 1)

    def test_write_file_increments_on_every_rewrite_not_just_the_first(self):
        write_file("requirements.txt", "flask\n", tool_context=self.tool_context)
        write_file("requirements.txt", "\n", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["dependency_manifest_write_count"], 2)

    def test_write_file_does_not_increment_for_a_nested_requirements_txt(self):
        """Only the root-level manifest is what check_build actually
        installs from - a same-named file elsewhere in the tree isn't
        the one the Tested gate's staleness check cares about."""
        write_file("subdir/requirements.txt", "flask\n", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state.get("dependency_manifest_write_count", 0), 0)

    def test_write_file_does_not_increment_for_unrelated_files(self):
        write_file("notes/foo.md", "content", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state.get("dependency_manifest_write_count", 0), 0)

    def test_delete_file_removes_it_from_disk(self):
        write_file("tests/test_todo_unit.py", "broken import", tool_context=self.tool_context)
        result = delete_file("tests/test_todo_unit.py", tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((self.repo_root / "tests/test_todo_unit.py").exists())

    def test_delete_file_records_touched_path(self):
        write_file("tests/test_todo_unit.py", "broken import", tool_context=self.tool_context)
        delete_file("tests/test_todo_unit.py", tool_context=self.tool_context)
        self.assertIn("tests/test_todo_unit.py", self.tool_context.state["sprint_files_touched"])

    def test_delete_file_errors_on_a_path_that_does_not_exist(self):
        result = delete_file("tests/nope.py", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", result["message"])

    def test_delete_file_refuses_a_directory(self):
        (self.repo_root / "tests").mkdir(parents=True, exist_ok=True)
        result = delete_file("tests", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("directory", result["message"])

    def test_delete_file_refuses_a_path_outside_the_repo_root(self):
        result = delete_file("../outside.py", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("outside the repository root", result["message"])

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


class TestSeedRepositoryGitignore(unittest.TestCase):
    """
    Acceptance Criteria (GH #419): two separate real eval runs each hit a
    GitFlow checkout hard-failing with git's "untracked working tree files
    would be overwritten by checkout" - instance/todo.db in one,
    .coverage/__pycache__/*.pyc in the other - because the generated
    project had no .gitignore at all, so DevTeam/QA's own normal local test
    runs left real, untracked build/test artifacts in the working tree for
    a later checkout to trip over. seed_repository now creates one from the
    project's very first commit, preventing the whole class of failure
    rather than patching one artifact type at a time.
    """

    @patch("agents.scrum_team.tools.github._git_push_impl", return_value={"status": "ok"})
    def test_creates_a_gitignore_on_first_seed(self, mock_git_push_impl):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=Path(tmp_dir)):
                result = seed_repository(tool_context=tool_context)
            gitignore_content = (Path(tmp_dir) / ".gitignore").read_text()

        self.assertEqual(result["status"], "ok")
        self.assertIn(".gitignore", result["seeded"])
        # The two real failures this closes, by name.
        self.assertIn("__pycache__", gitignore_content)
        self.assertIn(".coverage", gitignore_content)
        self.assertIn("instance/", gitignore_content)

    @patch("agents.scrum_team.tools.github._git_push_impl", return_value={"status": "ok"})
    def test_does_not_overwrite_an_existing_gitignore_by_default(self, mock_git_push_impl):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / ".gitignore").write_text("# custom, team-written rules\n")
            with patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=Path(tmp_dir)):
                result = seed_repository(tool_context=tool_context)
            gitignore_content = (Path(tmp_dir) / ".gitignore").read_text()

        self.assertNotIn(".gitignore", result.get("seeded", []))
        self.assertEqual(gitignore_content, "# custom, team-written rules\n")

    @patch("agents.scrum_team.tools.github._git_push_impl", return_value={"status": "ok"})
    def test_overwrite_flag_does_replace_an_existing_gitignore(self, mock_git_push_impl):
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()

        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / ".gitignore").write_text("# stale rules\n")
            with patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=Path(tmp_dir)):
                result = seed_repository(overwrite=True, tool_context=tool_context)
            gitignore_content = (Path(tmp_dir) / ".gitignore").read_text()

        self.assertIn(".gitignore", result["seeded"])
        self.assertIn("__pycache__", gitignore_content)


if __name__ == "__main__":
    unittest.main()