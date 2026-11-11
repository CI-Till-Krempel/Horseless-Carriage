# User Story

- Story ID: US-0021
- Title: Introduce the Workbench Template Mechanism
- Status: Draft
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## As an Architect, I want to select and customize a named framework-workbench template (toolchain install steps, MCP config, and package-repo network allowlist) instead of authoring `.hc/workbench-setup.sh` from scratch every time, so that setting up a new project's tooling is faster and consistent, while every change still requires human PR approval.

## Acceptance Criteria
- Given a project has no workbench configured yet, when the Architect proposes a workbench using a named template, then the resulting PR (via the extended `propose_framework_change`) includes that template's toolchain script content, its MCP config, and its declared network-allowlist hosts, under the same rationale/human-review flow `propose_framework_change` already enforces.
- Given a chosen template, when the Architect customizes it (e.g. adds an extra package or MCP server), then the customization is included in that same human-reviewed PR — no direct write path to `.hc/` is introduced; `docs.py`'s existing write-block keeps refusing direct `write_file`/`delete_file` calls under `.hc/`.
- Given a template's declared `additional_allowlist_hosts`, when the PR is opened, then the hosts are surfaced via the existing "HUMAN OPERATOR ACTION REQUIRED" PR-body section (today's behavior) — or, if this story also implements ADR-0002's tracked-allowlist-file design, written to `.hc/allowlist.filter` in the same commit. Either way, the human-approval guarantee must not regress.
- Given a template bundles an MCP config fragment, when a workbench is active, then the relevant role(s) can reach that MCP server — the merge/application mechanism is specified by this story, even if full per-role MCP wiring across all roles lands incrementally.
- Given no template matches a project's stack, when the Architect proposes a fully custom script (today's behavior), then that path keeps working unchanged — templates are additive, never a replacement for the existing free-form option.

## Notes
- Parent epic: EP-0010.
- Design/technical notes: extends `propose_framework_change` (`agents/scrum_team/tools/workflow.py:295-469`); needs a new template-resolution step (template name → script + MCP config + allowlist) before the existing PR-creation logic runs. Template storage should mirror `config/model-templates/`'s convention (e.g. `config/workbench-templates/<stack-name>/`).
- Depends on resolving how `additional_allowlist_hosts` is tracked going forward — see `specs/architecture/ADR-0002-Per-Project-Workbench-Network-Allowlist-Proposals.md` (Status: Proposed, not yet implemented). This story should either implement that ADR or explicitly scope itself as building on top of today's PR-body-only behavior and note the gap.
- Feeds US-0022 (reference templates need this mechanism to exist first) and US-0023 (doc describes this contract).

## Test Approach
- Unit tests for template resolution: given a template name, the correct script/MCP-config/allowlist bundle is loaded.
- Unit test asserting a customized template's content ends up only in the proposal PR, never written directly to the repo.
- Regression test confirming `write_file`/`delete_file` still refuse any `.hc/` path after this change (extend `agents/scrum_team/tests/test_docs.py`'s existing coverage).
- Test for the "no template selected, fully custom script" fallback path, matching today's `propose_framework_change` behavior.
- Test for `additional_allowlist_hosts` handling (PR-body section and/or `.hc/allowlist.filter`, per whichever design this story lands).
