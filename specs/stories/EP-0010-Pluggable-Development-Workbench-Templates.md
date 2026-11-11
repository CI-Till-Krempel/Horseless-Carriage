# Epic

- Epic ID: EP-0010
- Title: Pluggable Development Workbench Templates
- Status: Draft
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
Today, a project's "workbench" — the toolchain (JDK, Node, Go, ...) the agent image needs to scaffold/build/test/run the actual product's tech stack — is a single hand-written shell script, `.hc/workbench-setup.sh`, that the Architect authors from scratch per project and proposes as a PR via `propose_framework_change` (`agents/scrum_team/tools/workflow.py:302-469`). There is no library of pre-built starting points, no concept of a per-workbench MCP configuration, and the network allowlist a workbench needs (`additional_allowlist_hosts`) is still PR-body-only text — `specs/architecture/ADR-0002-Per-Project-Workbench-Network-Allowlist-Proposals.md` proposes turning it into a tracked `.hc/allowlist.filter` file, but that ADR's own Status is "Proposed", not implemented.

This epic introduces a library of named, reusable **Development Workbench templates** (e.g. under `config/development-workbench-templates/<stack-name>/`), each bundling:
- A Dockerfile/script description installing everything needed to scaffold, develop, run test suites, do exploratory testing, and run/debug an application in that framework.
- An MCP configuration for any MCP servers relevant to that stack.
- A network allowlist of the package-repository hosts the toolchain needs at runtime.

The "Development" prefix is deliberate: this is the workbench for the technical development roles (`architect`, `dev_team`, `qa_agent`, `quality_guardian`) under GH-291's "Role Workbenches" concept. GH-291 scopes role-specific workbenches broadly (e.g. Figma for UX Designers, tooling for the Product Owner role); per the decision recorded on that issue, this epic covers only the Development Workbench — PO/UX/Testing-role workbenches are explicitly out of scope here and may be picked up later as separate epics.

The Architect selects and customizes a template per project, still exclusively through `propose_framework_change` (extended, not replaced) — every change remains a human-reviewed draft PR, exactly as today. This mirrors the existing `config/model-templates/` pattern (named, swappable LLM-provider configs picked by `setup_llm.py`), applied to tech-stack tooling instead of model routing.

## User Stories / Features
- US-0021 — Introduce the Development Workbench Template Mechanism
- US-0022 — Ship Three Reference Development Workbenches (Flask, Kotlin Multiplatform, React+Firebase)
- US-0023 — Document How to Contribute a New Development Workbench

## Acceptance Criteria
- An Architect can select from a library of pre-built workbench templates (not only hand-write a script from scratch) when proposing a project's toolchain via `propose_framework_change`.
- A template bundles toolchain installation (scaffold/dev/test/exploratory-test/run/debug), an MCP config, and a network allowlist, each reviewable independently in the resulting PR.
- Every workbench change — template selection or customization — still requires human approval via a draft PR, identical to today's `propose_framework_change` guarantee. No new unreviewed-write path is introduced.
- Three reference templates ship: `python-flask`, `kotlin-multiplatform` (Android & iOS), `react-vite-firebase` (Functions + Firestore).
- A contributor-facing doc explains the template contract well enough that someone can add a fourth stack without reverse-engineering the existing ones.

## Notes
- Builds directly on `.hc/workbench-setup.sh`, `propose_framework_change` (`agents/scrum_team/tools/workflow.py:295-469`), `agent.Dockerfile`'s `COPY --from=workbench ...` build context, `check_state_repo.py`'s `ensure_workbench_script_default`, and `docs.py`'s `.hc/` write-block (direct `write_file`/`delete_file` must keep refusing any `.hc/` path).
- Templates should be stored the way `config/model-templates/` already stores LLM provider configs (plain, named files/directories selected at setup/propose time) — same precedent, different axis (tech stack instead of model provider).
- This epic is GH-291's Phase 1: the Development Workbench (technical roles only — `architect`, `dev_team`, `qa_agent`, `quality_guardian`), scoped per decision recorded on GH-291. Role-specific workbenches for Product Owner, UX, and Testing roles are explicitly deferred to later phases of GH-291, not covered here.
- No per-project MCP config mechanism exists today — this repo's own `.mcp.json` is Horseless Carriage's own dev-tooling MCP config, not a per-workbench one. The MCP-config piece of this epic is net-new surface.
- Relates to GH-456 ("architect sets up necessary bootstrapping and tooling... check regularly whether tooling is set up"), moved from v0.5 to v0.3 since it's largely a consequence of this epic shipping.
- Relates to ADR-0002 (network allowlist tracking) — this epic either implements that ADR's `.hc/allowlist.filter` design or explicitly documents that it's still building on today's PR-body-only allowlist behavior; it must not silently regress ADR-0002's intent.

## Roadmap
- Targeted for v0.3 — headline feature alongside the existing process/governance stories (see `specs/ROADMAP.md`).
