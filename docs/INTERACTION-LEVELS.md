# Human Interaction Levels

The Scrum team is designed to run with a human in the loop, but *how much* of the loop a human
actually sits in is configurable - a solo founder acting as their own Product Owner needs a very
different level of involvement than a CEO who only cares about spend, or a CI job running an
unattended evaluation. `INTERACTION_LEVEL` (see `.env.example`) selects one of four levels. It's read
fresh from the environment wherever it matters (`agents/scrum_team/helpers.py`'s
`get_interaction_level()`) - there's no separate state field to fall out of sync with what's actually
configured for the running process.

```
INTERACTION_LEVEL="Product"        # default if unset or unrecognized
```

Valid values (case-insensitive): `Product`, `Stakeholder`, `CEO`, `EVAL`. An unset or unrecognized
value falls back to `Product` - the most-supervised level - rather than silently disabling every
human-approval gate on a typo.

## The four levels

| Level | The human's role | What they decide |
|---|---|---|
| **Product** | Stands in for the Product Owner day-to-day. | Product decisions, answers developer clarifying questions, sets priorities. |
| **Stakeholder** | A business stakeholder, not embedded in day-to-day PO work. | Business needs, release order, approves new features, gives sprint-review feedback. |
| **CEO** | Budget holder only. | Approves the sprint's token/USD budget; otherwise reads the sprint report as a management summary. |
| **EVAL** | None - fully unattended. | Nothing; a fixed number of sprints run without any human review at all (see `agents/scrum_team/scripts/run_eval.py`). |

## What's actually mechanically enforced

Three tool-level gates already exist to stop the team from skipping human review entirely (see
`specs/requirements/ISSUE-0001-Human-Review-Gates-Are-Not-Mechanically-Enforced.md`):

