# Product Roadmap

Use this living roadmap to plan releases and track user stories across states. It doubles as a lightweight task board and a release planning tool.

## How to use
- Story IDs should match files in `specs/stories/` (e.g., `US-0001` corresponds to `specs/stories/US-0001-Capture-Sub-Agent-Turns-in-Conversation-History.md`; `EP-0001` corresponds to `specs/stories/EP-0001-Multi-Agent-Conversation-Transcript-Capture.md`).
- `[GH-NNN]` references a GitHub issue that hasn't been broken down into EP/US story files yet. When planning work for that release starts, write the proper story file(s) and swap the `[GH-NNN]` reference for the resulting `[EP-NNNN]`/`[US-NNNN]` IDs.
- Move story references between states as work progresses.
- Keep titles short; full details live in the story file (or the linked GitHub issue, for `[GH-NNN]` entries).
- For each planned version, list goals and the set of stories targeted for that release.
- When a release is cut, freeze the section by adding the actual tag (e.g., `v0.1.0`) and dates.
- Every open GitHub issue should carry a milestone matching one of the versions below (or the `Research backlog` wave) — see "Sync GitHub issues and versions to roadmap" below.

Legend
- `[ID] Title` — a user story or epic reference and its short title
- `[GH-NNN] Title` — an un-storied GitHub issue reference and its title
- Checkbox states: `- [ ]` To Do, `- [~]` In Progress (use `- [~]` to signal WIP), `- [R]` In Review, `- [x]` Done

---

## Release plan (versions → stories)

### v0.1 — Trust & Integrity Fixes (Released: `v0.1.0`, 2026-09-24)
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
- [x] [EP-0002] Real Test-Report and KPI Generation
  - [x] [US-0005] Execute Test Suite and Collect Coverage
  - [x] [US-0006] Compute Real Maintainability Metrics
  - [x] [US-0007] Run Security Vulnerability Scan
  - [x] [US-0008] Fail Gracefully When Test Tooling Unavailable
- [x] [EP-0003] Enforce Full Sprint Increment in Release PRs
  - [x] [US-0009] Track Sprint-Touched Files
  - [x] [US-0010] Verify Release PR Diff Against Sprint Tracking
  - [x] [US-0011] Block Release on Uncommitted Sprint Work

### v0.2 — Release Communication & Documentation (Released: `v0.2.0`, 2026-11-10)
Goals
- Generate an accurate, automated changelog per release.
- Draft a customer-facing announcement from real release content.
- Extend documentation tooling to cover end-user product docs, not just internal specs.

Stories:
- [x] [EP-0004] Changelog Generation
  - [x] [US-0012] Generate Changelog Entry per Release
  - [x] [US-0013] Derive Changelog Content from Sprint State
  - [x] [US-0014] Include Changelog in Release PR
- [x] [EP-0005] Customer-Facing Announcement Generation
  - [x] [US-0015] Add Announcement Template
  - [x] [US-0016] Generate Draft Announcement from Release Content
  - [x] [US-0017] Require Announcement Drafting in Sprint Review
- [x] [EP-0006] Product Documentation Tooling for End-User Docs
  - [x] [US-0018] Add End-User Doc Template
  - [x] [US-0019] Add `upsert_user_doc` Tool
  - [x] [US-0020] Wire Product Docs into Definition of Done

### v0.3 — Process Trust, Versioning & Governance (planned)
Goals
- Enforce semantic versioning discipline and a "snapshot" suffix during development.
- Let the stack point at a local-network Ollama server instead of only a bundled container.
- Keep GitHub issues/milestones and this roadmap in sync going forward.
- Close the loop on reported issues that stall instead of getting planned or explicitly declined.
- Add a QA release-testing gate and tighten sprint-approval/review mechanics.
- Investigate agent context-window/prompt-cache health (ADK cache-miss warning, conversation token budget).

Stories:
- [ ] [GH-501] Enforce semantic versioning and a development "snapshot" suffix
- [ ] [GH-502] Support pointing Horseless Carriage at a local-network Ollama server (LAN IP/hostname)
- [ ] [GH-272] Sync GitHub Issues and versions to roadmap
- [ ] [GH-295] Issues are reported but not fixed, or marked as Won't
- [ ] [GH-273] Release testing (QA audit/regression gate before shipping)
- [ ] [GH-452] Approval of the sprint should be done after creating the sprint PR
- [ ] [GH-316] Use CODEOWNER mechanism to enforce reviews for files that require human approval
- [ ] [GH-513] Context window optimizations (per-story/task conversations, leaner steering docs, prompt-size metrics)
- [ ] [GH-515] Investigate ADK performance warning: system instructions changing mid-session breaks prompt-cache alignment

