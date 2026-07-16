# Epic

- Epic ID: EP-0005
- Title: Customer-Facing Announcement Generation
- Status: Draft
- Priority: Should
- Owner: Product Owner
- Last Updated: 2026-07-16

## Overview
No tool, template, or prompt reference for a customer-facing release announcement exists today. This epic adds a lightweight template and generation tool so each release produces an external, non-technical announcement alongside its internal artifacts.

## User Stories / Features
- US-0015 — Add Announcement Template
- US-0016 — Generate Draft Announcement from Release Content
- US-0017 — Require Announcement Drafting in Sprint Review

## Acceptance Criteria
- A consistent announcement template exists (`spec-templates/announcements/TEMPLATE-ANNOUNCEMENT.md`).
- A tool can draft an announcement from the release's changelog/sprint-report content.
- The Product Owner prompt mandates this step during Sprint Review so it isn't silently skipped, the way KPIs and changelog generation were before this roadmap.

## Notes
- Depends on EP-0004 (Changelog Generation) as its primary content source.
- Touch points: new `generate_announcement` tool (in `agents/scrum_team/tools/docs.py` or a new `tools/announcements.py`), and `PO_PROMPT` in `agents/scrum_team/prompts.py`.
- Output location: `specs/announcements/ANNOUNCEMENT-{version}.md` in the target repo.

## Roadmap
- Targeted for v0.2 — Release Communication & Documentation (see `specs/ROADMAP.md`).
