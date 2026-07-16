# Epic

- Epic ID: EP-0006
- Title: Product Documentation Tooling for End-User Docs
- Status: Draft
- Priority: Should
- Owner: Dev Team
- Last Updated: 2026-07-16

## Overview
`agents/scrum_team/tools/docs.py` today only covers internal/requirements documentation (PRD, SRS, ADR) — there is no tool or template for end-user, how-to-use-the-product documentation. This epic extends the docs tooling to cover that gap, and wires it into the Definition of Done so it doesn't silently go stale.

## User Stories / Features
- US-0018 — Add End-User Doc Template
- US-0019 — Add `upsert_user_doc` Tool
- US-0020 — Wire Product Docs into Definition of Done

## Acceptance Criteria
- A `TEMPLATE-USER-GUIDE.md` exists defining a consistent structure for end-user docs.
- An `upsert_user_doc` tool exists, mirroring `upsert_prd`'s pattern, for creating/updating end-user docs.
- The Definition of Done and `DEV_PROMPT` require updating end-user docs when user-facing behavior changes.

## Notes
- Touch points: `agents/scrum_team/tools/docs.py` (new `upsert_user_doc`, extend `list_docs()` scan roots), `spec-templates/product-docs/` (new template type, already added), `agents/scrum_team/prompts.py` (`DEV_PROMPT`/Definition of Done text).
- Output location: distinct from `specs/requirements/` (internal specs) — proposed `specs/product-docs/` in the target repo, mirroring this project's own `spec-templates/product-docs/`.

## Roadmap
- Targeted for v0.2 — Release Communication & Documentation (see `specs/ROADMAP.md`).
