#!/usr/bin/env python3
"""
Optional, opt-in "watch mode" for Horseless Carriage (GH issue #48).

Nothing else in this repo ever imports or runs this script - start it
yourself (foreground, nohup, tmux, a systemd timer, or a cron job wrapping
`--once`) only if you want this behavior; run.py's own default behavior is
completely unchanged either way. This is the "configurable" the issue's
own follow-up comment asked for ("this is a big change, and should be
configurable in the best case").

Polls the configured state repository for two things a human would
otherwise have to remember to check for themselves:
  1. New commits landed on the develop branch (GITHUB_DEVELOP_BRANCH).
  2. A story sitting in the backlog whose completed pipeline stages
     (STORY_STAGES: Draft -> Ready -> Implemented -> Reviewed -> Tested ->
     Accepted) stop one short of the next one - i.e. genuinely "ready for
     <next stage's owner>" work (the issue's own example: "ready for
     developers"), not just specifically the "Ready" stage.

When either fires, it prints a hard-to-miss banner - the same zero-config,
always-works notification style as the ConsoleNotifier added for GH issue
#53 (agents/scrum_team/tools/notifications.py), deliberately not imported
from here: this is a plain host-side script (stdlib only, like doctor.py/
lib_docker.py), and the agents/ package pulls in the full ADK/pydantic
dependency stack that isn't otherwise a host-tooling requirement - rather
than starting or driving a session itself. See docs/RUNNING.md's "Watch
Mode" section for why: this repo's only proven mechanism for a fully
headless, non-interactive agent turn is the eval harness's ADK
InMemoryRunner pattern (agents/scrum_team/scripts/run_eval.py), built
around a disposable session created fresh for that one harness run.
Reusing it against this repo's real, persistent production session (the
sqlite-backed session a live web/CLI user may simultaneously have open)
risks two writers racing on the same session.state - a real correctness
problem, not a paperwork one. save_state_to_repo()'s git-commit checkpoint
(ISSUE-0024) is a safety net for *recovering* from that, not a fix for it
happening in the first place - it still writes each caller's full
in-memory snapshot unconditionally, so a second writer's save can silently
discard a first writer's unique changes. Closing that gap (optimistic
concurrency on state writes, a session lock, then a real headless trigger
here) is scoped as its own epic - see specs/stories/EP-0008-Concurrency-
Safe-State-And-A-Working-Parallel-Loop.md - rather than a shortcut in this
change. This script safely covers the "notice new work" half; someone
still starts the agent itself, same as today.

Usage:
  python3 watch_roadmap.py          Poll forever (Ctrl+C to stop).
  python3 watch_roadmap.py --once   Check once and exit - 0 if a trigger
                                     fired, 1 if not - for wrapping in your
                                     own cron/systemd timer instead of this
                                     script's own sleep loop.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

import lib_env

STORY_STAGES = ["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]

DEFAULT_POLL_INTERVAL_SECONDS = 300


def count_stories_ready_for_next_stage(state_json_path: Path) -> int:
    """How many backlog items (sprint_backlog + product_backlog, deduped
    isn't needed since we only care about the count crossing zero) have
    completed some stage but not the very next one in STORY_STAGES - e.g.
    Ready-but-not-Implemented ("ready for developers", the issue's own
    example), or Reviewed-but-not-Tested ("ready for QA"). 0 if
    state_json_path doesn't exist yet or isn't valid JSON - a missing/
    corrupted file is "nothing to report", not an error this script
    should crash over."""
    if not state_json_path.is_file():
        return 0
    try:
        data = json.loads(state_json_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    count = 0
    for item in (data.get("sprint_backlog") or []) + (data.get("product_backlog") or []):
        if not isinstance(item, dict):
            continue
        completed = set(item.get("stages_completed") or [])
        for i, stage in enumerate(STORY_STAGES[:-1]):
            if stage in completed and STORY_STAGES[i + 1] not in completed:
                count += 1
                break
    return count


def develop_branch_head(repo_root: Path, branch: str) -> str:
    """origin/<branch>'s current commit sha after a best-effort `git
    fetch` - "" if that fails for any reason (no remote, offline, not a
    git repo at all). A watch check degrades to "no new commits detected"
    rather than crashing the poll loop over a transient network issue."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=repo_root, capture_output=True, timeout=30,
        )
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _notify(summary: str, detail: str) -> None:
    """Zero-config, always-works notification: a hard-to-miss banner to
    stderr - the same style as ConsoleNotifier (GH issue #53's
    agents/scrum_team/tools/notifications.py), duplicated in miniature
    here rather than imported, since this host-side script deliberately
    stays stdlib-only (no ADK/pydantic dependency) like doctor.py/
    lib_docker.py."""
    banner = "!" * 70
    print(banner, file=sys.stderr)
    print(f"[NEW WORK] {summary}", file=sys.stderr)
    print(detail, file=sys.stderr)
    print(banner, file=sys.stderr)


def check_once(env_path: Path, last_seen_head: str) -> Tuple[bool, str, int]:
    """One poll cycle. Returns (triggered, new_last_seen_head, ready_count).
    last_seen_head=="" (the very first call) never counts as "new commits" -
    there's nothing yet to compare the current HEAD against."""
    state_repo_str = lib_env.read_env_var(env_path, "STATE_REPO_PATH")
    develop_branch = lib_env.read_env_var(env_path, "GITHUB_DEVELOP_BRANCH") or "develop"

    if not state_repo_str:
        return False, last_seen_head, 0

    state_repo = Path(state_repo_str).expanduser()
    ready_count = count_stories_ready_for_next_stage(state_repo / ".hc" / "state.json")

    new_head = last_seen_head
    new_commits = False
    head = develop_branch_head(state_repo, develop_branch)
    if head:
        new_commits = bool(last_seen_head) and head != last_seen_head
        new_head = head

    if not (new_commits or ready_count > 0):
        return False, new_head, ready_count

    if new_commits:
        summary = f"New commit(s) landed on '{develop_branch}'"
    else:
        summary = f"{ready_count} stor{'y' if ready_count == 1 else 'ies'} ready for the next pipeline stage"
    detail = (
        f"watch_roadmap.py detected new work in the state repository - new_commits={new_commits}, "
        f"ready_count={ready_count}. Start the agent (python3 run.py) to pick it up."
    )
    _notify(summary, detail)

    return True, new_head, ready_count


def main(argv=None, repo_root: Path = None) -> None:
    """repo_root defaults to this file's own directory (real usage); a
    caller (tests) can pass a tmp_path instead so this never reads the
    real .env sitting in an actual checkout."""
    argv = sys.argv[1:] if argv is None else argv
    once = "--once" in argv

    repo_root = repo_root or Path(__file__).resolve().parent
    env_path = repo_root / ".env"
    interval_str = lib_env.read_env_var(env_path, "WATCH_POLL_INTERVAL_SECONDS")
    try:
        interval = int(interval_str) if interval_str else DEFAULT_POLL_INTERVAL_SECONDS
    except ValueError:
        interval = DEFAULT_POLL_INTERVAL_SECONDS

    print(f"--- Horseless Carriage: watch_roadmap.py (poll every {interval}s, Ctrl+C to stop) ---")
    last_seen_head = ""
    while True:
        triggered, last_seen_head, _ = check_once(env_path, last_seen_head)
        if once:
            sys.exit(0 if triggered else 1)
        time.sleep(interval)


if __name__ == "__main__":
    main()
