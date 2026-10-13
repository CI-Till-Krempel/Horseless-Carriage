# Reports

Durable records of sprint-level process artifacts, alongside the sprint reports themselves
(`specs/reports/SPRINT-REPORT-NNN.md`/`TRANSCRIPT-NNN.md`).

Guidelines
- These aren't written by hand - `create_sprint_report`/`render_fallback_sprint_report`
  (`agents/scrum_team/tools/budget.py`) render them automatically alongside the sprint report
  itself, reusing the same numbered-path-reuse-within-a-sprint pattern.
- `RETRO-NNN.md` is written every sprint a report is produced - the full, durable record of every
  retro action/impediment logged so far (category, status, resolution), not just a terse one-line
  summary.
- `STEERING-NNN.md` is written only when at least one `propose_steering_change` call has happened by
  then - no empty file otherwise.

Templates
- TEMPLATE-RETRO-ITEM.md — Structure for a single retro action or impediment entry.
- TEMPLATE-STEERING-PROPOSAL.md — Structure for a single steering proposal entry.
