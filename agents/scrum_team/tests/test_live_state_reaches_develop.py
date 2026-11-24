"""
SPEC-ISSUE-0094: live session state must actually reach origin/develop when a
sprint closes, even if the working tree is sitting on an unmerged feature
branch at the time.

A real eval run (0.2.1-run95) went like this. advance_story_stage's GH #383
state_push sent US-0002's Implemented stage to its feature branch and left
the tree checked out there. Every later save_state_to_repo checkpoint then
landed on that never-merged branch. Both close-out paths (create_release_pr's
land step, agent.py's _ensure_sprint_report_on_final_halt) did `checkout -B
develop origin/develop`, which silently swapped in develop's stale
.hc/state.json. They re-rendered only the report files from live state and
pushed, so the stale state shipped. The next sprint loaded it:

- the story's Implemented stage was gone;
- current_sprint_phase still read 'Development', so start_sprint was refused;
- the sprint number and per-sprint budget never reset.

These tests use a real bare `origin` and real git, and only stub `gh`. They
assert on what's actually on origin/develop, never the working tree, because
the working tree is exactly what masks this bug locally.
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.scrum_team.state import ScrumState
from agents.scrum_team.helpers import current_sprint_phase
from agents.scrum_team.tools import github as github_module
from agents.scrum_team.tools.github import create_release_pr
from agents.scrum_team.tools.scrum import save_state_to_repo

FEATURE = "feature/US-0002-data-model"
LIVE_TOKENS = 6_172_370


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _stale_develop_state():
    """What develop's .hc/state.json looked like in run95 (commit a7566072):
    the story only Ready, sprint 1's backlog PR still recorded as current,
    no sprint report, token usage under budget."""
    s = ScrumState().model_dump()
    s["sprint_number"] = 1
    s["sprint_backlog_pr_sprint"] = 1
    s["sprint_backlog_pr_opened"] = True
    s["sprint_report"] = ""
    s["budgets"] = {"total": 5_000_000, "total_usd": 15.0}
    s["token_usage"] = {"total": 4_375_611, "agents": {}}
    s["sprint_backlog"] = [_story(["Draft", "Ready"])]
    s["product_backlog"] = [_story(["Draft", "Ready"])]
    return s


def _story(stages):
    return {
        "id": "US-0002",
        "title": "Implement Data Model for Lists and Tasks",
        "type": "User Story",
        "priority": "Must",
        "version": "0.1.0",
        "user_story": "As the system, I need a data model for lists and tasks.",
        "acceptance_criteria": "- Lists and tasks can be created and deleted",
        "stages_completed": list(stages),
        "status": stages[-1],
    }


@pytest.fixture
def repo(_isolated_repo_root, tmp_path, monkeypatch):
    """A bare origin with main+develop carrying the STALE state, a pushed
    feature branch, and a local clone (at the conftest-isolated repo root)
    left checked out on that feature branch - exactly where run95's tree was
    when its sprint closed."""
    for var in ("INTERNAL_STATE_REPO_PATH", "EVAL_RUN_ID", "GITHUB_DEVELOP_BRANCH", "GITHUB_REPO_BRANCH"):
        monkeypatch.delenv(var, raising=False)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    clone = _isolated_repo_root
    _git(clone.parent, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    monkeypatch.setenv("STATE_REPO_PATH", str(clone))

    (clone / ".hc").mkdir()
    (clone / ".hc" / "state.json").write_text(json.dumps(_stale_develop_state()))
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "seed")
    _git(clone, "push", "-q", "origin", "HEAD:main")
    _git(clone, "checkout", "-q", "-b", "develop")
    _git(clone, "push", "-q", "-u", "origin", "develop")
    _git(clone, "checkout", "-q", "-b", FEATURE)
    _git(clone, "push", "-q", "-u", "origin", FEATURE)
    return {"origin": origin, "clone": clone}


def _live_context():
    """Live session state at the end of the sprint: the story reached
    Implemented on its (unmerged) feature branch, sprint report authored,
    budget spent."""
    tool_context = MagicMock()
    tool_context.agent_name = None
    s = _stale_develop_state()
    s["repo"] = {"default_branch": "main", "develop_branch": "develop"}
    s["product_backlog"] = [_story(["Draft", "Ready", "Implemented"])]
    s["sprint_backlog"] = [_story(["Draft", "Ready", "Implemented"])]
    s["active_feature_branches"] = {"US-0002": FEATURE}
    s["token_usage"] = {"total": LIVE_TOKENS, "agents": {}}
    s["human_approvals"] = [{"type": "release", "note": "reviewed"}]
    s["release_audit_run_count"] = 1
    tool_context.state = s
    return tool_context


def _checkpoint_live_state_on_feature_branch(repo, tool_context):
    """What run95 actually did: every checkpoint after the story's
    Implemented push committed live state onto the feature branch, which
    then got pushed there (and never merged)."""
    save_state_to_repo(tool_context)
    _git(repo["clone"], "push", "-q", "origin", FEATURE)
    assert _git(repo["clone"], "rev-parse", "--abbrev-ref", "HEAD") == FEATURE


def _origin_develop_state(repo):
    return json.loads(_git(repo["origin"], "show", "develop:.hc/state.json"))


def _origin_develop_file(repo, prefix):
    names = _git(repo["origin"], "ls-tree", "-r", "--name-only", "develop").splitlines()
    match = next((n for n in names if n.startswith(prefix)), None)
    return None if match is None else _git(repo["origin"], "show", f"develop:{match}")


def _stub_gh_only(real_run):
    """github._run with `git` passed through to the real implementation and
    `gh` stubbed: no existing release PR, so a new one gets created."""
    def fake(cmd, *args, **kwargs):
        if cmd and cmd[0] == "gh":
            return {"status": "ok", "returncode": 0, "stdout": "[]", "stderr": ""}
        return real_run(cmd, *args, **kwargs)
    return fake


def test_create_release_pr_publishes_live_state_to_origin_develop(repo):
    tool_context = _live_context()
    tool_context.state["sprint_report"] = "# Sprint Review Report\n\nSprint 2 report."
    _checkpoint_live_state_on_feature_branch(repo, tool_context)

    with patch.object(github_module, "_run", side_effect=_stub_gh_only(github_module._run)), \
         patch.object(github_module, "gh_pr_create",
                      return_value={"status": "ok", "stdout": "https://github.com/o/r/pull/7"}):
        result = create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)

    assert result["status"] == "ok", result
    assert result["released"] is True

    develop_state = _origin_develop_state(repo)
    item = next(x for x in develop_state["product_backlog"] if x["id"] == "US-0002")
    assert "Implemented" in item["stages_completed"]
    assert develop_state["sprint_report"].startswith("# Sprint Review Report")
    assert develop_state["token_usage"]["total"] == LIVE_TOKENS
    # create_release_pr's own release-success reset (SPEC-ISSUE-0070) must be
    # on develop too, not just in a local-only checkpoint - it's what lets
    # the next sprint's start_sprint through.
    assert develop_state["sprint_report_pending_release"] is False
    assert develop_state["sprint_backlog_pr_sprint"] == 0
    assert current_sprint_phase(develop_state) != "Development"

    story_md = _origin_develop_file(repo, "specs/stories/US-0002")
    assert story_md is not None and "Implemented" in story_md
    assert result["state_push"]["status"] == "ok", result["state_push"]


def test_create_release_pr_state_push_is_a_no_op_when_develop_already_matches(repo):
    """A second call with nothing new must not pile up empty commits on
    develop."""
    tool_context = _live_context()
    tool_context.state["sprint_report"] = "# Sprint Review Report\n\nreport."
    stub = _stub_gh_only(github_module._run)
    pr_ok = {"status": "ok", "stdout": "https://github.com/o/r/pull/7"}
    with patch.object(github_module, "_run", side_effect=stub), \
         patch.object(github_module, "gh_pr_create", return_value=pr_ok):
        create_release_pr(title="Sprint 1", body="body", tool_context=tool_context)
        before = _git(repo["origin"], "rev-parse", "develop")
        result = github_module._push_live_state_to_develop(tool_context)

    assert result["status"] == "ok"
    assert result["pushed"] is False
    assert _git(repo["origin"], "rev-parse", "develop") == before


def test_final_halt_safety_net_publishes_live_state_to_origin_develop(repo):
    from agents.scrum_team import agent as agent_module

    tool_context = _live_context()
    _checkpoint_live_state_on_feature_branch(repo, tool_context)

    # create_release_pr is patched out so this asserts on the safety net's
    # own commit alone (create_release_pr has its own test above).
    with patch.object(agent_module, "create_release_pr"):
        agent_module._ensure_sprint_report_on_final_halt(tool_context)

    develop_state = _origin_develop_state(repo)
    item = next(x for x in develop_state["product_backlog"] if x["id"] == "US-0002")
    assert "Implemented" in item["stages_completed"]
    assert develop_state["token_usage"]["total"] == LIVE_TOKENS
    assert develop_state["sprint_report"], "the fallback report must be in develop's state.json too"
    assert develop_state["sprint_report_pending_release"] is True
    assert current_sprint_phase(develop_state) == "Review, Retro & Release"
