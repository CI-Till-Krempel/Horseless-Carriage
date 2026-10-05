# agents/scrum_team/tools/workflow.py
from __future__ import annotations
from datetime import datetime
from typing import List, Dict, Any

from .base import _configured_repo_root, _develop_branch_name, _run
from .github import _checkout_develop_or_recover, git_push, gh_pr_create
from ..prompts import ROLE_NAMES


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
    when a finding calls for an actual <Role>-identity.md edit, instead of
    just leaving it as a retro note nobody acts on (see ISSUE about retro
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


def propose_steering_change(role: str, new_content: str, rationale: str, tool_context=None) -> Dict[str, Any]:
    """
    Propose a change to one role's own CUSTOMIZATION - `<role>-identity.md`
    in the PRODUCT/STATE repository - as a Pull Request, for human review.
    Never a direct write.

    This is the ONLY thing any tool can ever customize. Horseless-Carriage's
    own repo (this checkout - agents/scrum_team/prompts.py and everything
    under agents/scrum_team/prompt_modules/*-guardrails.md/*-workflow.md)
    is never a target and is never modified by any tool: those are
    non-negotiable, fixed parts of how every role operates - see
    prompts.py's own module docstring for the guardrails/workflow/identity
    split this supports. `<role>-identity.md` lives in the product/state
    repo instead, right alongside its specs/ and state.json (see
    _configured_repo_root, already used by write_file/create_story_spec_pr
    for that same repo), and is injected into that role's session
    dynamically by agent.py's role_identity_injection_callback - it can
    never override the guardrails/workflow content, structurally: those
    two never touch this repo's own product/state-repo content at all.

    Because of that structural separation, a role proposing a change to
    its OWN identity.md is safe - unlike an earlier version of this tool
    that refused a role editing its own prompt, there is no longer
    anything here for a role to weaken: identity.md was never where its
    guardrails/workflow rules live.

    - role: one of ROLE_NAMES (prompts.py) - ScrumOrchestrator, ProductOwner,
      ScrumMaster, DevTeam, QA, Architect, or QualityGuardian. Anything else
      is refused outright.
    - new_content: the full replacement content for that role's
      `<role>-identity.md` (like write_file - not a unified diff, simpler
      and safer to validate/apply).
    - rationale: why this change is needed - tie it to a real retrospective
      finding (add_retro_action/add_impediment/upsert_issue,
      gather_workflow_improvement_proposals), not a placeholder.

    Refuses an unknown role, a missing/too-short rationale, or a no-op
    (new_content already matches what's there). Always opens a draft PR
    against the product/state repo's develop branch and leaves it unmerged
    for human review, regardless of interaction level - unlike
    create_story_spec_pr (which self-merges immediately, no interaction
    level requiring a separate design-review gate), this changes how a role
    behaves, not just one story's content, so it always needs a human to
    actually look at it.
    """
    if role not in ROLE_NAMES:
        return {
            "status": "error",
            "message": f"Unknown role {role!r} - propose_steering_change can only target one of: {sorted(ROLE_NAMES)}.",
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

    file_name = f"{role}-identity.md"
    target_path = repo_root_path / file_name
    current_content = target_path.read_text(encoding="utf-8", errors="replace") if target_path.exists() else ""
    if new_content == current_content:
        return {"status": "ok", "proposed": False, "message": f"new_content is identical to the current {file_name} - nothing to propose."}

    agent_name = getattr(tool_context, "agent_name", None) if tool_context else None
    role_slug = role.lower()
    branch = f"steering/{role_slug}-identity-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    checkout_branch = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout_branch.get("status") == "error":
        return {"status": "error", "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}"}

    target_path.write_text(new_content, encoding="utf-8")

    push_res = git_push(branch=branch, commit_message=f"docs: propose {file_name} change", add_all=True, tool_context=tool_context)
    if push_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to push the proposal branch.", "push": push_res}
    actual_branch = push_res.get("branch", branch)

    pr_body = (
        f"Proposed by {agent_name or 'an agent'} via `propose_steering_change`.\n\n"
        f"### Rationale\n{rationale.strip()}\n\n"
        f"This is a proposed edit to {role}'s `{file_name}` - role-specific customization only, "
        "opened for human review. It has not been merged automatically, and cannot change that "
        f"role's non-negotiable guardrails or mechanically-enforced workflow, which live outside "
        "this repository entirely."
    )
    pr_res = gh_pr_create(
        title=f"Steering proposal: {file_name}",
        body=pr_body,
        base=develop,
        head=actual_branch,
        head_is_resolved=True,
        draft=True,
        tool_context=tool_context,
    )
    ok = pr_res.get("status") == "ok"
    if ok and tool_context and getattr(tool_context, "state", None):
        # GH issue #342: create_sprint_report's own gate demands a *fresh*
        # proposal (steering_proposal_count past steering_baseline) since
        # the last report whenever an open "steering"-category retro
        # finding exists - mirrors retro_baseline/kpi_baseline exactly, so
        # a steering gap can't just be logged once and never actually acted
        # on, the same failure this whole category-triage design fixes.
        tool_context.state["steering_proposal_count"] = tool_context.state.get("steering_proposal_count", 0) + 1
        # GH issue #356: a durable, structured record of this proposal -
        # rendered into specs/reports/STEERING-NNN.md alongside the sprint
        # report, since the PR body itself may be lost once the PR
        # merges/closes.
        tool_context.state["steering_proposals"] = list(tool_context.state.get("steering_proposals", [])) + [{
            "role": role,
            "proposed_by": agent_name or "an agent",
            "rationale": rationale.strip(),
            "new_content": new_content,
            "pr_url": (pr_res.get("stdout") or "").strip(),
            "branch": actual_branch,
        }]
    return {
        "status": "ok" if ok else "error",
        "proposed": True,
        "role": role,
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
    }
