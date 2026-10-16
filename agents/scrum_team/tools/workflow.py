# agents/scrum_team/tools/workflow.py
from __future__ import annotations
from datetime import datetime
from typing import List, Dict, Any

from .base import _configured_repo_root, _develop_branch_name, _run
from .github import _checkout_develop_or_recover, git_push, gh_pr_create
from .notifications import record_blocking_interaction
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
        - Calculate KPIs
        - Author sprint report
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
      ScrumMaster, DevTeam, QA, or Architect. Anything else is refused
      outright.
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


_WORKBENCH_SCRIPT_PATH = ".hc/workbench-setup.sh"
_NEW_ISSUE_URL_FOR_ALLOWLIST = (
    "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new"
    "?title=Allowlist+request&labels=network-allowlist"
)


def propose_framework_change(
    workbench_setup_script: str,
    additional_allowlist_hosts: List[str],
    rationale: str,
    tool_context=None,
) -> Dict[str, Any]:
    """
    Propose a change to this project's "workbench" - the toolchain (JDK,
    Node, Go, ...) the agent image needs to scaffold/build/test THIS
    product's actual tech stack - as a Pull Request, for human review.
    Never a direct write (see docs.py's write_file, which refuses any path
    under `.hc/` and points here instead).

    ISSUE-0064: a per-language build-arg flag doesn't generalize - every new
    stack would need its own flag and a Horseless-Carriage code change
    forever. Instead, `.hc/workbench-setup.sh` in the PRODUCT/STATE repo
    (same repo `_configured_repo_root`/`write_file`/`propose_steering_change`
    already operate on) is a plain shell script that agent.Dockerfile COPYs
    in and RUNs at image-build time via a named Docker build context -
    see agent.Dockerfile's own comment for exactly how. Horseless-Carriage's
    own repo (this checkout, including its network/compose config) is never
    a target and is never modified by any tool, same structural separation
    `propose_steering_change` already relies on for identity.md.

    - workbench_setup_script: the FULL replacement content for
      `.hc/workbench-setup.sh` (like `propose_steering_change`'s
      new_content - not a diff).
    - additional_allowlist_hosts: hostnames this workbench's toolchain needs
      to reach at RUNTIME (e.g. "repo.maven.apache.org" for Gradle,
      "registry.npmjs.org" for npm) that the agent's network sandbox
      (agent-net + net-proxy's allowlist) doesn't already permit. Pass []
      if the workbench needs no new runtime network access (build-time
      `apt-get`/`curl` inside the script itself already has the Docker
      build's own unrestricted network, same as every other line in any
      Dockerfile - this list is specifically about what the agent container
      can reach once it's running, a completely separate, much more
      restricted network path).
    - rationale: why this change is needed, concretely (same ≥15-character
      rule as `propose_steering_change`) - not a placeholder.

    Mechanically CANNOT apply an allowlist change itself: `config/net-proxy/
    allowlist.filter` lives in Horseless-Carriage's own repo, which this
    tool has no filesystem access to and no git credentials for (only
    GITHUB_REPO_URL/GITHUB_TOKEN for the PRODUCT repo) - this is a genuine,
    structural boundary, not a policy choice that could be loosened later.
    When additional_allowlist_hosts is non-empty, the opened PR's body says
    so explicitly and a blocking interaction is also raised (see
    record_blocking_interaction) so a human operator sees it as an
    immediate "ACTION NEEDED" item, not just another PR waiting in a queue -
    the Tested gate stays blocked until that operator-side step actually
    happens, the same class of out-of-reach fix the original ModuleNotFound/
    JVM incident that led to this tool needed a human for in the first
    place.

    Refuses a missing/too-short rationale, or a no-op (workbench_setup_script
    already matches what's there). Always opens a draft PR against the
    product/state repo's develop branch and leaves it unmerged for human
    review, regardless of interaction level - same as propose_steering_change.
    """
    if not rationale or len(rationale.strip()) < 15:
        return {
            "status": "error",
            "message": (
                "rationale is required and must concretely explain why this workbench change is "
                "needed - not a placeholder."
            ),
        }

    if not tool_context or not getattr(tool_context, "state", None):
        return {"status": "error", "message": "propose_framework_change requires an active session (tool_context.state)."}

    additional_allowlist_hosts = list(additional_allowlist_hosts or [])

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

    target_path = repo_root_path / _WORKBENCH_SCRIPT_PATH
    current_content = target_path.read_text(encoding="utf-8", errors="replace") if target_path.exists() else ""
    if workbench_setup_script == current_content:
        return {"status": "ok", "proposed": False, "message": f"workbench_setup_script is identical to the current {_WORKBENCH_SCRIPT_PATH} - nothing to propose."}

    agent_name = getattr(tool_context, "agent_name", None) if tool_context else None
    branch = f"framework/workbench-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    checkout_branch = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout_branch.get("status") == "error":
        return {"status": "error", "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}"}

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(workbench_setup_script, encoding="utf-8")

    push_res = git_push(branch=branch, commit_message=f"build: propose {_WORKBENCH_SCRIPT_PATH} change", add_all=True, tool_context=tool_context)
    if push_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to push the proposal branch.", "push": push_res}
    actual_branch = push_res.get("branch", branch)

    pr_body = (
        f"Proposed by {agent_name or 'an agent'} via `propose_framework_change`.\n\n"
        f"### Rationale\n{rationale.strip()}\n\n"
        f"### Workbench script (`{_WORKBENCH_SCRIPT_PATH}`)\n```sh\n{workbench_setup_script}\n```\n\n"
        "This is a proposed change to this project's agent-image toolchain, opened for human "
        "review. It has not been merged automatically, and it cannot change Horseless Carriage's "
        "own network sandbox (agent-net/net-proxy) - that lives outside this repository entirely."
    )
    if additional_allowlist_hosts:
        hosts_list = "\n".join(f"- `{host}`" for host in additional_allowlist_hosts)
        pr_body += (
            "\n\n### ⚠️ HUMAN OPERATOR ACTION REQUIRED (outside this repo)\n"
            "This workbench needs outbound network access this project's agent container doesn't "
            "have yet. Merging this PR alone is **not enough** - a human must also, in "
            "Horseless Carriage's own repository:\n"
            f"{hosts_list}\n\n"
            "1. Add the host(s) above to `config/net-proxy/allowlist.filter`.\n"
            "2. Rebuild/restart `net-proxy` for the change to take effect.\n\n"
            f"No agent tool can do this directly - open an issue there if unsure how: {_NEW_ISSUE_URL_FOR_ALLOWLIST}"
        )

    pr_res = gh_pr_create(
        title="Framework change proposal: workbench",
        body=pr_body,
        base=develop,
        head=actual_branch,
        head_is_resolved=True,
        draft=True,
        tool_context=tool_context,
    )
    ok = pr_res.get("status") == "ok"
    if ok and tool_context and getattr(tool_context, "state", None):
        tool_context.state["framework_change_proposal_count"] = tool_context.state.get("framework_change_proposal_count", 0) + 1
        tool_context.state["framework_change_proposals"] = list(tool_context.state.get("framework_change_proposals", [])) + [{
            "proposed_by": agent_name or "an agent",
            "rationale": rationale.strip(),
            "workbench_setup_script": workbench_setup_script,
            "additional_allowlist_hosts": additional_allowlist_hosts,
            "pr_url": (pr_res.get("stdout") or "").strip(),
            "branch": actual_branch,
        }]
        if additional_allowlist_hosts:
            record_blocking_interaction(
                "framework_change",
                f"Workbench change needs new network access: {', '.join(additional_allowlist_hosts)}",
                detail=(
                    f"Draft PR opened by {agent_name or 'an agent'}: {(pr_res.get('stdout') or '').strip()}. "
                    "A human operator must add the listed host(s) to Horseless Carriage's own "
                    "config/net-proxy/allowlist.filter and restart net-proxy, outside this repo, "
                    "before this workbench's tests can actually reach them."
                ),
                tool_context=tool_context,
            )
    return {
        "status": "ok" if ok else "error",
        "proposed": True,
        "branch": actual_branch,
        "additional_allowlist_hosts": additional_allowlist_hosts,
        "push": push_res,
        "pr": pr_res,
    }
