# agents/scrum_team/tools/workflow.py
from __future__ import annotations
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from .base import _project_root, _run, _eval_run_id


def generate_workflow_diagram(tool_context=None) -> Dict[str, Any]:
    """
    Generates a PlantUML diagram of the current workflow.
    """
    from .docs import write_file
    plantuml_code = """
@startuml
title Current Workflow

start

:User Request;

fork
    :ProductOwner;
    note right
        - Refine requirements
        - Create/update stories
        - Prioritize backlog
    end note
fork again
    :ScrumMaster;
    note left
        - Facilitate events
        - Remove impediments
        - Monitor budget
    end note
fork again
    :DevTeam;
    note left
        - Implement stories
        - Create pull requests
        - Run tests
    end note
fork again
    :QA;
    note right
        - Review pull requests
        - Propose test cases
    end note
fork again
    :Architect;
    note right
        - Review pull requests
        - Propose ADRs
    end note
fork again
    :QualityGuardian;
    note right
        - Calculate KPIs
        - Update sprint report
    end note
end fork

:Sprint Review;
note right
    - Demonstrate increment
    - Gather feedback
end note

:Sprint Retrospective;
note left
    - Discuss what went well
    - Discuss what could be improved
end note

stop

@enduml
"""
    return write_file("specs/workflow.puml", plantuml_code, overwrite=True, tool_context=tool_context)


def gather_workflow_improvement_proposals(tool_context=None) -> List[str]:
    """
    Gathers concrete workflow-improvement proposals from this engagement's
    own retrospective history (state.retro_actions / state.impediment_log -
    see add_retro_action/add_impediment in tools/scrum.py), rather than the
    3 hardcoded dummy strings this used to return unconditionally - those
    never reflected anything that actually happened in a real sprint, so
    nothing ever consumed them for real.

    Feed any of these into propose_steering_change(...) as the rationale
    when a finding calls for an actual prompts.py/workflow-doc edit, instead
    of just leaving it as a retro note nobody acts on (see ISSUE about
    retro findings going unfixed because agents had no path to propose a
    steering-doc change themselves).
    """
    state = getattr(tool_context, "state", None) or {}
    proposals: List[str] = []
    for entry in state.get("retro_actions", []) or []:
        if entry.get("status", "open") != "open":
            continue
        proposals.append(
            f"{(entry.get('action') or '').strip()} "
            f"(owner: {entry.get('owner', 'unassigned')}, success metric: {entry.get('success_metric', 'n/a')})"
        )
    for entry in state.get("impediment_log", []) or []:
        if entry.get("status", "open") != "open":
            continue
        proposals.append(f"Unblock: {(entry.get('description') or '').strip()} (owner: {entry.get('owner', 'unassigned')})")
    if not proposals:
        return ["No open retro actions or impediments recorded yet this engagement - nothing concrete to propose."]
    return proposals


# Fixed allowlist for propose_steering_change - this project's "workflow
# steering" surface: the agents' own system prompts, the human-facing
# workflow doc they're derived from, and the DoD/DoR templates that gate
# story-stage transitions. Deliberately narrow and hardcoded (not, say,
# "anything under docs/") - this tool's whole point is a human reviewing a
# small, predictable set of high-leverage files, not a general-purpose PR
# tool with a repo-wide blast radius.
_STEERING_ALLOWED_PATHS = {
    "agents/scrum_team/prompts.py",
    "docs/DEVELOPMENT-WORKFLOW.md",
    "spec-templates/DOD.md",
    "spec-templates/DOR.md",
}

# Maps each specialist role (tool_context.agent_name - see agent.py's
# SPECIALIST_AGENT_NAMES) to its own prompt constant in prompts.py, so
# propose_steering_change can refuse a role editing its own system prompt
# even via PR with human review (see _diff_touches_own_prompt below).
_ROLE_PROMPT_CONSTANTS = {
    "ProductOwner": "PO_PROMPT",
    "ScrumMaster": "SM_PROMPT",
    "DevTeam": "DEV_PROMPT",
    "QA": "QA_PROMPT",
    "Architect": "ARCH_PROMPT",
    "QualityGuardian": "QUALITY_GUARDIAN_PROMPT",
}


