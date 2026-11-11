# User Story

- Story ID: US-0023
- Title: Document How to Contribute a New Development Workbench
- Status: Draft
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

## Test Approach
- Docs-only story — no automated test; verified via PR review, consistent with this repo's Definition of Done ("Docs updated").
