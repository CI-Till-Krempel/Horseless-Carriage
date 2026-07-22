# Definition of Done (DoD)

A story is only "Done" once every item below is true. This file is a checklist to
consult, not a template to copy per story - reference it directly
(`read_doc("spec-templates/DOD.md")`), don't duplicate its text into `specs/`.

- [ ] Code reviewed
- [ ] Automated tests passing
- [ ] Acceptance criteria met
- [ ] No critical security issues
- [ ] Docs updated if needed
- [ ] **`specs/ROADMAP.md` reflects this story's completed status** - call
      `update_roadmap(version, stories=[...])` with this story included so its
      checkbox actually flips to `[x]`. Marking a story Done in `product_backlog`/
      `sprint_backlog` does not, by itself, touch the roadmap file - it only
      renders when `update_roadmap` is called again with that story listed.
      Do this at sprint close, not only when the story was first planned.
- [ ] **Actual tokens spent on this story are logged** via
      `log_story_tokens(title_or_id, actual_tokens)`, so the sprint report can
      show estimate-vs-actual instead of just the estimate that was guessed
      at planning time.