def _extract_prompt_constant(text: str, const_name: str) -> str | None:
    """
    Pulls a `NAME = \"\"\"...\"\"\"` triple-quoted constant's body out of
    prompts.py source text. Returns None if the constant isn't found in
    this form (e.g. a proposal that deletes it outright, or malformed
    content) - callers treat that as "can't prove this leaves the role's
    own prompt untouched", not as "safe".
    """
    match = re.search(rf'^{re.escape(const_name)}\s*=\s*"""(.*?)"""', text, re.DOTALL | re.MULTILINE)
    return match.group(1) if match else None


def _diff_touches_own_prompt(current_content: str, new_content: str, agent_name: str | None) -> bool:
    """
    True if `new_content` changes (or removes) the calling role's own
    prompt constant relative to `current_content` - the content-level guard
    propose_steering_change applies on top of the path allowlist, per the
    issue's "agents should not be able to modify their system prompts"
    requirement. Only meaningful for agents/scrum_team/prompts.py; callers
    should only invoke this for that path.
    """
    own_constant = _ROLE_PROMPT_CONSTANTS.get(agent_name or "")
    if not own_constant:
        return False
    old_val = _extract_prompt_constant(current_content, own_constant)
    new_val = _extract_prompt_constant(new_content, own_constant)
    return old_val != new_val


def _resolve_project_default_branch(repo_root: Path, tool_context=None) -> str:
    """
    This project's (Horseless-Carriage's own) default branch on origin -
    resolved from `git remote show origin` rather than hardcoded "main", so
    this still works if that's ever renamed. Falls back to "main" if the
    remote lookup fails for any reason (offline, no remote configured,
    etc.) rather than raising - the caller's subsequent fetch/push will
    surface a clearer error if that fallback is also wrong.
    """
    result = _run(["git", "remote", "show", "origin"], cwd=str(repo_root), tool_context=tool_context)
    if result.get("status") == "ok":
        for line in (result.get("stdout") or "").splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                return line.split(":", 1)[1].strip() or "main"
    return "main"


