# User Story

- Story ID: US-0023
- Title: Document How to Contribute a New Development Workbench
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## As a contributor, I want documentation describing the workbench-template contract and the PR-review process, so that I can add support for a new tech stack without reverse-engineering the existing templates.

## Acceptance Criteria
- Given the new doc, when a contributor reads it, then they can find: the required template directory layout (toolchain/Dockerfile description, MCP config, network allowlist), how to wire up scaffold/dev/test/exploratory-test/run/debug tooling, and how `propose_framework_change` turns a template into a human-reviewed PR.
- Given one of the reference templates from US-0022, when the doc references it, then it's used as a concrete worked example rather than a purely abstract description.
- Given the network-allowlist step, when the doc covers it, then it explicitly explains the human-approval flow (today's PR-body "HUMAN OPERATOR ACTION REQUIRED" section, or its ADR-0002 successor if that ships first) so contributors don't assume allowlist changes apply automatically.
- Doc is added under `docs/` (e.g. `docs/WORKBENCHES.md`) and linked from the relevant existing docs (`docs/CONFIGURATION.md` and/or `docs/ARCHITECTURE.md`).

## Notes
- Parent epic: EP-0010.
- Depends on US-0021 (mechanism) for the contract to document, and benefits from US-0022's templates existing as worked examples — can be drafted in parallel and finalized once both land.
- Should cross-reference GH-456 ("document the list of supported tech stacks...") and note that this epic implements GH-291's Phase 1 (the Development Workbench, for technical roles); PO/UX/Testing-role workbenches are a later phase of GH-291, not covered by this doc.

## Implementation Notes (as built)
- Added `docs/WORKBENCHES.md`, covering:
  - The required template directory layout under
    `config/development-workbench-templates/<name>/` — `workbench-setup.sh`
    (required), `mcp-config.json` (optional), `manifest.json` (optional) —
    matching exactly what `resolve_workbench_template`/`get_workbench_template`
    (`agents/scrum_team/tools/workbench_templates.py`) implement, including
    the `^[a-z0-9]+(-[a-z0-9]+)*$` name-validation rule and the "missing
    `workbench-setup.sh` is silently skipped, not an error" discovery
    behavior of `list_workbench_templates`.
  - How scaffold/dev/test/exploratory-test/run/debug tooling is wired up —
    there's no per-concern schema field; each template's script installs
    packages, then ends with a heredoc documenting the actual commands, as
    all three reference templates do.
  - How `propose_framework_change` (`agents/scrum_team/tools/workflow.py`)
    turns a selected/customized template into a human-reviewed draft PR,
    documenting its `template_name` and `additional_mcp_config` parameters
    (plus `workbench_setup_script`, `additional_allowlist_hosts`,
    `rationale`) and the "templates are additive, the fully-custom-script
    path keeps working unchanged" guarantee.
  - The network-allowlist step, explained explicitly as **today's**
    PR-body "⚠️ HUMAN OPERATOR ACTION REQUIRED" flow (add hosts to
    `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` in Horseless Carriage's own `.env`,
    then `docker compose up`) — stated clearly that merging the workbench
    PR alone does **not** grant the new network access, and that this
    builds on top of today's behavior rather than ADR-0002's (still
    "Proposed", unimplemented) tracked-`.hc/allowlist.filter` design.
  - `python-flask` used as the concrete worked example (full
    `manifest.json`/`mcp-config.json`, an abridged `workbench-setup.sh`,
    and a line-by-line mapping from its heredoc back onto the
    scaffold/install/test/run/debug/exploratory-test contract), with
    `kotlin-multiplatform` (iOS build/run/debug limitation) and
    `react-vite-firebase` (emulator suite) referenced as further examples
    of the same shape.
  - A "Who this is for" / scope-note section cross-referencing GH-456 and
    stating explicitly that this epic implements GH-291's Phase 1 (the
    Development Workbench, technical roles only —  `architect`, `dev_team`,
    `qa_agent`, `quality_guardian`); PO/UX/Testing-role workbenches are a
    later phase of GH-291, not covered here.
  - A step-by-step "Adding a new template" section for a contributor adding
    a fourth stack.
- Linked the new doc from both `docs/CONFIGURATION.md` (a new "Development
  Workbench templates × network allowlist" subsection, placed just before
  "Known-safe no-ops", covering the `.env`/`NET_PROXY_EXTRA_ALLOWLIST_HOSTS`
  interaction) and `docs/ARCHITECTURE.md` (a new "Development Workbench
  templates" section just before "Repository documentation structure").
- Sanity-checked every function/file/path referenced in the doc actually
  exists under the given name before finalizing: `resolve_workbench_template`,
  `get_workbench_template`, `list_workbench_templates`, `propose_framework_change`,
  `write_file`/`delete_file`'s `.hc/` refusal in `agents/scrum_team/tools/docs.py`,
  every reference template file path, `config/net-proxy/allowlist.filter`,
  `agent.Dockerfile`'s `workbench` build context and
  `config/development-workbench-templates` COPY, `.env.example`'s
  `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` entry, and
  `specs/architecture/ADR-0002-Per-Project-Workbench-Network-Allowlist-Proposals.md`
  — all grepped/read directly off disk, none asserted from memory.
- Docs-only story: no automated test added, per this story's own Test
  Approach. Verified via PR review.

## Test Approach
- Docs-only story — no automated test; verified via PR review, consistent with this repo's Definition of Done ("Docs updated").
