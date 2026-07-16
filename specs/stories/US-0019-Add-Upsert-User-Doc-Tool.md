# User Story

- Story ID: US-0019
- Title: Add `upsert_user_doc` Tool
- Status: Draft
- Priority: Should
- Owner: Dev Team
- Last Updated: 2026-07-16

## As a Dev Team member, I want a tool analogous to `upsert_prd`/`upsert_adr`, so end-user docs can be created/updated the same way internal specs are.

## Acceptance Criteria
- Given a call to the new `upsert_user_doc` tool with a title and content sections, when invoked, then it writes a `TEMPLATE-USER-GUIDE.md`-shaped file to `specs/product-docs/{filename}` in the target repo, creating the file if absent and updating it if present.
- Given the naming convention used by `upsert_prd`/`upsert_adr`, when this tool assigns a filename, then it follows an equivalent, consistent pattern.
- Edge case: called with a filename that already exists — content is merged/updated rather than duplicated as a second file.

## Notes
- Parent epic: EP-0006.
- Design/technical notes: mirrors `upsert_prd()`'s implementation pattern in `agents/scrum_team/tools/docs.py`; also extend `list_docs()`'s glob roots to include the new `specs/product-docs/` location so it shows up in doc listings.

## Test Approach
- Unit test asserting a new file is created with expected content on first call.
- Unit test asserting a second call with the same filename updates rather than duplicates.
- Unit test asserting `list_docs()` now includes files from `specs/product-docs/`.