def propose_steering_change(file_path: str, new_content: str, rationale: str, tool_context=None) -> Dict[str, Any]:
    """
    Propose a change to one of Horseless-Carriage's own workflow "steering"
    documents (see _STEERING_ALLOWED_PATHS) as a Pull Request against THIS
    project's own repo, for human review - never a direct write.

    Distinct from write_file (tools/docs.py), which only ever targets the
    configured target/state repo (_configured_repo_root) - the project's
    own prompts.py/workflow docs live outside that entirely, so nothing
    could reach them before this. This is a deliberate, narrow, human-
    reviewed path to close that gap, not an oversight being reopened.

    - file_path: repo-relative path; must be one of _STEERING_ALLOWED_PATHS.
      Anything else is refused outright.
    - new_content: the full replacement content for that file (like
      write_file - not a unified diff, simpler and safer to validate/apply).
    - rationale: why this change is needed - tie it to a real retrospective
      finding (add_retro_action/add_impediment/upsert_issue,
      gather_workflow_improvement_proposals), not a placeholder.

    Refuses:
    - Any file_path outside the allowlist.
    - A missing/too-short rationale.
    - A diff to agents/scrum_team/prompts.py that changes (or removes) the
      *calling* role's own prompt constant (see _diff_touches_own_prompt) -
      even with a human merge gate downstream, a role cannot propose
      watering down its own guardrails. Editing a *different* role's
      prompt, or one of the other steering docs, is fine.
    - Running during an eval run (EVAL_RUN_ID set) - this targets this
      project's own real GitHub repo/PRs, never the isolated eval scenario
      repo an eval run's other git-backed tools operate against; there is
      no safe interpretation of an eval run opening PRs here.

    Never writes directly to this checkout - the actual file edit happens
    in a throwaway git worktree (removed again before returning), so a
    proposal in flight can't transiently change what read_doc/create_from_
    template or anything else reading live from this checkout sees.
    """
    clean_path = file_path.lstrip("/")
    if clean_path not in _STEERING_ALLOWED_PATHS:
        return {
            "status": "error",
            "message": (
                f"Refusing to propose a change to '{file_path}' - propose_steering_change can only "
                f"target one of: {sorted(_STEERING_ALLOWED_PATHS)}."
            ),
        }

    if not rationale or len(rationale.strip()) < 15:
        return {
            "status": "error",
            "message": (
                "rationale is required and must concretely explain why this change is needed - tie it "
                "to a real retrospective finding (add_retro_action/add_impediment/upsert_issue), not a "
                "placeholder."
            ),
        }

    if _eval_run_id():
        return {
            "status": "error",
            "message": (
                "propose_steering_change is disabled during eval runs - it targets this project's own "
                "real repo/PRs, not the isolated eval scenario repo other git-backed tools use here."
            ),
        }

    repo_root = _project_root()
    abs_path = (repo_root / clean_path).resolve()
    if not str(abs_path).startswith(str(repo_root.resolve())) or not abs_path.exists():
        return {
            "status": "error",
            "message": f"'{file_path}' does not exist in this repository - propose_steering_change only edits existing steering docs.",
        }

    current_content = abs_path.read_text(encoding="utf-8", errors="replace")

    agent_name = getattr(tool_context, "agent_name", None) if tool_context else None
    if clean_path == "agents/scrum_team/prompts.py" and _diff_touches_own_prompt(current_content, new_content, agent_name):
        own_constant = _ROLE_PROMPT_CONSTANTS.get(agent_name or "")
        return {
            "status": "error",
            "message": (
                f"Refusing: this change touches {own_constant}, which is {agent_name}'s own system "
                "prompt. Agents cannot propose changes to their own prompt, even via PR with human "
                "review - a different role or a human must raise this instead."
            ),
        }

    if new_content == current_content:
        return {"status": "ok", "proposed": False, "message": "new_content is identical to the current file - nothing to propose."}

    if not tool_context or not getattr(tool_context, "state", None):
        return {"status": "error", "message": "propose_steering_change requires an active session (tool_context.state)."}

    default_branch = _resolve_project_default_branch(repo_root, tool_context)
    file_slug = re.sub(r"[^a-z0-9]+", "-", Path(clean_path).stem.lower()).strip("-") or "steering"
    role_slug = (agent_name or "agent").lower()
    branch = f"steering/{role_slug}-{file_slug}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    worktree_dir = tempfile.mkdtemp(prefix="steering-proposal-")
    try:
        fetch = _run(["git", "fetch", "origin", default_branch], cwd=str(repo_root), tool_context=tool_context)
        add_worktree = _run(
            ["git", "worktree", "add", "-B", branch, worktree_dir, f"origin/{default_branch}"],
            cwd=str(repo_root), tool_context=tool_context,
        )
        if add_worktree.get("status") == "error":
            return {
                "status": "error",
                "message": "Could not create an isolated worktree for this proposal.",
                "fetch": fetch,
                "worktree": add_worktree,
            }

        target_file = Path(worktree_dir) / clean_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(new_content, encoding="utf-8")

        add_res = _run(["git", "add", clean_path], cwd=worktree_dir, tool_context=tool_context)
        commit_res = _run(
            ["git", "commit", "-m", f"docs: propose steering change to {clean_path}"],
            cwd=worktree_dir, tool_context=tool_context,
        )
        if commit_res.get("status") == "error":
            return {"status": "error", "message": "Failed to commit the proposed change.", "add": add_res, "commit": commit_res}

        push_res = _run(["git", "push", "-u", "origin", branch], cwd=worktree_dir, tool_context=tool_context)
        if push_res.get("status") == "error":
            return {"status": "error", "message": "Failed to push the proposal branch.", "push": push_res}

        pr_body = (
            f"Proposed by {agent_name or 'an agent'} via `propose_steering_change`.\n\n"
            f"### Rationale\n{rationale.strip()}\n\n"
            "This is a proposed edit to a workflow steering document, opened for human review - it has "
            "not been merged automatically."
        )
        pr_res = _run(
            [
                "gh", "pr", "create",
                "--base", default_branch,
                "--head", branch,
                "--title", f"Steering proposal: {clean_path}",
                "--body", pr_body,
                "--draft",
            ],
            cwd=worktree_dir, tool_context=tool_context,
        )
        return {
            "status": "ok" if pr_res.get("status") == "ok" else "error",
            "proposed": True,
            "branch": branch,
            "push": push_res,
            "pr": pr_res,
        }
    finally:
        _run(["git", "worktree", "remove", "--force", worktree_dir], cwd=str(repo_root), tool_context=tool_context)
        shutil.rmtree(worktree_dir, ignore_errors=True)
