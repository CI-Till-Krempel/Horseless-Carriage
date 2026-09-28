# agents/scrum_team/prompts.py
"""
Each role's system prompt is assembled from 2 files under prompt_modules/,
never a single hand-edited blob (see GH issue: "modularize agent system
prompts"):

- `<Role>-guardrails.md` - non-negotiable (legal/data-protection/security-
  shaped) requirements. Fixed content of THIS repository; no tool, role, or
  customization can ever change it at runtime.
- `<Role>-workflow.md` - the process this project's tooling mechanically
  enforces (stage gates, GitFlow, budget mechanics, tool call sequences).
  Also fixed content of THIS repository, for the same reason: a gate
  description that could silently drift from what the code actually
  enforces would be worse than no description at all.

Both are concatenated here, at import time, in that order - guardrails
first, since they take precedence over everything that follows.

A THIRD piece, `<Role>-identity.md` - role-specific tone/emphasis, the only
part a project may actually customize - is intentionally NOT loaded here.
It lives in the product/state repository (not this one), is only
resolvable once STATE_REPO_PATH is known at runtime, and is injected
per-session by agent.py's role_identity_injection_callback instead of
being baked into this static instruction string - see that callback's own
docstring for why (and for the security framing injected alongside it).
`<Role>-identity.default.md`, alongside the two files above, is that
callback's fallback/seed content when the product repo has none of its own
yet - never read by this module.
"""
from pathlib import Path

_PROMPT_MODULES_DIR = Path(__file__).resolve().parent / "prompt_modules"

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


def _load_role_prompt(role: str) -> str:
    """
    Reads `<role>-guardrails.md` + `<role>-workflow.md` from
    prompt_modules/ and concatenates them (guardrails first) into that
    role's static system-prompt text. Raises FileNotFoundError with a
    clear message if either file is missing - a role silently running
    with no guardrails/workflow content at all would be far worse than
    failing loudly at import time.
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
