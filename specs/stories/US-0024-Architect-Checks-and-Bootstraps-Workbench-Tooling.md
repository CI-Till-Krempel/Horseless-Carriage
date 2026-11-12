# User Story

- Story ID: US-0024
- Title: Architect Checks and Bootstraps Workbench Tooling
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-12

## As the team, I want the list of supported tech stacks documented and the Architect to regularly check whether this project's Development Workbench tooling is actually set up, so that a missing test framework/toolchain is caught and proposed for bootstrapping instead of silently discovered mid-sprint (or never at all).

## Acceptance Criteria
- Given the three shipped Development Workbench templates (US-0022: `python-flask`, `kotlin-multiplatform`, `react-vite-firebase`), when a human or agent wants "the list of supported tech stacks", then it's documented in one place (`docs/WORKBENCHES.md`), cross-linked rather than duplicated from the existing template contract docs.
- Given a project with no `.hc/workbench-setup.sh` yet (or still the auto-created placeholder), when the Architect's periodic check runs (at session startup, and at the start of every sprint), then it surfaces a concrete recommendation rather than silently doing nothing.
- Given a project whose tech stack is recognized (matches one of the three shipped templates' manifest signatures), when the check runs, then its recommendation names that template (`propose_framework_change(template_name=...)`).
- Given a project whose tech stack is NOT recognized, when the check runs, then it degrades gracefully to recommending the original fully-custom-script path (`propose_framework_change(workbench_setup_script=...)`, no `template_name`) - never a crash, never a silent no-op.
- Given a project whose workbench is already genuinely configured, when the check runs, then it is a no-op (no recommendation, `action_needed: False`) - it never second-guesses a deliberate customization just because a cheap manifest sniff disagrees with it.
- Given any new allowlist host this leads the Architect to propose, when `propose_framework_change` opens the resulting PR, then the existing "⚠️ HUMAN OPERATOR ACTION REQUIRED" PR-body mechanism is reused unchanged - no new silent-allowlist-write path is introduced.

## Notes
- Builds directly on EP-0010 (US-0021/US-0022/US-0023) - this issue was moved from v0.5 to v0.3 specifically because it's "largely a consequence" of that epic shipping (see `specs/ROADMAP.md`).
- `check_state_repo.py`'s `ensure_workbench_script_default`/`is_workbench_script_placeholder` already define "configured vs. placeholder" for the host-side `doctor.py` nudge, but that script is never copied into the agent image (see `agent.Dockerfile`) - this story's in-container check necessarily duplicates that placeholder constant (the same "kept independent deliberately" shape `tools/test_runners.py`'s own module docstring already documents for an analogous host/container split), cross-checked by a test so the two can't silently drift apart.
- GH issue #456's own text: "Check regularly (at startup / each sprint)... Keep in mind to propose allowlist entries... (need human approval)" - both halves are addressed: the periodic check (startup + each sprint) and the existing human-approval allowlist flow, reused rather than reinvented.

## Implementation Notes (as built)
- `agents/scrum_team/tools/workbench_templates.py`:
  - `WORKBENCH_SCRIPT_RELATIVE_PATH` (`.hc/workbench-setup.sh`) - the single source of truth for this path, now also imported by `tools/workflow.py` instead of that module declaring its own separate copy.
  - `_WORKBENCH_SCRIPT_PLACEHOLDER` - byte-for-byte duplicate of `check_state_repo.py`'s own constant (see Notes above for why it's duplicated, not imported); cross-checked by `test_workbench_templates.py`.
  - `_TECH_STACK_DETECTORS` / `detect_recommended_workbench_template(repo_root)` - one best-effort manifest-sniffing detector per shipped reference template: `requirements.txt`/`Pipfile`/`pyproject.toml` mentioning "flask" for `python-flask`; `build.gradle(.kts)`/`settings.gradle(.kts)` mentioning "multiplatform" (or a `src/commonMain` source-set layout) for `kotlin-multiplatform`; `package.json` mentioning both "vite" and "react"/"firebase" for `react-vite-firebase`. Never raises; an unreadable/missing manifest is just "that detector doesn't match".
  - `check_workbench_tooling(tool_context=None)` - the periodic check itself. Reads `.hc/workbench-setup.sh` from the product/state repo (`_configured_repo_root`), compares against the placeholder, runs the stack detectors (cross-checked against `list_workbench_templates()` so a stale detector match against a renamed/removed template is never recommended), and returns a structured `{"status", "workbench_configured", "detected_tech_stack", "action_needed", "recommendation"}` - never writes anything, never calls `propose_framework_change` itself.
- `agents/scrum_team/tools/scrum.py`: `init_scrum_state` (the "at startup" check - GH #423: available to every role) and `start_sprint` (the "each sprint" check - ScrumMaster's own tool) both call `check_workbench_tooling` best-effort and fold the result into their own return value as `workbench_check`, so a caller sees it without a separate round-trip. Both wrapped so this check's own failure can never block state init or sprint start.
- `agents/scrum_team/agent.py`: registered `check_workbench_tooling` as one of Architect's own tools (alongside the existing `propose_framework_change`/`list_workbench_templates`/`get_workbench_template`).
- `agents/scrum_team/prompt_modules/Architect-workflow.md`: new "DEVELOPMENT WORKBENCH TOOLING" section - call `check_workbench_tooling()` early in a sprint or when ScrumMaster names `action_needed`; recognized stack -> `propose_framework_change(template_name=...)`; unrecognized -> the fully-custom-script fallback; any new allowlist host always goes through `additional_allowlist_hosts` (the existing human-approval PR-body mechanism), never a direct edit.
- `agents/scrum_team/prompt_modules/ScrumMaster-workflow.md`: new bullet under Sprint Planning - read `start_sprint`'s own `workbench_check`, and `transfer_to_agent` to Architect with its `recommendation` when `action_needed` is true.
- `docs/WORKBENCHES.md`: new "Supported tech stacks" section (a table of the three shipped templates, their tech stack, and their declared allowlist hosts - cross-linked to the live `list_workbench_templates()`/`get_workbench_template()` tools as the always-current source of truth) and a new "Checking whether a project's workbench is actually set up (GH-456)" section documenting `check_workbench_tooling` and where it's wired in.
- `specs/ROADMAP.md`: marked `[GH-456]` done in the v0.3 section.
- Tests: `agents/scrum_team/tests/test_workbench_templates.py` - `check_workbench_tooling` for "not configured, recognized stack -> template recommendation", "not configured, unrecognized stack -> fully-custom-script fallback, no crash", "already configured -> no-op even when the detected stack doesn't match", each detector's positive/negative manifest-sniffing cases, and the placeholder-constant cross-check against `check_state_repo.py`'s own. `agents/scrum_team/tests/test_scrum.py` - `init_scrum_state`/`start_sprint` both surface a `workbench_check` field without breaking any existing assertion on their return shape.

## Test Approach
- Unit tests for `check_workbench_tooling`/`detect_recommended_workbench_template` directly (temp-dir fixtures with/without each manifest signature, with/without a real `.hc/workbench-setup.sh`), plus integration-style assertions that `init_scrum_state`/`start_sprint` surface the same field. Full suite run via `python3 run_tests.py` before merge.
