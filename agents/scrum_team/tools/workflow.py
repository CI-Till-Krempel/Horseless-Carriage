# agents/scrum_team/tools/workflow.py
from __future__ import annotations
from datetime import datetime
from typing import List, Dict, Any

from .base import _configured_repo_root, _develop_branch_name, _run
from .github import _checkout_develop_or_recover, git_push, gh_pr_create


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
    when a finding calls for an actual AGENTS.md edit, instead of just
    leaving it as a retro note nobody acts on (see ISSUE about retro
    findings going unfixed because agents had no path to propose a
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


def propose_steering_change(new_content: str, rationale: str, tool_context=None) -> Dict[str, Any]:
    """
    Propose a change to the PRODUCT/STATE repo's own AGENTS.md - the file
    where THIS project's customization of workflow/steering lives - as a
    Pull Request, for human review. Never a direct write.

    Horseless-Carriage's own repo (this checkout - agents/scrum_team/
    prompts.py, docs/DEVELOPMENT-WORKFLOW.md, spec-templates/DOD.md/DOR.md)
    is never a target and is never modified by any tool: those are
    non-negotiable, fixed parts of how the agents operate, not something a
    running instance can customize or water down, even via PR with human
    review. What a project CAN customize - conventions, extra process
    steps, team-specific conventions - belongs in the product/state repo's
    own AGENTS.md instead, right alongside its specs/ and state.json (see
    _configured_repo_root, already used by write_file/create_story_spec_pr
    for that same repo).

    - new_content: the full replacement content for AGENTS.md (like
      write_file - not a unified diff, simpler and safer to validate/apply).
    - rationale: why this change is needed - tie it to a real retrospective
      finding (add_retro_action/add_impediment/upsert_issue,
      gather_workflow_improvement_proposals), not a placeholder.

    Refuses a missing/too-short rationale, or a no-op (new_content already
    matches what's there). Always opens a draft PR against the product/
    state repo's develop branch and leaves it unmerged - same
    human-review-required pattern as create_story_spec_pr's Stakeholder
    path, regardless of interaction level, since this changes how the team
    itself works, not just one story's content.
    """
    if not rationale or len(rationale.strip()) < 15:
        return {
            "status": "error",
            "message": (
                "rationale is required and must concretely explain why this change is needed - tie it "
                "to a real retrospective finding (add_retro_action/add_impediment/upsert_issue), not a "
                "placeholder."
            ),
        }

    if not tool_context or not getattr(tool_context, "state", None):
        return {"status": "error", "message": "propose_steering_change requires an active session (tool_context.state)."}

    repo_root_path = _configured_repo_root(tool_context)
    repo_root = str(repo_root_path)
    develop = _develop_branch_name(tool_context)

    recovery = _checkout_develop_or_recover(repo_root, develop, tool_context=tool_context)
    checkout_develop = recovery["checkout"]
    if checkout_develop.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out '{develop}': {checkout_develop.get('stderr') or checkout_develop.get('message')}",
            "fetch": recovery["fetch"],
            "auto_integrated": recovery["auto_integrated"],
        }

    target_path = repo_root_path / "AGENTS.md"
    current_content = target_path.read_text(encoding="utf-8", errors="replace") if target_path.exists() else ""
    if new_content == current_content:
        return {"status": "ok", "proposed": False, "message": "new_content is identical to the current AGENTS.md - nothing to propose."}

    agent_name = getattr(tool_context, "agent_name", None) if tool_context else None
    role_slug = (agent_name or "agent").lower()
    branch = f"steering/{role_slug}-agents-md-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    checkout_branch = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout_branch.get("status") == "error":
        return {"status": "error", "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}"}

    target_path.write_text(new_content, encoding="utf-8")

    push_res = git_push(branch=branch, commit_message="docs: propose AGENTS.md change", add_all=True, tool_context=tool_context)
    if push_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to push the proposal branch.", "push": push_res}
    actual_branch = push_res.get("branch", branch)

    pr_body = (
        f"Proposed by {agent_name or 'an agent'} via `propose_steering_change`.\n\n"
        f"### Rationale\n{rationale.strip()}\n\n"
        "This is a proposed edit to this project's AGENTS.md, opened for human review - it has not "
        "been merged automatically."
    )
    pr_res = gh_pr_create(
        title="Steering proposal: AGENTS.md",
        body=pr_body,
        base=develop,
        head=actual_branch,
        head_is_resolved=True,
        draft=True,
        tool_context=tool_context,
    )
    return {
        "status": "ok" if pr_res.get("status") == "ok" else "error",
        "proposed": True,
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
    }
