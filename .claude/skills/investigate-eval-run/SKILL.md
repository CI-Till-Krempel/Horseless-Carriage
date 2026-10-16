---
name: investigate-eval-run
description: Investigate a "Team Performance Evaluation" GitHub Actions run (the 5-sprint AI Scrum team eval harness) to pinpoint why the team stalled, thrashed, or underperformed, then write up a root-caused SPEC-ISSUE-NNNN.md in specs/requirements/. Use when asked to look at a team eval run/report, a run number (e.g. "run 61", "team eval run #61", "0.1.0-run61"), or to figure out why an eval's Team Efficiency/Code Quality score dropped. Pulls the run's manifest/transcript/report artifact plus the matching PRs/branches in the separate CI-Till-Krempel/horseless-carriage-eval-todo-app repo, correlates them against this repo's actual tool code, and distinguishes a genuine code-level bug from an agent/prompt behavior issue.
---

# Investigating a Team Performance Evaluation run

This repo's `.github/workflows/eval.yml` ("Team Performance Evaluation") runs a simulated 5-sprint
AI Scrum team (ProductOwner, ScrumMaster, Architect, DevTeam, QA, ScrumOrchestrator) against a
separate, throwaway target repo, `CI-Till-Krempel/horseless-carriage-eval-todo-app`, building the
fixed `eval/scenario/PRODUCT-VISION.md` to-do-list MVP. It exists specifically so regressions or
improvements in the *team's own behavior* surface release over release, not just anecdotally - see
`RELEASE.md`'s "Team performance evaluation" section for the full mechanics (GitFlow branch layout,
auto-merge/auto-approval simplifications, budget/duration guardrails) before digging in; this skill
assumes you've skimmed it.

Every eval run adds one `### <heading>` entry to `RELEASE.md` describing what a real run showed and
what got fixed because of it ("A real eval run (0.1.0-runNN) showed ..."), and (when the finding is a
genuine code-level gap, not just an agent having a bad run) a matching `specs/requirements/SPEC-ISSUE-NNNN.md`
filed via this same investigation process. Skim a few recent ones (e.g. SPEC-ISSUE-0050, SPEC-ISSUE-0051) before
writing a new one - they show the expected evidence density and tone: concrete quotes/line numbers,
not "the agent seemed confused."

## Step 1 - Find the run

```
gh run list --workflow "Team Performance Evaluation" --limit 50 \
  --json databaseId,number,displayTitle,status,conclusion,createdAt,headBranch
```

`number` is the human-facing "run N" (`0.1.0-runN` in the eval repo's branch names / run-id strings);
`databaseId` is what every other `gh run ...`/`gh api .../actions/runs/...` call actually wants. If the
user says "run 61" or "team eval run #61", match on `number`, then use that row's `databaseId` for
everything below.

**Sandbox note:** `gh` needs real network/TLS access this harness's default sandbox blocks (you'll see
a `tls: failed to verify certificate` or similar error without it) - pass `dangerouslyDisableSandbox:
true` on every `gh`/network Bash call in this flow.

```
gh run view <databaseId>                       # jobs, conclusion, annotations, artifact name
gh run view <databaseId> --log > run.log        # full raw step log if you need it (can be large)
```

## Step 2 - Pull the run's own artifact

Every run uploads `eval-report-<run-id>` containing `report.md` (the rendered judge-LLM summary:
per-sprint metrics, KPI trends, Code Quality/Requirements Quality/Team Efficiency scores, "Top
Problems"), `manifest.json` (the full structured run state - every sprint's `product_backlog`/
`sprint_backlog` snapshot, token usage, PR merges, stop reasons), and `transcript.md` (the complete,
uncapped multi-agent conversation log, every tool call and response, across all sprints).

```
gh api repos/<owner>/<repo>/actions/artifacts --jq '.artifacts[] | select(.name=="eval-report-<run-id>")'
gh api repos/<owner>/<repo>/actions/artifacts/<artifactId>/zip > eval-report.zip
unzip eval-report.zip -d run<N>-artifact/
```

Read `report.md` first - it's short and already names the top problems with one piece of evidence
each. Treat it as a **lead, not a conclusion**: it's a judge-LLM's best guess from the final
code/specs state, not a verified root cause. Your job is to go find the actual mechanism in
`transcript.md`/`manifest.json`/the source code.

`manifest.json` is large (hundreds of KB) - don't read it whole. It's valid JSON; query it with
Python/`jq` for exactly what you need. The most useful single query for "did a story's progress
regress between sprints" (this has been the dominant failure class across multiple past runs -
SPEC-ISSUE-0050, SPEC-ISSUE-0052):

```python
import json
d = json.load(open("manifest.json"))
for sp in d["sprints"]:
    pb = sp.get("product_backlog") or []
    item = next((x for x in pb if x.get("id") == "US-000N"), None)
    print(sp.get("sprint_number"), item.get("stages_completed") if item else "MISSING")
```

A story's `stages_completed` should only ever grow (`Draft -> Ready -> Implemented -> Reviewed ->
Tested -> Accepted`, strictly in order, never shrinking) across sprints. If it shrinks or resets
between two consecutive sprint snapshots, that's hard, undeniable proof of a state-loss bug - find the
exact tool call that caused it in `transcript.md` next (see Step 3).

`transcript.md` is organized as `## Raw agent activity log - sprint N` sections, each a numbered
`### Event K - <Agent>` list of tool calls/responses and any free text. Grep it for the specific thing
you're chasing rather than reading linearly - e.g.:

