# agents/scrum_team/prompts.py
"""
Each role's system prompt is assembled from up to 4 pieces, never a single
hand-edited blob - see docs/AGENT-PROMPTS.md for the user-facing version of
everything below.

- `<Role>-guardrails.md` - non-negotiable (legal/data-protection/security-
  shaped) requirements. Fixed content of THIS repository; no tool, role, or
  customization can ever change it at runtime.
- `<Role>-workflow.md` - the process this project's tooling mechanically
  enforces (stage gates, GitFlow, budget mechanics, tool call sequences).
  Also fixed content of THIS repository, for the same reason: a gate
  description that could silently drift from what the code actually
  enforces would be worse than no description at all.
- `spec-templates/DOD.md` / `DOR.md` - the Definition of Done / Definition
  of Ready checklists, for the roles that actually need to know them (see
  DOD_ROLES/DOR_ROLES below) - the literal file content, not a paraphrase,
  so this can never drift from the canonical checklist `read_doc` would
  return for the same path. Appended here, in the prompt composition
  itself, rather than left as something a role would only see if it
  happened to call `read_doc("spec-templates/DOD.md")` first.

All of the above are concatenated at import time, in that order
(guardrails, workflow, then DOD/DOR if applicable) - guardrails first,
since they take precedence over everything that follows.

A FOURTH piece, `<Role>-identity.md` - role-specific tone/emphasis, the
only part a project may actually customize - is intentionally NOT loaded
here. It lives in the product/state repository (not this one), is only
resolvable once STATE_REPO_PATH is known at runtime, and is injected
per-session by agent.py's role_identity_injection_callback instead of
being baked into this static instruction string - see that callback's own
docstring for why (and for the security framing injected alongside it).
`<Role>-identity.default.md`, alongside the prompt_modules/ files above, is
that callback's fallback/seed content when the product repo has none of
its own yet - never read by this module.
"""
from pathlib import Path

_PROMPT_MODULES_DIR = Path(__file__).resolve().parent / "prompt_modules"
_SPEC_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "spec-templates"

# Roles that need the Definition of Done in their own prompt, verbatim -
# exactly the 4 roles DOD.md's own checklist names as owning a stage
# (IMPLEMENTED/REVIEWED/TESTED/ACCEPTED): the roles actually "involved in
# the realisation of the tasks" (implementing, reviewing, testing, and
# accepting the resulting increment), not the roles that only facilitate
# or report on it (ScrumMaster, QualityGuardian) or only route (Orchestrator).
DOD_ROLES = ("DevTeam", "Architect", "QA", "ProductOwner")

# Roles that need the Definition of Ready in their own prompt, verbatim -
# the roles "responsible in specifying and documenting the requirements
# and technical concepts": Product Owner (requirements/stories) and
# Architect (technical feasibility/architecture concepts) - DOR.md's own
# text names READY as "Product Owner's stage gate (supported by Architect
# for technical feasibility)".
DOR_ROLES = ("ProductOwner", "Architect")

# Every role this project ships, mapped to the exact <Role> prefix its 3
# prompt_modules/ files use - also the "agent_name"/tool_context.agent_name
# value used elsewhere (agent.py's LlmAgent `name=`, ROLE_IDENTITY_ROLES
# below), so a role's prompt files, its ADK agent name, and the identity
# file a product repo would check in for it are always the same string.
ROLE_NAMES = (
    "ScrumOrchestrator",
    "ProductOwner",
    "ScrumMaster",
    "DevTeam",
    "QA",
    "Architect",
    "QualityGuardian",
)


def _load_checklist(name: str) -> str:
    """
    Reads spec-templates/<name>.md (DOD or DOR) - the same file `read_doc`
    would return for that path, so a role's prompt can never silently
    drift from the canonical checklist. Raises FileNotFoundError if it's
    missing; this project ships both files, so a missing one is a real
    packaging problem, not something to degrade gracefully around.
    """
    path = _SPEC_TEMPLATES_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Missing required checklist: {path}.")
    return path.read_text(encoding="utf-8").strip()


def _load_role_prompt(role: str) -> str:
    """
    Reads `<role>-guardrails.md` + `<role>-workflow.md` from
    prompt_modules/, then spec-templates/DOD.md and/or DOR.md if this role
    is in DOD_ROLES/DOR_ROLES, and concatenates all of it (in that order)
    into that role's static system-prompt text. Raises FileNotFoundError
    with a clear message if a required prompt_modules/ file is missing - a
    role silently running with no guardrails/workflow content at all would
    be far worse than failing loudly at import time.
    """
    parts = []
    for suffix in ("guardrails", "workflow"):
        path = _PROMPT_MODULES_DIR / f"{role}-{suffix}.md"
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing required prompt module for role {role!r}: {path}. "
                f"Every role in ROLE_NAMES must have both a *-guardrails.md and "
                f"*-workflow.md file under {_PROMPT_MODULES_DIR}."
            )
        parts.append(path.read_text(encoding="utf-8").strip())

    if role in DOD_ROLES:
        parts.append(
            "DEFINITION OF DONE (spec-templates/DOD.md, reproduced here verbatim so you always "
            "know it without a separate read_doc call - this is the literal checklist file, not a "
            "paraphrase, so it never drifts from what read_doc(\"spec-templates/DOD.md\") returns)\n\n"
            + _load_checklist("DOD")
        )
    if role in DOR_ROLES:
        parts.append(
            "DEFINITION OF READY (spec-templates/DOR.md, reproduced here verbatim so you always "
            "know it without a separate read_doc call - this is the literal checklist file, not a "
            "paraphrase, so it never drifts from what read_doc(\"spec-templates/DOR.md\") returns)\n\n"
            + _load_checklist("DOR")
        )

    return "\n\n" + "\n\n".join(parts) + "\n"


def load_role_identity_default(role: str) -> str:
    """
    Reads `<role>-identity.default.md` - the fallback/seed identity content
    used by agent.py's role_identity_injection_callback when the product/
    state repository has no `<role>-identity.md` of its own yet, and by
    check_state_repo.py's migration as the seed written into a fresh one.
    Distinct from _load_role_prompt above: this file is never concatenated
    into the static instruction strings below, only used dynamically/at
    migration time - see prompts.py's own module docstring.
    """
    path = _PROMPT_MODULES_DIR / f"{role}-identity.default.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing required default identity file for role {role!r}: {path}."
        )
    return path.read_text(encoding="utf-8").strip() + "\n"


ORCHESTRATOR_PROMPT = _load_role_prompt("ScrumOrchestrator")
PO_PROMPT = _load_role_prompt("ProductOwner")
SM_PROMPT = _load_role_prompt("ScrumMaster")
DEV_PROMPT = _load_role_prompt("DevTeam")
QA_PROMPT = _load_role_prompt("QA")
ARCH_PROMPT = _load_role_prompt("Architect")
QUALITY_GUARDIAN_PROMPT = _load_role_prompt("QualityGuardian")
