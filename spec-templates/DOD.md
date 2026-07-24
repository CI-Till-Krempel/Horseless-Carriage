# Definition of Done (DoD)

A story is only truly done once it has reached **ACCEPTED** - the last of the 5 mandatory stages
every story passes through in strict order (READY, IMPLEMENTED, REVIEWED, TESTED, ACCEPTED - see
"Story workflow" in RELEASE.md), each completed via `advance_story_stage(title_or_id, stage)`
(`agents/scrum_team/tools/requirements.py`), each owned by exactly one role, no skipping. This file
is a checklist to consult, not a template to copy per story - reference it directly
(`read_doc("spec-templates/DOD.md")`), don't duplicate its text into `specs/`.

- [ ] **IMPLEMENTED (Dev Team)**: Code reviewed-ready, automated tests written and passing, actual
      tokens spent logged via `log_story_tokens(title_or_id, actual_tokens)` (so the sprint report
      can show estimate-vs-actual, not just the estimate guessed at planning time), then
      `advance_story_stage(title_or_id, "Implemented")`.
- [ ] **REVIEWED (Architect)**: Architectural/technical review of the implementation is complete -
      no critical security issues, no unaddressed cross-cutting concerns - then
      `advance_story_stage(title_or_id, "Reviewed")`.
- [ ] **TESTED (QA)**: `check_build()` reports `passing: true` - it attempts a real install of the
      project's declared dependencies (`requirements.txt`/`package.json`); a nonexistent pinned
      package version or similar breakage fails this immediately. This exact failure mode (a pinned
      `SQLAlchemy` version that doesn't exist) shipped as "Done" in a real eval run before this
      check existed - a story whose build doesn't install is not Done no matter how complete the
      code otherwise looks. Then `advance_story_stage(title_or_id, "Tested")`.
- [ ] **ACCEPTED (Product Owner)**: Acceptance criteria are genuinely verified met, docs updated if
      needed, then `advance_story_stage(title_or_id, "Accepted")` - which also flips this story's
      checkboxes in `specs/ROADMAP.md` automatically, for every stage, not just this last one.

Marking a stage complete any other way (e.g. setting `status` directly via `upsert_story`/
`plan_sprint_backlog_item`) is refused in code - it must go through `advance_story_stage`, which is
what actually enforces this order and per-stage ownership.