```
grep -n '"status": "error"' transcript.md          # every rejected tool call, across all sprints
grep -n 'init_scrum_state\|force.*true' transcript.md
grep -n 'advance_story_stage\|start_feature_branch' transcript.md
```

When `report.md`'s "Top Problems" names a specific failure mode, grep for the exact error string it
quotes (or the tool name involved) to find every occurrence and its surrounding context, then read
~30-60 lines around each hit with `Read`/`sed -n` to see what the agent did right before and after.

## Step 3 - Correlate with the eval repo's PRs/branches, then the actual source

Every run's branches/PRs live in `CI-Till-Krempel/horseless-carriage-eval-todo-app`
(`gh ... --repo CI-Till-Krempel/horseless-carriage-eval-todo-app`, `dangerouslyDisableSandbox: true`
again), isolated to `eval/<run-id>/main` + `eval/<run-id>/develop` - never that repo's real default
branch. Story feature-branch PRs (`feature/<story-id>-<slug>` into `develop`), each sprint's release
PR (`develop` -> `main`, auto-merged by the harness), the small auto-generated report PR, and a final
"overview PR" (opened against the eval repo's real default branch, deliberately left **open, never
merged** - the single place to review everything the run produced) are all tagged
`[eval-<run-id>] ...` in their titles. `gh pr list --repo ... --search "<run-id>" --state all` finds
them; `gh pr view <num> --repo ... --json title,body,state,mergedAt,comments` to read sign-off/review
comments (Architect/QA/DevTeam) for where a human-review-shaped gate got stuck.

Once you have a candidate mechanism from the transcript (a specific rejected tool call, a stale-state
symptom, a repeated confabulation), **read the actual tool implementation in this repo** -
`agents/scrum_team/tools/*.py` - rather than guessing from the error message alone. Past investigations
(SPEC-ISSUE-0050, SPEC-ISSUE-0051, SPEC-ISSUE-0052) all needed the real source to go from "symptom" to "root cause":
e.g. a guardrail doing exactly what its message says but reading state that got silently clobbered
upstream, or a git helper with no status-checking on its own subprocess calls. Trace the data
backwards: which function last wrote the value that's wrong, under what condition, and whether that
write path has ever been exercised safely before (check `RELEASE.md`/existing `SPEC-ISSUE-*.md` for whether
this exact symptom already has a "Done" fix that this run's mechanism evades).

## Step 4 - Decide: code bug, or agent/prompt behavior?

Not everything is a code fix. Distinguish:
- **Mechanical/data-integrity bug** (state silently lost or corrupted, a guardrail with a logic gap,
  an unhandled subprocess failure) - write it up as a new `specs/requirements/SPEC-ISSUE-NNNN.md` (Step 5).
- **Agent behavior under a working guardrail** (e.g. ScrumMaster repeatedly drafting an
  over-optimistic sprint report that a validation gate correctly rejects, then retrying) - worth
  naming as a secondary finding/retro item, but don't file it as a code "Issue" unless the guardrail
  itself has a real gap (e.g. it's too easy to eventually satisfy without real progress). Several past
  eval reports have flagged this same pattern in different runs; check whether it's already tracked
  before re-filing it.

## Step 5 - Write the ISSUE doc

Find the next free ID (`ls specs/requirements/ | grep -oE 'SPEC-ISSUE-[0-9]+' | sort -u | tail -1`), then
start from `spec-templates/requirements/TEMPLATE-ISSUE.md` - never edit the template in place, create
`specs/requirements/SPEC-ISSUE-NNNN-Short-Slug.md`. Match the density and evidence style of recent issues
(SPEC-ISSUE-0050/0051/0052): every claim backed by a quoted error string, a `transcript.md`/`manifest.json`
line reference, or a source file:line citation - no "the model seemed to struggle" without a pointer to
where you saw it. Fields:
- **Status**: `Ready` if you've confirmed the mechanism (even without writing the fix yet) - matches
  how SPEC-ISSUE-0050/0052 were filed; `Draft` only if you're documenting an effect without a confirmed
  mechanism, expecting a follow-up run/investigation to pin it down.
- **Priority**: `Must` for anything that stalls real delivery (most of these); `Should`/`Could` for
  quality-of-reporting gaps that don't block the team itself.
- **Owner**: the role/agent most responsible for the affected code path (`Architect` for core
  state/git plumbing has been the common case).
- **Overview**: lead with what the maintainer/report noticed, then the confirmed root cause with
  inline evidence, same structure as the examples above.
- **Acceptance Criteria** / **Test Approach**: a concrete, falsifiable fix shape and a test that would
  fail today and pass once fixed - even if you (this investigation) aren't implementing the fix, this
  is what the next engineer picks up and drives through the normal story pipeline
  (`upsert_issue` -> `advance_story_stage`, see `spec-templates/requirements/README.md`).

Do not implement the fix as part of this investigation unless explicitly asked to - this skill is
about pinpointing and documenting, matching how SPEC-ISSUE-0050 itself was "originally filed (Ready, not
yet implemented) ... without a confirmed git-level root cause" before a follow-up pass landed the fix.

## Step 6 - Report back

Summarize for whoever asked, independent of whether you filed an ISSUE doc: what actually happened
(in plain terms - "the team shipped 1 of 6 stories because X"), the root cause with its strongest
single piece of evidence, and where the full write-up lives (the ISSUE file path, or "no code bug
found, see retro notes" if that's what Step 4 concluded).