- `advance_story_stage(title_or_id, "Ready")` - refuses unless the story's design was cleared via
  `record_design_approval(title_or_id, note)` (GH issue #94), if this level requires it.
- `advance_story_stage(title_or_id, "Implemented")` - refuses unless a fresh approval was recorded
  for this sprint via `record_human_approval(approval_type, note)`.
- `create_release_pr(...)` - refuses unless a fresh approval was recorded for this increment.

`INTERACTION_LEVEL` controls *which* `approval_type` (if any) each of these gates actually
requires - see `requires_pre_ready_design_approval` / `_PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL` /
`_PRE_RELEASE_APPROVAL_BY_LEVEL` in `agents/scrum_team/helpers.py`:

| Level | Before Ready (`advance_story_stage(..., "Ready")`) | Before implementing (`advance_story_stage(..., "Implemented")`) | Before releasing (`create_release_pr`) |
|---|---|---|---|
| Product | not required | `record_human_approval("sprint", ...)` required | `record_human_approval("release", ...)` required |
| Stakeholder | `record_design_approval(title_or_id, ...)` required, per story | `record_human_approval("sprint", ...)` required | `record_human_approval("release", ...)` required |
| CEO | not required | `record_human_approval("budget", ...)` required | none - the team releases on its own judgment |
| EVAL | not required | none | none |

The Ready-stage gate is deliberately per-story (a flag set directly on that one story), not the
shared "one approval unlocks everything for the rest of the sprint" pattern the other two gates
use - a stakeholder reviewing one story's mockup doesn't stand in for having reviewed a different
story's design.

Both gates use the same "must be *new* since last time" pattern already used for
`retro_baseline`/`sprint_report_pending_release`: one approval can't be replayed to silently unblock
every future sprint or release. A rejected call's own error message always names the exact
`approval_type` and tool call needed - the calling agent doesn't need to know the configured level in
advance to recover from a rejection.

Product and Stakeholder require the *same* two mechanical approvals (`sprint` + `release`) - the
difference between them is in *what's actually discussed* with the human before those calls are
made (task-level priorities and dev questions vs. business/feature/release-order decisions), which is
a matter of conversation content the prompts guide, not something a tool call can verify.

CEO trades the `sprint`/`release` approvals for a single `budget` approval: the human isn't expected
to review each sprint's backlog or each release individually, only to approve what the team is
allowed to spend. `create_sprint_report`'s generated report (the "management summary") is the CEO's
primary visibility into what happened - every report is stamped with the active interaction level so
it's traceable which mode produced it.

EVAL requires no human approval at all, mechanically as well as in practice: `run_eval.py` sets
`INTERACTION_LEVEL=EVAL` for every evaluation run (see its `_configure_env`), since a fixed-length,
unattended run has no human in the loop to review anything - `_merge_open_prs` auto-merging release
PRs is the harness's own stand-in for human release review, documented in that script's module
docstring. EVAL uses the same `full` report detail as Product (see below) - the report is analyzed
by tooling afterwards, not read by a human, so there's nothing to gain from trimming it.

## Report detail tiers

`INTERACTION_LEVEL` also controls how much detail `create_sprint_report` (`agents/scrum_team/tools/
budget.py`) actually renders into the report a human reads - via `report_detail_level()` in
`agents/scrum_team/helpers.py`. This is mechanical, not prompt-guided: the same report-generation
code branches on the level, so a human at a given level always gets the same shape of report
regardless of how the PO agent happened to phrase its call.

| Detail tier | Levels | Renders |
|---|---|---|
| `full` | Product, EVAL | Everything: per-agent token usage, retrospective actions, impediments, story-level estimates, full conversation transcript with per-agent excerpts. |
| `business` | Stakeholder | Everything except per-agent token usage and transcript excerpts - keeps retro/impediments/estimates (delivery-process content a business stakeholder cares about) and a bare transcript location pointer (no excerpt dump). |
| `executive` | CEO | Budget and usage, and the sprint-length/budget recommendation, only - retro, impediments, story estimates, and the transcript are all omitted from the rendered report. |

None of the underlying data is ever deleted or skipped by this - `retro_actions`, `impediment_log`,
`story_estimates`, and `transcript` are always written in full regardless of level (the mechanical
gates in the previous section, e.g. the retrospective having to be *new* each sprint, apply
unconditionally). Only what this one report *renders* for the human changes. When a tier omits a
section, the report says so explicitly under a "Full Process Detail" heading, naming what was left
out and where it still lives - `retro_actions`/`impediment_log`/`story_estimates` in `.hc/state.json`,
the conversation transcript in `specs/reports/TRANSCRIPT-LATEST.md` (a human-readable Markdown file,
not a raw blob in `.hc/state.json` - see `RELEASE.md` "State persistence") - a silently thinner report
would look like nothing happened, rather than like a level-appropriate summary.

## What's still prompt-only, deliberately

`INTERACTION_LEVEL` does not change *what questions get asked* of the human in conversation, only
*whether an approval call is mechanically required* before certain tools proceed, and *how much
detail `create_sprint_report` renders* (both described above). Getting a PO agent to actually ask
task-level questions at the Product level vs. business/feature questions at the Stakeholder level -
or to keep a CEO-level response to one or two sentences instead of a full backlog rundown - is
conversational judgment (see ORCHESTRATOR_PROMPT's INTERACTION-LEVEL DETAIL, PO_PROMPT), not
something a mechanical gate can verify the way `check_build()` can check "did the build pass". If a
real run surfaces the team ignoring this distinction as a repeatable problem, file it as an Issue
(`upsert_issue`, `specs/requirements/`) the same way every other prompt-only gap in this project has
been - see [ARCHITECTURE.md](ARCHITECTURE.md) "Design Principle: Enforce Mandatory Process
Mechanically, Not Just by Prompting" section.

At Stakeholder and CEO levels, the orchestrator should run the story pipeline continuously between
the two points where the human actually needs to be involved (approving the sprint goal/backlog, and
the sprint review at the end), rather than stopping for a user-facing reply after every internal
hand-off (see `specs/requirements/ISSUE-0016-Sprint-Stays-Interactive-Instead-Of-Autonomous-At-
Stakeholder-And-CEO-Levels.md`). This - *how often* the orchestrator stops to address the human, as
opposed to *what it says* when it does (covered above) - is its own named section in
`ORCHESTRATOR_PROMPT` (AUTONOMY BY INTERACTION LEVEL), deliberately prompt-only: "did the team stop
and chat unnecessarily" isn't something a mechanical gate can check.
