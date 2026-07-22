# Definition of Ready (DoR)

A story shouldn't enter a sprint backlog until every item below is true. This
file is a checklist to consult, not a template to copy per story - reference
it directly (`read_doc("spec-templates/DOR.md")`), don't duplicate its text
into `specs/`.

- [ ] Story has a clear "As a ... I want ... so that ..." statement
- [ ] Acceptance criteria are written as concrete Given/When/Then scenarios,
      not left as template placeholders
- [ ] Dependencies and risks are identified
- [ ] Story is small enough to plausibly finish within the sprint
- [ ] Development Team has estimated it (`plan_sprint_backlog_item`'s
      `estimate` field) - the estimate that will later be compared against
      actual tokens spent (see `spec-templates/DOD.md`)
- [ ] Story is present in `specs/ROADMAP.md` under the version it targets
- [ ] The existing build is green before adding new work on top of it - if the previous story's
      `check_build()` (see `spec-templates/DOD.md`) wasn't run or failed, fix that first rather than
      building further on top of an already-broken dependency set
