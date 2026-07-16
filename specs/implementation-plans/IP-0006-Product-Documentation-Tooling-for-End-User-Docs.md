# Implementation Plan

- Plan ID: IP-0006
- Title: Product Documentation Tooling for End-User Docs
- Epic: EP-0006
- Status: Draft
- Owner: Dev Team
- Last Updated: 2026-07-16

## 1. Objective
Extend the docs tooling to cover end-user, how-to-use-the-product documentation, distinct from the existing internal PRD/SRS/ADR coverage, and ensure it's kept current via the Definition of Done.

## 2. Approach
The template scaffolding (`spec-templates/product-docs/README.md`, `TEMPLATE-USER-GUIDE.md`) already exists as of this planning pass (US-0018, done). Add an `upsert_user_doc` tool in `agents/scrum_team/tools/docs.py` mirroring `upsert_prd`'s implementation pattern, writing to `specs/product-docs/{filename}` in the target repo. Extend `list_docs()`'s glob roots to include this new location. Update `DEV_PROMPT` and the default `definition_of_done` content in `agents/scrum_team/state.py` to require end-user doc updates whenever a completed story changes user-facing behavior.

## 3. Affected Components / Files
- `agents/scrum_team/tools/docs.py` — new `upsert_user_doc`, extended `list_docs()`.
- `agents/scrum_team/prompts.py` — `DEV_PROMPT`.
- `agents/scrum_team/state.py` — default `definition_of_done` content.

## 4. Steps / Milestones
1. Confirm/finalize `TEMPLATE-USER-GUIDE.md` structure (already done, US-0018).
2. Implement `upsert_user_doc`, following `upsert_prd`'s create-or-update pattern (US-0019).
3. Extend `list_docs()` to glob `specs/product-docs/` alongside existing roots (US-0019).
4. Update `DEV_PROMPT` and `definition_of_done` to require `upsert_user_doc` calls for user-facing stories, with an explicit "not applicable" path for internal-only changes (US-0020).

## 5. Testing / Verification
- Unit tests per US-0019 acceptance criteria (create, update, `list_docs()` inclusion).
- Prompt/DoD-content review plus an integration test simulating completion of a user-facing story and asserting `upsert_user_doc` is invoked (US-0020).

## 6. Risks & Mitigations
- Risk: prompt-only DoD enforcement can be skipped, same limitation noted in IP-0005. Mitigation: track as a known limitation; revisit with code-level enforcement if needed.
- Risk: ambiguity over what counts as "user-facing" for a given story. Mitigation: explicit "not applicable" acknowledgment path keeps the decision visible rather than silently defaulted either way.

## 7. Rollout / Rollback
- New, additive tool plus prompt/DoD text changes — no schema migration. Rollback is removing the new tool and reverting prompt/DoD text.

## 8. References
- EP-0006, US-0018, US-0019, US-0020.
