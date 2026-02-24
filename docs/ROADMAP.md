# Product Roadmap

Use this living roadmap to plan releases and track user stories across states. It doubles as a lightweight task board and a release planning tool.

## How to use
- Story IDs should match files in `docs/stories/` (e.g., `ST-001` corresponds to `docs/stories/ST-001-some-title.md`).
- Move story references between states as work progresses.
- Keep titles short; full details live in the story file.
- For each planned version, list goals and the set of stories targeted for that release.
- When a release is cut, freeze the section by adding the actual tag (e.g., `v0.1.0`) and dates.

Legend
- `[ST-###] Title` → a user story reference and its short title
- Checkbox states: `- [ ]` To Do, `- [~]` In Progress (use `- [~]` to signal WIP), `- [R]` In Review, `- [x]` Done

Tip: If you prefer standard checkboxes only, use the Kanban tables below and keep raw lists unchecked.

---

## Release plan (versions → stories)

### v0.1 — MVP (target: YYYY-MM)
Goals
- Short goal statement for the release

Stories
- [ ] [ST-001] Your first story title
- [ ] [ST-002] Your second story title
- [ ] [ST-003] Another story title

### v0.2 — Next iteration (target: YYYY-MM)
Goals
- Short goal statement for the release

Stories
- [ ] [ST-010] Some enhancement
- [ ] [ST-011] Another enhancement

### Backlog (unplanned)
- [ ] [ST-100] Future idea
- [ ] [ST-101] Another idea

---

## Task board (Kanban)

Use either the per-version boards below or one global board; duplicate as needed for each active version.

### v0.1 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| [ST-001] Your first story title | [ST-002] Your second story title |  |  |
| [ST-003] Another story title |  |  |  |

Notes
- Update this table in PRs alongside code changes.
- Keep the board limited to the current sprint scope if you’re also running sprints.

### v0.2 Kanban

| To Do | In Progress | In Review | Done |
|------|-------------|-----------|------|
| [ST-010] Some enhancement |  |  |  |
| [ST-011] Another enhancement |  |  |  |

---

## Cross-cutting initiatives (optional)
Track broader themes/epics that span multiple versions. Link constituent stories.

- Initiative: Developer Experience
  - Stories: [ST-020], [ST-021]
- Initiative: Reliability
  - Stories: [ST-030], [ST-031]

---

## Release checklist (for when cutting a release)
- [ ] All included stories are in `Done` and meet Definition of Done
- [ ] Docs updated (stories, PRD/SRS, ADRs as needed)
- [ ] Version/tag created (e.g., `v0.1.0`) and changelog drafted
- [ ] Known issues captured and follow-ups added to backlog

---

## Index of story references
Group story references by planned version for quick scanning.

- v0.1
  - [ST-001] Your first story title — docs/stories/ST-001-your-first-story.md
  - [ST-002] Your second story title — docs/stories/ST-002-your-second-story.md
  - [ST-003] Another story title — docs/stories/ST-003-another-story.md
- v0.2
  - [ST-010] Some enhancement — docs/stories/ST-010-some-enhancement.md
  - [ST-011] Another enhancement — docs/stories/ST-011-another-enhancement.md
- Unplanned
  - [ST-100] Future idea — docs/stories/ST-100-future-idea.md
  - [ST-101] Another idea — docs/stories/ST-101-another-idea.md

Replace placeholders with your actual story IDs and titles. Keep this file updated in the same PRs that move work forward.