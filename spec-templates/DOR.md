# Definition of Ready (DoR)

A story shouldn't be marked **READY** (see "Story workflow" in RELEASE.md and
`advance_story_stage` in `agents/scrum_team/tools/requirements.py`) until every item below is
true. This file is a checklist to consult, not a template to copy per story - reference it
directly (`read_doc("spec-templates/DOR.md")`), don't duplicate its text into `specs/`.

READY is Product Owner's stage gate (supported by Architect for technical feasibility). It is the
second of the 6 mandatory stages every story passes through in strict order - DRAFT, READY,
IMPLEMENTED, REVIEWED, TESTED, ACCEPTED - via `advance_story_stage(title_or_id, stage)`. That tool
mechanically rejects marking a story READY if the checks below aren't met (missing/placeholder
title, blank or still-templated user story, no acceptance criteria) - this checklist is what to fix
if it does.

- [ ] Story reached DRAFT first (GH issue #94) - the concept/mockup was actually shaped, not skipped
      straight to READY with placeholder content
- [ ] At the Stakeholder interaction level: the design has been cleared via
      `record_design_approval(title_or_id, note)` - a per-story approval, not a shared sprint-wide
      one (see `requires_pre_ready_design_approval` in `agents/scrum_team/helpers.py`); not required
      at Product/CEO/EVAL

- [ ] Story has a clear "As a ... I want ... so that ..." statement - not left blank, not still the
      template placeholder
- [ ] Acceptance criteria are written as concrete Given/When/Then scenarios, not left as template
      placeholders
- [ ] Dependencies and risks are identified
- [ ] Story is small enough to plausibly finish within the sprint
- [ ] Development Team has estimated it (`plan_sprint_backlog_item`'s
      `estimate` field) - the estimate that will later be compared against
      actual tokens spent (see `spec-templates/DOD.md`)
- [ ] Story is present in `specs/ROADMAP.md` under the version it targets (`plan_backlog_item`) -
      `advance_story_stage` keeps its checkboxes current automatically once it starts moving
      through stages
- [ ] The existing build is green before adding new work on top of it - if the previous story's
      `check_build()` (see `spec-templates/DOD.md`) wasn't run or failed, fix that first rather than
      building further on top of an already-broken dependency set
- [ ] The story immediately above this one in `product_backlog` priority order has already reached
      ACCEPTED - stories are worked one at a time, top to bottom; `advance_story_stage` enforces
      this and rejects the call otherwise
