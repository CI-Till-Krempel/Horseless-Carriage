"""
Regression coverage for GH #405: steering-proposal PRs (opened by
propose_steering_change, always targeting develop) were never touched by
the eval harness's auto-merge sweep - _merge_open_prs is only ever called
with base_branch=args.branch (the release PR's target), by design (see its
own call site comment), so develop-targeted PRs are deliberately left
alone. A real eval run (0.1.0-run52) left 5 such PRs open across all 5
sprints. _merge_open_steering_prs is a second, narrowly-scoped sweep for
exactly this PR shape - verify it only ever merges PRs whose head branch
is a steering-proposal branch, never a blanket develop sweep.
"""

import json
from pathlib import Path
from unittest.mock import patch

from agents.scrum_team.scripts.run_eval import _merge_open_steering_prs


def _fake_run(list_stdout=None, list_returncode=0, merge_returncode=0):
    def fake(cmd, cwd=None, capture_output=None, text=None):
        class _Result:
            pass

        r = _Result()
        if cmd[:3] == ["gh", "pr", "list"]:
            r.returncode = list_returncode
            r.stdout = json.dumps(list_stdout or [])
            r.stderr = "" if list_returncode == 0 else "gh: something went wrong"
        elif cmd[:3] == ["gh", "pr", "merge"]:
            r.returncode = merge_returncode
            r.stdout = "merged\n" if merge_returncode == 0 else ""
            r.stderr = "" if merge_returncode == 0 else "merge failed\n"
        else:  # pragma: no cover - not expected to be hit
            raise AssertionError(f"unexpected command: {cmd}")
        return r

    return fake


def test_merges_only_prs_whose_head_branch_is_a_steering_proposal():
    prs = [
        {"number": 101, "headRefName": "eval-0.1.0-run52/steering/scrummaster-identity-20261008142008"},
        {"number": 102, "headRefName": "eval-0.1.0-run52/feature/add-login"},
        {"number": 103, "headRefName": "eval-0.1.0-run52/steering/productowner-identity-20261008150000"},
    ]
    with patch("agents.scrum_team.scripts.run_eval.subprocess.run", side_effect=_fake_run(list_stdout=prs)):
        results = _merge_open_steering_prs(Path("."), "develop")

    assert [r["number"] for r in results] == [101, 103]
    assert all(r["merged"] for r in results)


def test_returns_empty_list_when_no_open_prs():
    with patch("agents.scrum_team.scripts.run_eval.subprocess.run", side_effect=_fake_run(list_stdout=[])):
        results = _merge_open_steering_prs(Path("."), "develop")
    assert results == []


def test_skips_prs_with_no_steering_segment_at_all():
    prs = [{"number": 201, "headRefName": "eval-0.1.0-run52/chore/cleanup"}]
    with patch("agents.scrum_team.scripts.run_eval.subprocess.run", side_effect=_fake_run(list_stdout=prs)):
        results = _merge_open_steering_prs(Path("."), "develop")
    assert results == []


def test_records_merge_failure_without_raising():
    prs = [{"number": 301, "headRefName": "eval-0.1.0-run52/steering/qa-identity-20261008160000"}]
    with patch(
        "agents.scrum_team.scripts.run_eval.subprocess.run",
        side_effect=_fake_run(list_stdout=prs, merge_returncode=1),
    ):
        results = _merge_open_steering_prs(Path("."), "develop")
    assert results == [{"number": 301, "merged": False, "message": "merge failed"}]


def test_surfaces_gh_pr_list_failure_as_an_error_entry():
    with patch(
        "agents.scrum_team.scripts.run_eval.subprocess.run",
        side_effect=_fake_run(list_returncode=1),
    ):
        results = _merge_open_steering_prs(Path("."), "develop")
    assert results == [{"error": "gh pr list failed: gh: something went wrong"}]
