# agents/scrum_team/tests/test_docs.py
import subprocess
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
    _heal_gitignore_drift,
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
        Acceptance Criteria (SPEC-ISSUE-0008): overwriting a file whose existing
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

    def test_write_file_flags_content_loss_when_overwrite_removes_definitions(self):
        """
        Acceptance Criteria (SPEC-ISSUE-0075): a real eval run overwrote
        app.py wholesale for one story, silently deleting every route five
        prior, already-Accepted stories' acceptance criteria depended on -
        write_file must surface this, not just that content changed at all.
        """
        write_file(
            "app.py",
            "@app.route('/lists', methods=['POST'])\ndef create_list():\n    pass\n"
            "\n@app.route('/lists/<id>/tasks', methods=['POST'])\ndef add_task(id):\n    pass\n",
            tool_context=self.tool_context,
        )
        result = write_file(
            "app.py",
            "@app.route('/')\ndef index():\n    pass\n",
            overwrite=True,
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("content_loss_warning", result)
        self.assertEqual(
            self.tool_context.state["content_loss_warnings"],
            [{"path": "app.py", "previous_definition_count": 4, "new_definition_count": 2}],
        )

    def test_write_file_does_not_flag_content_loss_when_overwrite_adds_definitions(self):
        write_file("app.py", "def index():\n    pass\n", tool_context=self.tool_context)
        result = write_file(
            "app.py",
            "def index():\n    pass\n\ndef create_list():\n    pass\n",
            overwrite=True,
            tool_context=self.tool_context,
        )
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("content_loss_warning", result)
        self.assertEqual(self.tool_context.state.get("content_loss_warnings", []), [])

    def test_write_file_does_not_flag_content_loss_for_a_brand_new_file(self):
        result = write_file("notes/new.md", "just some prose, no code", tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("content_loss_warning", result)
        self.assertEqual(self.tool_context.state.get("content_loss_warnings", []), [])

    def test_write_file_does_not_flag_content_loss_for_an_identical_overwrite(self):
        write_file("app.py", "def index():\n    pass\n", tool_context=self.tool_context)
        result = write_file("app.py", "def index():\n    pass\n", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("content_loss_warning", result)
        self.assertEqual(self.tool_context.state.get("content_loss_warnings", []), [])

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

    def test_write_file_increments_source_content_change_count_for_a_new_source_file(self):
        write_file("app.py", "print('hi')", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["source_content_change_count"], 1)

    def test_write_file_increments_source_content_change_count_on_each_genuine_rewrite_of_the_same_path(self):
        """
        SPEC-ISSUE-0074 (0.1.0-run76): unlike sprint_files_touched (which
        dedupes by path for the whole run, so a second write to the SAME
        path never grows it), source_content_change_count must keep growing
        for as long as a path's content keeps genuinely changing - the
        overwhelmingly common case for a small, stable set of source files
        reused across many stories.
        """
        write_file("app.py", "v1", tool_context=self.tool_context)
        write_file("app.py", "v2", overwrite=True, tool_context=self.tool_context)
        write_file("app.py", "v3", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["source_content_change_count"], 3)
        # sprint_files_touched itself is untouched by this fix - still
        # deduped by path, for its own different consumer.
        self.assertEqual(self.tool_context.state["sprint_files_touched"], ["app.py"])

    def test_write_file_does_not_increment_source_content_change_count_for_identical_overwrite(self):
        write_file("app.py", "same", tool_context=self.tool_context)
        write_file("app.py", "same", overwrite=True, tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state["source_content_change_count"], 1)

    def test_write_file_does_not_increment_source_content_change_count_for_a_spec_path(self):
        write_file("specs/stories/US-0001.md", "content", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state.get("source_content_change_count", 0), 0)

    def test_delete_file_removes_it_from_disk(self):
        write_file("tests/test_todo_unit.py", "broken import", tool_context=self.tool_context)
        result = delete_file("tests/test_todo_unit.py", tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((self.repo_root / "tests/test_todo_unit.py").exists())

    def test_delete_file_records_touched_path(self):
        write_file("tests/test_todo_unit.py", "broken import", tool_context=self.tool_context)
        delete_file("tests/test_todo_unit.py", tool_context=self.tool_context)
        self.assertIn("tests/test_todo_unit.py", self.tool_context.state["sprint_files_touched"])

    def test_delete_file_increments_source_content_change_count(self):
        write_file("tests/test_todo_unit.py", "broken import", tool_context=self.tool_context)
        delete_file("tests/test_todo_unit.py", tool_context=self.tool_context)
        # One from the write_file above, one from this delete.
        self.assertEqual(self.tool_context.state["source_content_change_count"], 2)

    def test_delete_file_does_not_increment_source_content_change_count_for_a_spec_path(self):
        write_file("specs/stories/US-0001.md", "content", tool_context=self.tool_context)
        delete_file("specs/stories/US-0001.md", tool_context=self.tool_context)
        self.assertEqual(self.tool_context.state.get("source_content_change_count", 0), 0)

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

    def test_write_file_refuses_a_path_under_hc(self):
        """SPEC-ISSUE-0064: .hc/ is Horseless-Carriage-internal (state.json via
        save_state_to_repo, workbench-setup.sh via propose_framework_change)
        - never a direct write_file target."""
        result = write_file(".hc/workbench-setup.sh", "RUN apt-get install -y default-jdk", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("propose_framework_change", result["message"])
        self.assertFalse((self.repo_root / ".hc" / "workbench-setup.sh").exists())

    def test_write_file_refuses_a_nested_path_under_hc(self):
        result = write_file(".hc/some/nested/file.txt", "content", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn("propose_framework_change", result["message"])

    def test_write_file_allows_paths_that_merely_start_with_hc(self):
        """A real sibling directory/file that happens to share .hc's prefix
        (e.g. '.hc-notes/foo.md') must not be caught by the .hc/ guardrail -
        it's a different path, not a subpath of .hc/."""
        result = write_file(".hc-notes/foo.md", "content", tool_context=self.tool_context)
        self.assertEqual(result["status"], "ok")

    def test_delete_file_refuses_a_path_under_hc(self):
        (self.repo_root / ".hc").mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".hc" / "state.json").write_text("{}")
        result = delete_file(".hc/state.json", tool_context=self.tool_context)
        self.assertEqual(result["status"], "error")
        self.assertIn(".hc/", result["message"])
        self.assertTrue((self.repo_root / ".hc" / "state.json").exists())

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


class TestHealGitignoreDrift(unittest.TestCase):
    """
    Acceptance Criteria (GH issue, 0.1.0-run58): seed_repository's own
    .gitignore-on-first-seed fix (GH #419) only ever helps a brand-new
    project's very first commit - it never re-visits a project that
    already looks seeded. A long-lived project (the eval harness's own
    standing target repo, reused run after run rather than recreated
    fresh) can have been first seeded before .gitignore existed at all, or
    accumulated a tracked build artifact before .gitignore started
    covering it - that drift otherwise persists forever. _heal_gitignore_
    drift is a mechanical, best-effort fix for exactly that: ensure
    .gitignore exists and untrack anything already-tracked that it now
    covers.
    """

    def setUp(self):
        self.remote = Path(tempfile.mkdtemp(prefix="hc-test-remote-"))
        self.clone = Path(tempfile.mkdtemp(prefix="hc-test-clone-"))
        subprocess.run(["git", "init", "-q", "--bare"], cwd=self.remote, check=True)
        subprocess.run(["git", "init", "-q"], cwd=self.clone, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.clone, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.clone, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.clone, check=True)

        # A tracked build artifact, as if it had been committed before this
        # project ever had a .gitignore - the exact drift this heals.
        pycache_dir = self.clone / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "app.cpython-311.pyc").write_bytes(b"not real bytecode")
        (self.clone / "README.md").write_text("# Test project\n")
        subprocess.run(["git", "add", "-A"], cwd=self.clone, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial commit, no .gitignore yet"], cwd=self.clone, check=True)
        subprocess.run(["git", "branch", "-M", "develop"], cwd=self.clone, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "develop"], cwd=self.clone, check=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.remote, ignore_errors=True)
        shutil.rmtree(self.clone, ignore_errors=True)

    def _tool_context(self):
        tool_context = MagicMock()
        tool_context.state = {}
        tool_context.agent_name = "ScrumMaster"
        return tool_context

    @patch("agents.scrum_team.tools.docs._configured_repo_root")
    def test_heals_an_already_tracked_file_the_default_gitignore_now_covers(self, mock_repo_root):
        mock_repo_root.return_value = self.clone

        result = _heal_gitignore_drift(self._tool_context())

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["healed"])
        self.assertIn(".gitignore", result["changed"])
        self.assertIn("__pycache__/app.cpython-311.pyc", result["changed"])

        gitignore_content = (self.clone / ".gitignore").read_text()
        self.assertIn("__pycache__", gitignore_content)

        # Untracked from git's index, but the actual file is left alone on disk.
        ls_files = subprocess.run(
            ["git", "ls-files", "__pycache__/"], cwd=self.clone, capture_output=True, text=True
        ).stdout
        self.assertEqual(ls_files.strip(), "")
        self.assertTrue((self.clone / "__pycache__" / "app.cpython-311.pyc").exists())

        # And it actually reached origin, not just the local clone.
        remote_log = subprocess.run(
            ["git", "log", "--oneline", "origin/develop"], cwd=self.clone, capture_output=True, text=True
        ).stdout
        self.assertIn("untrack build/test artifacts", remote_log)

    @patch("agents.scrum_team.tools.docs._configured_repo_root")
    def test_is_a_no_op_once_already_healed(self, mock_repo_root):
        mock_repo_root.return_value = self.clone

        _heal_gitignore_drift(self._tool_context())
        result = _heal_gitignore_drift(self._tool_context())

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["healed"])

    @patch("agents.scrum_team.tools.docs._configured_repo_root")
    def test_no_op_when_theres_no_git_repo_yet(self, mock_repo_root):
        with tempfile.TemporaryDirectory() as plain_dir:
            mock_repo_root.return_value = Path(plain_dir)
            result = _heal_gitignore_drift(self._tool_context())

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["healed"])

    @patch("agents.scrum_team.tools.docs._configured_repo_root")
    def test_no_op_when_develop_does_not_exist_on_origin_yet(self, mock_repo_root):
        """A brand-new project mid-bootstrap (configure_github_repo has run,
        seed_repository/its first push hasn't yet) has no develop branch on
        origin at all - that's normal setup, not drift, and must not error."""
        fresh_remote = Path(tempfile.mkdtemp(prefix="hc-test-remote-empty-"))
        fresh_clone = Path(tempfile.mkdtemp(prefix="hc-test-clone-empty-"))
        try:
            subprocess.run(["git", "init", "-q", "--bare"], cwd=fresh_remote, check=True)
            subprocess.run(["git", "init", "-q"], cwd=fresh_clone, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=fresh_clone, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=fresh_clone, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(fresh_remote)], cwd=fresh_clone, check=True)

            mock_repo_root.return_value = fresh_clone
            result = _heal_gitignore_drift(self._tool_context())

            self.assertEqual(result["status"], "ok")
            self.assertFalse(result["healed"])
        finally:
            import shutil
            shutil.rmtree(fresh_remote, ignore_errors=True)
            shutil.rmtree(fresh_clone, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()