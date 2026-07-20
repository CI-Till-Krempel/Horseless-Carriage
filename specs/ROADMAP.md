# Product Roadmap

Use this living roadmap to plan releases and track user stories across states. It doubles as a lightweight task board and a release planning tool.

## How to use
- Story IDs should match files in `specs/stories/` (e.g., `US-0001` corresponds to `specs/stories/US-0001-Capture-Sub-Agent-Turns-in-Conversation-History.md`; `EP-0001` corresponds to `specs/stories/EP-0001-Multi-Agent-Conversation-Transcript-Capture.md`).
- Move story references between states as work progresses.
- Keep titles short; full details live in the story file.
- For each planned version, list goals and the set of stories targeted for that release.
- When a release is cut, freeze the section by adding the actual tag (e.g., `v0.1.0`) and dates.

Legend
- `[ID] Title` — a user story or epic reference and its short title
- Checkbox states: `- [ ]` To Do, `- [~]` In Progress (use `- [~]` to signal WIP), `- [R]` In Review, `- [x]` Done

---

## Release plan (versions → stories)

### v0.1 — Trust & Integrity Fixes (target: 2026-08)
Goals
- Stop the sprint pipeline from silently fabricating or dropping sprint-end artifacts.
- Capture the full multi-agent conversation, not just the Orchestrator's turns.
- Make KPI/test-report numbers real instead of hardcoded dummy data.
- Guarantee release PRs actually contain the full sprint increment.

Stories:
- [x] [EP-0001] Multi-Agent Conversation Transcript Capture
  - [x] [US-0001] Capture Sub-Agent Turns in Conversation History
  - [x] [US-0002] Persist Full Transcript to State Repo
  - [x] [US-0003] Expose Transcript in Sprint Report
  - [x] [US-0004] Trim Transcript for Token Budget
- [ ] [EP-0002] Real Test-Report and KPI Generation
  - [x] [US-0005] Execute Test Suite and Collect Coverage
  - [x] [US-0006] Compute Real Maintainability Metrics
  - [ ] [US-0007] Run Security Vulnerability Scan
  - [ ] [US-0008] Fail Gracefully When Test Tooling Unavailable
- [ ] [EP-0003] Enforce Full Sprint Increment in Release PRs
  - [ ] [US-0009] Track Sprint-Touched Files
  - [ ] [US-0010] Verify Release PR Diff Against Sprint Tracking
  - [ ] [US-0011] Block Release on Uncommitted Sprint Work

### v0.2 — Release Communication & Documentation (target: 2026-10)
Goals
- Generate an accurate, automated changelog per release.
- Draft a customer-facing announcement from real release content.
- Extend documentation tooling to cover end-user product docs, not just internal specs.

Stories:
- [ ] [EP-0004] Changelog Generation
  - [ ] [US-0012] Generate Changelog Entry per Release
  - [ ] [US-0013] Derive Changelog Content from Sprint State
  - [ ] [US-0014] Include Changelog in Release PR
- [ ] [EP-0005] Customer-Facing Announcement Generation
  - [x] [US-0015] Add Announcement Template
  - [ ] [US-0016] Generate Draft Announcement from Release Content
  - [ ] [US-0017] Require Announcement Drafting in Sprint Review
- [ ] [EP-0006] Product Documentation Tooling for End-User Docs
  - [x] [US-0018] Add End-User Doc Template
  - [ ] [US-0019] Add `upsert_user_doc` Tool
  - [ ] [US-0020] Wire Product Docs into Definition of Done

### Backlog (unplanned)
- None currently — all identified gaps are scheduled into v0.1/v0.2 above.

---

## Task board (Kanban)

### v0.1 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| US-0007–US-0008, EP-0003, US-0009–US-0011 | | | EP-0001, US-0001, US-0002, US-0003, US-0004, US-0005, US-0006 |

Notes
- Update this table in PRs alongside code changes.
- Keep the board limited to the current sprint scope if you're also running sprints.

### v0.2 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| EP-0004, US-0012–US-0014, EP-0005, US-0016–US-0017, EP-0006, US-0019–US-0020 | | | US-0015, US-0018 |

---

## Cross-cutting initiatives (optional)
- **Sprint-artifact trust**: EP-0001, EP-0002, EP-0003 all address the same underlying theme — sprint-end artifacts must reflect what actually happened, not what the process assumed happened. See ADR-0001 for the related tooling-convention decisions (implementation plans, release representation).
- **Release communications chain**: EP-0004 → EP-0005 is a direct dependency chain (announcement drafts from changelog content); EP-0006 is independent but grouped into the same release wave thematically.

---

## Release checklist (for when cutting a release)
- [ ] All included stories are in `Done` and meet Definition of Done
- [ ] Docs updated (stories, PRD/SRS, ADRs as needed)
- [ ] Version/tag created (e.g., `v0.1.0`) and changelog drafted
- [ ] Known issues captured and follow-ups added to backlog

---

## Index of story references

- v0.1: EP-0001, US-0001, US-0002, US-0003, US-0004, EP-0002, US-0005, US-0006, US-0007, US-0008, EP-0003, US-0009, US-0010, US-0011
- v0.2: EP-0004, US-0012, US-0013, US-0014, EP-0005, US-0015, US-0016, US-0017, EP-0006, US-0018, US-0019, US-0020
- Unplanned: none

Keep this file updated in the same PRs that move work forward.
