"""
Last-resort state rebuild: re-derives Scrum state from whatever specs/
already has on disk (epics, stories, issues, the PRD's vision/goals, the
architecture vision doc) - the same "doc sync" step init_scrum_state()
already runs automatically on a session's first-ever init (see
agents/scrum_team/tools/scrum.py), exposed here as an explicit, host-run
last resort instead.

SPEC-ISSUE-0052: init_scrum_state is deliberately NOT an agent-callable tool -
nothing in a live session ever legitimately needs to re-run it (the
mechanical ensure_state_initialized_callback already guarantees it ran
once, correctly, before any agent's first turn), and exposing it let an
agent re-trigger a reload mid-session, silently overwriting richer live
progress with a stale on-disk snapshot. The one genuine use this forecloses
- deliberately rebuilding state.json from specs/ after an out-of-band edit,
or because check_state_repo.py's own git-history-reset/delete-and-restart
repair options (see _offer_state_repair) aren't enough - belongs to a human
operator running this script directly, never to a role mid-sprint.

Must run inside the agent container (or any Python environment with
pydantic/the agents package installed) - same constraint as
validate_state.py, and invoked the same way (see check_state_repo.py).
"""
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

USAGE_ERROR_EXIT_CODE = 1

try:
    from agents.scrum_team.tools.scrum import init_scrum_state, save_state_to_repo
except ImportError as e:
    print(f"Error importing required modules: {e}")
    sys.exit(USAGE_ERROR_EXIT_CODE)


class _HostToolContext:
    """The minimal shape init_scrum_state/save_state_to_repo actually read:
    a mutable `.state` dict, nothing else - no live ADK session exists when
    this runs host-side."""

    def __init__(self):
        self.state = {}
        self.agent_name = None


def rebuild(state_repo_path: str) -> dict:
    """Points _configured_repo_root (agents/scrum_team/tools/base.py) at
    state_repo_path, then runs init_scrum_state(force=True) against a
    blank in-memory state so its own "load whatever's on disk first, then
    sync stories/vision/goals/architecture from specs/ on top" logic does
    the real work, and persists the result via save_state_to_repo - nothing
    here duplicates that logic."""
    os.environ["INTERNAL_STATE_REPO_PATH"] = state_repo_path
    ctx = _HostToolContext()
    init_result = init_scrum_state(tool_context=ctx, force=True)
    if init_result.get("status") != "ok":
        return {"status": "error", "message": f"init_scrum_state failed: {init_result}"}
    save_result = save_state_to_repo(tool_context=ctx)
    if save_result.get("status") != "ok":
        return {"status": "error", "message": f"Failed to persist rebuilt state: {save_result}"}
    return {
        "status": "ok",
        "path": save_result.get("path"),
        "doc_sync": init_result.get("doc_sync", {}),
        "state_json_corrupted": init_result.get("state_json_corrupted", False),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 rebuild_state_from_specs.py <state_repo_path>")
        sys.exit(USAGE_ERROR_EXIT_CODE)

    result = rebuild(sys.argv[1])
    if result["status"] != "ok":
        print(f"ERROR: {result['message']}")
        sys.exit(USAGE_ERROR_EXIT_CODE)

    doc_sync = result["doc_sync"]
    print(f"Rebuilt state from specs/ and wrote it to {result['path']}:")
    print(f"  stories synced: {doc_sync.get('stories_synced', 0)}")
    print(f"  vision synced: {doc_sync.get('vision_synced', False)}")
    print(f"  goals synced: {doc_sync.get('goals_synced', 0)}")
    print(f"  architecture synced: {doc_sync.get('architecture_synced', False)}")
    if result["state_json_corrupted"]:
        print("  NOTE: the previous state.json could not be loaded (even via git history) - "
              "this rebuild started from blank/default state plus whatever specs/ had.")
    sys.exit(0)