### v0.4 — Security & Platform Hardening (planned)
Goals
- Integrate Heinzelmann.
- Harden the Docker-based deployment and document the network sandboxing model.
- Apply least-privilege to agent roles and add real authentication in front of ADK web UI / LiteLLM UI.
- Isolate the process state machine from agents with repo access.

Stories:
- [ ] [GH-503] Heinzelmann integration
- [ ] [GH-179] Harden Docker images
- [ ] [GH-180] Improve and document Network Sandboxing
- [ ] [GH-300] Apply least privilege principle
- [ ] [GH-304] Add real authentication in front of ADK web UI / LiteLLM UI
- [ ] [GH-286] Move Development Process Tooling State machine and state into a separate Docker container

### v0.5 — Dashboard & Onboarding UX (planned)
Goals
- Make sprint progress, cost, and platform links visible from the dashboard.
- Fix rough edges in day-to-day usability (log ordering, Windows tool calls).
- Lower time-to-first-sprint for new projects.

Stories:
- [ ] [GH-453] Links to the ADK web interface and litellm instance in the dashboard
- [ ] [GH-278] Sticky header showing current sprint budget, and total project cost
- [ ] [GH-312] ADK Eval run logs are out of order
- [ ] [GH-188] Tool calls do not work on Windows
- [ ] [GH-293] Quick start script
- [ ] [GH-456] Document the list of supported tech stacks and let the architect set up the necessary bootstrapping and tooling

### v0.6 — LiteLLM & ADK Platform Depth (planned)
Goals
- Exploit LiteLLM's MCP and skills integrations from specific agent roles.
- Make broader use of newer ADK features.
- Audit the codebase for feature gaps or half-implemented functionality before building further on top of it.

Stories:
- [ ] [GH-284] LiteLLM MCP Integration
- [ ] [GH-285] LiteLLM skills integration
- [ ] [GH-297] Make better use of ADK features
- [ ] [GH-299] Identify any feature gaps or half implemented features

### v0.7 — Workflow & Team Customization Architecture (planned)
Goals
- Make the development workflow pluggable (SCRUM/Kanban/Waterfall) instead of hardcoded.
- Make team staffing and role tooling customizable per tech stack/product.
- Modularize process tooling by topic and move project-specific settings into the product/state repo.

Stories:
- [ ] [GH-289] Abstraction layer for development workflow
- [ ] [GH-290] Customizable Team staffing
- [ ] [GH-291] Role Workbenches
- [ ] [GH-282] Modularize the tooling by topic (Process, Specs, Implementation)
- [ ] [GH-308] The horseless carriage settings are project specific, and should be stored in the project repo
- [ ] [GH-292] Check whether simplifying existing development workflow could improve system performance

### Research backlog (exploratory, not a committed release)
Goals
- Hold spikes and open-ended research that haven't been scoped into a committed release yet.

Stories:
- [ ] [GH-96] Spike: Research custom frontend with CopilotKit
- [ ] [GH-97] Research: Support different spec models (bmad/openspec/speckit)
- [ ] [GH-178] New Epic: Improve Github integration

### Backlog (unplanned, no GitHub issue yet)
- [ ] [EP-0008] Concurrency-Safe State and a Working Parallel Loop
  - Related to [GH-286] (v0.4) but not the same scope — #286 isolates the state machine into its own container; this epic covers making concurrent/parallel sprint execution actually safe.

Note: EP-0007 (Deeper LiteLLM Platform Integration), previously listed here, is now tracked via the GitHub issues under v0.6.

---

## Task board (Kanban)

### v0.1 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| | | | EP-0001, US-0001, US-0002, US-0003, US-0004, US-0005, US-0006, US-0007, EP-0002, US-0008, US-0009, US-0010, EP-0003, US-0011 |

Notes
- Update this table in PRs alongside code changes.
- Keep the board limited to the current sprint scope if you're also running sprints.

### v0.2 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| | | | EP-0004, US-0012–US-0014, EP-0005, US-0015–US-0017, EP-0006, US-0018–US-0020 |

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
- v0.3: GH-501, GH-502, GH-272, GH-295, GH-273, GH-452, GH-316, GH-513, GH-515
- v0.4: GH-503, GH-179, GH-180, GH-300, GH-304, GH-286
- v0.5: GH-453, GH-278, GH-312, GH-188, GH-293, GH-456
- v0.6: GH-284, GH-285, GH-297, GH-299
- v0.7: GH-289, GH-290, GH-291, GH-282, GH-308, GH-292
- Research backlog: GH-96, GH-97, GH-178
- Unplanned: EP-0008

Keep this file updated in the same PRs that move work forward.
