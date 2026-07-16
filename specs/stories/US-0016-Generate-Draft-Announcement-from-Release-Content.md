# User Story

- Story ID: US-0016
- Title: Generate Draft Announcement from Release Content
- Status: Draft
- Priority: Should
- Owner: Product Owner
- Last Updated: 2026-07-16

## As a Product Owner, I want a tool that drafts a customer-facing announcement from the sprint's shipped stories/changelog, so I don't write it from scratch.

## Acceptance Criteria
- Given a generated changelog entry (US-0012/US-0013), when the announcement tool runs, then it drafts a `TEMPLATE-ANNOUNCEMENT.md`-shaped document translating changelog items into user-facing benefit language.
- Given internal-only changes (e.g. refactors, test additions) in the changelog, when drafting, then they are excluded from the customer-facing announcement rather than leaking implementation detail.
- Edge case: no changelog exists yet for this release — the tool reports that the announcement can't be drafted yet rather than producing an empty/fabricated one.

## Notes
- Parent epic: EP-0005. Depends on EP-0004 (changelog) as its content source.
- Design/technical notes: likely a `generate_announcement` tool in `agents/scrum_team/tools/docs.py` or a new `tools/announcements.py`, writing to `specs/announcements/ANNOUNCEMENT-{version}.md` in the target repo.

## Test Approach
- Unit test with a fixture changelog containing both user-facing and internal-only entries, asserting only the former appear in the drafted announcement.
- Unit test for the "no changelog yet" failure path.
