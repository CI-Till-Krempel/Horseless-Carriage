# Architecture Decision Record (ADR)

- ADR-ID: ADR-0001
- Title: Release and Implementation Plan artifact conventions
- Status: Accepted
- Date: 2026-07-16
- Owners: Scrum Team (tooling)

## Context
While planning the work to close six sprint-end artifact gaps (transcript capture, real KPIs, release-PR enforcement, changelog, customer announcement, product docs), we needed to represent two things that don't have an existing artifact type in `spec-templates/`:

1. A "release" — a specific version being shipped, with its scope, target date, and cut-over checklist.
2. An "implementation plan" — the concrete technical execution plan for an Epic, as opposed to the rationale for a design decision (which ADRs already cover) or the requirement itself (which Epics/User Stories already cover).

We needed a convention for both before authoring the actual epics, stories, and roadmap for those six gaps.

## Decision
- **Releases**: do not introduce a new `specs/releases/` artifact type. `spec-templates/ROADMAP.md` already has per-version sections (`### v0.1 — Name (target: YYYY-MM)`) with Goals/Stories subsections, plus a dedicated "Release checklist" section, and explicit instructions to freeze a section with the actual tag and dates once a release is cut. `specs/ROADMAP.md` is the single source of truth for release planning.
- **Implementation Plans**: introduce a new template type, `spec-templates/implementation-plans/` (`README.md` + `TEMPLATE-IMPLEMENTATION-PLAN.md`), following the same house style as the other template directories (AGENT SAFEGUARD blueprint comment, flat bullet metadata, numbered sections). ID scheme: `IP-NNNN`, 4-digit zero-padded, sequential, mirroring the `ADR-NNNN` scheme. One Implementation Plan per Epic, stored under `specs/implementation-plans/`.

## Options Considered
- **Option A — New `specs/releases/RELEASE-{version}.md` type**: rejected. Would duplicate version metadata (goals, target date, story list) already captured in `ROADMAP.md`, creating two documents that can drift out of sync. Violates this project's own rule of "one artifact per file, small and focused... link liberally" by splitting one concern (a release) across two files.
- **Option B — Reuse `ROADMAP.md` for releases (chosen)**: no duplication; the roadmap template already anticipates this ("When a release is cut, freeze the section by adding the actual tag... and dates").
- **Option C — Model Implementation Plans as ADRs**: rejected. An ADR records *why* a decision was made (context, options, consequences) at a point in time; an Implementation Plan records *how* an epic will be executed (steps, affected files, testing, rollout) and is expected to be updated as work proceeds. Conflating the two would make ADRs mutable, which contradicts their purpose as a historical decision record.
- **Option D — Informal implementation-plan docs with no reusable template (chosen: rejected)**: would leave future authors without a consistent structure, unlike every other artifact type in this framework.

## Consequences
- Positive: no redundant release-tracking file; a new, consistently structured place to capture technical execution plans without overloading ADRs or Epics.
- Negative: the tooling in `agents/scrum_team/tools/docs.py` does not yet have an ID generator or upsert helper for `IP-` IDs (mirroring `_generate_next_adr_id`/`upsert_adr`) — this is planned as part of EP-0001's sibling work but is not implemented yet; IDs in this initial batch were assigned by hand.
- Follow-ups: if multiple releases per roadmap version become common (e.g. hotfixes/patch releases), revisit this decision via a new ADR rather than retrofitting `ROADMAP.md`.

## References
- `spec-templates/ROADMAP.md`
- `spec-templates/implementation-plans/TEMPLATE-IMPLEMENTATION-PLAN.md`
- EP-0001 through EP-0006 (`specs/stories/`)
