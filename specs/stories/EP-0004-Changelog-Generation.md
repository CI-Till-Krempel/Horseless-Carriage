# Epic

- Epic ID: EP-0004
- Title: Changelog Generation
- Status: Draft
- Priority: Should
- Owner: Product Owner
- Last Updated: 2026-07-16

## Overview
No `CHANGELOG.md`, template, or generation code exists anywhere in the tooling today. This epic introduces automated changelog generation for the product repo at release time, derived from real sprint state rather than manually authored after the fact.

## User Stories / Features
- US-0012 — Generate Changelog Entry per Release
- US-0013 — Derive Changelog Content from Sprint State
- US-0014 — Include Changelog in Release PR

## Acceptance Criteria
- A changelog entry is generated automatically for each release.
- Entry content is derived from `sprint_backlog`/`decision_log` state, not freehand text.
- The changelog update ships in the same release PR as the code it describes.

## Notes
- Likely touch point: a new function alongside `create_release_pr` in `agents/scrum_team/tools/github.py`, or a new `agents/scrum_team/tools/changelog.py`.
- Depends on EP-0003: the changelog can only be trusted to describe "what shipped" once release-PR scope is verified rather than assumed.
- Feeds EP-0005 (Customer-Facing Announcement Generation), which drafts from this changelog content.

## Roadmap
- Targeted for v0.2 — Release Communication & Documentation (see `specs/ROADMAP.md`).
