# User Story

- Story ID: US-0018
- Title: Add End-User Doc Template
- Status: Ready
- Priority: Should
- Owner: Dev Team
- Last Updated: 2026-07-16

## As a Product Owner, I want a `TEMPLATE-USER-GUIDE.md` defining structure for end-user how-to docs, so authors have a consistent format.

## Acceptance Criteria
- Given the new template directory, when authors write an end-user guide, then they follow a consistent structure (Overview, Prerequisites, Steps, Examples, Troubleshooting, Related).
- Given the house style of other template types, when this template is added, then it carries the same AGENT SAFEGUARD blueprint comment and metadata-bullet conventions.
- Edge case: a guide with no meaningful troubleshooting content — the section is kept but may state "no known issues" rather than being omitted (keeps structure predictable).

## Notes
- Parent epic: EP-0006.
- Status: **Ready** — this story is already satisfied. `spec-templates/product-docs/README.md` and `TEMPLATE-USER-GUIDE.md` were created as part of this same planning pass (see ADR-0001).

## Test Approach
- N/A (documentation template, not executable code). Verified by inspection against the other template directories' structure.
