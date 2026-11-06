---
name: investigate-sprint
description: Investigate a real sprint of a configured Horseless Carriage project (a production/day-to-day scrum_team run against someone's actual target repo, driven by human chat - not the CI eval harness) to pinpoint why it stalled, thrashed, or underperformed, then write up a root-caused SPEC-ISSUE-NNNN.md in this repo's specs/requirements/ if the cause is a genuine framework bug. Use when asked to look at "sprint N", "the last sprint", "the current sprint" for a named project (or the only/current one if unnamed), or why a sprint's report/retro/KPIs flagged a problem. If no sprint number is given, investigate the most recent one. Distinct from the investigate-eval-run skill, which is for GitHub Actions "Team Performance Evaluation" runs against the throwaway eval repo - use that one instead if the user says "eval run", "run N", or "0.1.0-runN".
---

# Investigating a real project's sprint

Horseless Carriage normally runs as a long-lived agent container chatting with a human, driven
against whatever single real repo ("the target repo", "the project") that installation's
`STATE_REPO_PATH` points at - see `MANUAL.md` §4 "Running a sprint" and `docs/STATE-REPOSITORY.md`
before digging in. **This is a different thing from the "Team Performance Evaluation" GitHub Actions
harness** (`investigate-eval-run` skill) - there is no CI run, no `manifest.json`/`report.md` artifact
bundle, and no throwaway eval repo here. A real sprint's evidence lives inside the target repo itself
(`specs/reports/*.md`, `.hc/state.json` git history) plus this installation's local session logs -
you're investigating a live/historical production run, not a CI job.

## Step 1 - Resolve the project (its target repo)

Multiple Horseless Carriage installations on this machine are tracked in a machine-wide registry,
**outside any git repo**: `~/.horseless-carriage/projects.json` (`project_registry.py`), schema:

```json
{"projects": [{"name": "<target-repo-dirname>", "hc_path": "<HC checkout dir>", "target_repo": "<product repo dir, i.e. STATE_REPO_PATH>"}]}
```

```
python3 -c "import json,pathlib; print(json.dumps(json.load(open(pathlib.Path.home()/'.horseless-carriage'/'projects.json'))['projects'], indent=2))"
```

- If the user named a project, match it against `name` (just the target repo's directory name -
  informational only, not a slug used anywhere else) and take that entry's `hc_path`/`target_repo`.
- If no project was named: if only one is registered, use it; if several are, ask which one rather
  than guessing. If none are registered at all, you're probably already inside the relevant HC
  checkout - treat the current working tree as `hc_path` and read its own `STATE_REPO_PATH`.
- **The registry can drift from reality** (`project_registry.py:80-86` documents a real incident where
  it silently went stale) - always confirm against that `hc_path`'s own `.env`, which is the live
  source of truth:

```
grep -E '^(STATE_REPO_PATH|SESSION_ID)=' <hc_path>/.env
```

  `STATE_REPO_PATH` may be relative - if so it's resolved against `hc_path` itself (how Docker
  Compose/`dashboard.py` do it), i.e. `target_repo = (hc_path / STATE_REPO_PATH).resolve()`. This can
  be a **nested install** (`target_repo` is the product repo, with the HC checkout living inside it)
  or a **standalone checkout** pointed at a separate sandbox/product repo - both are normal, see
  `docs/STATE-REPOSITORY.md`.

From here on, `<target_repo>` means this resolved path - **all the actual sprint evidence
(`specs/reports/`, `.hc/state.json`, git history, PRs) lives there, not in this Horseless-Carriage
checkout.**

## Step 2 - Resolve the sprint

```
python3 -m json.tool <target_repo>/.hc/state.json | head -30     # sprint_number, sprint_goal, sprint_report_pending_release
```

`sprint_number` (`agents/scrum_team/state.py:21`) is bumped exactly once per `start_sprint(goal)` call
(`agents/scrum_team/tools/scrum.py:1023`) and is the **only** place a sprint number is tracked in real
usage - this is a separate, parallel concept from the eval harness's own sprint counter. If the user
gave no sprint number, this live value (or one less, if a sprint is still mid-flight and hasn't closed
yet - check whether `sprint_report_pending_release` is true, or whether a `SPRINT-REPORT-<N>.md` for
it exists yet) is "the last sprint."

**Important:** `specs/reports/SPRINT-REPORT-NNN.md`'s own number is allocated independently, by
scanning existing filenames for the max N (`_next_numbered_artifact_path`,
`agents/scrum_team/tools/budget.py:547`), **not** copied from `sprint_number` directly - the two
usually line up but don't assume it; confirm by opening the candidate report and checking its own
"Sprint N" heading/body.

```
ls <target_repo>/specs/reports/SPRINT-REPORT-*.md
grep -l 'Sprint '"$N"'\b' <target_repo>/specs/reports/SPRINT-REPORT-*.md   # confirm which numbered file is sprint N
```

If the requested sprint never produced a report at all (it stalled before Sprint Review), reconstruct
its state from `.hc/state.json`'s own git history instead - every `save_state_to_repo()` call commits
a local checkpoint (`_checkpoint_state_commit`, `agents/scrum_team/tools/scrum.py:433-447`, message
`"checkpoint: save_state_to_repo"`):

```
git -C <target_repo> log --format='%H %ad %s' --date=short -- .hc/state.json
git -C <target_repo> show <sha>:.hc/state.json | python3 -m json.tool   # walk until sprint_number matches
```

## Step 3 - Pull the sprint's own artifacts

All in `<target_repo>/specs/reports/`, one independently-numbered sequence per artifact type (same
`_next_numbered_artifact_path` helper, `budget.py:541-544`):

- **`SPRINT-REPORT-NNN.md`** (`SPRINT-REPORT-LATEST.md` is a convenience pointer to the newest) -
  written by `create_sprint_report` (`budget.py:1098`): summary, accomplishments, budget/usage, and
  real quality KPIs. Read this first, same caveat as the eval skill: it's the Product Owner's own
  write-up, a **lead, not a verified conclusion** - go confirm the mechanism yourself.
- **`TRANSCRIPT-NNN.md`** / `TRANSCRIPT-LATEST.md` - rendered from in-memory `state.transcript`
  (`_write_conversation_transcript`, `budget.py:692`). **This is a rolling window over the whole run,
  not a guaranteed-complete per-sprint archive** (`TRANSCRIPT_MAX_ENTRIES` trimming in `agent.py`) -
  older entries can fall off the front entirely. For the full, untruncated record, use the session log
  instead (next bullet).
- **The real full transcript**: `<hc_path>/sessions/transcript-<SESSION_ID>.log` on the **host**
  filesystem (bind-mounted from the container's `/app/sessions` - see `docker-compose.yaml`'s
  `./sessions:/app/sessions` mount). `SESSION_ID` defaults to `my-sprint` if unset in `.env` (checked
  in Step 1). This is the one to grep for a specific tool call/error across the *entire* run, sprint
  boundaries and all - same spirit as `transcript.md` in the eval skill, just not capped. There's also
  a parallel `agent-<SESSION_ID>.log` (raw agent/tool-call log, `agent.py:32`) if the rendered
  transcript format isn't enough.
- **`RETRO-NNN.md`** (always written) and **`STEERING-NNN.md`** (only if any
  `propose_steering_change` call succeeded that sprint) - structured retro items / steering proposals
  with owner, rationale, and PR link (`_file_retro_items_as_issues`/steering rendering, `budget.py`
  around line 614-673).
- `.hc/state.json`'s own `impediment_log`, `retro_actions`, and `decision_log` fields are the
  underlying structured data these render from - useful to diff across two sprints' `git show
  <sha>:.hc/state.json` snapshots (Step 2) if you need to see exactly when an entry was added, not
  just its final rendered form.

A story's `stages_completed` in `product_backlog`/`sprint_backlog` (same `Draft -> Ready -> Implemented
-> Reviewed -> Tested -> Accepted` progression as the eval harness) should only ever grow across
sprint snapshots - if two consecutive `.hc/state.json` checkpoints show it shrinking for the same
story ID, that's the same hard state-loss signature the eval skill looks for; chase it the same way
(find the exact tool call in the transcript/session log, then the source).

## Step 4 - Correlate with the target repo's actual PRs/branches

Everything lives in `<target_repo>` itself (get its GitHub remote first: `git -C <target_repo> remote
get-url origin`), not a separate eval repo - **no `[eval-<run-id>]` tagging convention applies here**.
What to look for instead:

- `sprint-backlog/<N>` branch, PR titled `"Sprint Backlog #<N>"` - opened by `create_sprint_backlog_pr`
  (`agents/scrum_team/tools/github.py:938`, branch name built at line 1046).
- `feature/<story_id>-<slug>` branches, one per story - `start_feature_branch`
  (`agents/scrum_team/tools/github.py:712`).
- The sprint's `develop` -> `main` release PR - `create_release_pr`
  (`agents/scrum_team/tools/github.py:1762`); title/body are free text, not auto-numbered by sprint,
  so match it by merge date against the sprint's state-checkpoint timestamps (Step 2) rather than by
  title pattern.

```
gh pr list --repo <owner>/<repo> --search "Sprint Backlog #<N>" --state all
gh pr view <num> --repo <owner>/<repo> --json title,body,state,mergedAt,comments
```

**Sandbox note:** same as the eval skill - `gh` needs real network/TLS access; pass
`dangerouslyDisableSandbox: true` on every `gh` call here.

## Step 5 - Trace into source: framework bug, or this project's own code?

Two separate codebases can be at fault, and they live in different places:

- **The scrum_team framework itself** (guardrails, state machine, git/GitHub tool plumbing) -
  `agents/scrum_team/**` in **this** Horseless-Carriage repo, version-pinned independently of whatever
  commit `<target_repo>` is on. If a guardrail rejected something it shouldn't have, or state got
  silently clobbered, the bug is here.
- **The project's own product code** - inside `<target_repo>` (whatever `DevTeam`/`QA` actually built).
  A failing test, a bad implementation, or a real design disagreement the team correctly flagged is
  not a Horseless Carriage bug at all.

Once you have a candidate mechanism (a rejected tool call, a stale-state symptom, a confabulated
claim), read the actual implementation rather than guessing from an error string alone - trace the
data backwards: which function last wrote the value that's wrong, under what condition, and whether
`RELEASE.md`/`specs/requirements/SPEC-ISSUE-*.md` in this repo already has a "Done" fix for this exact
symptom that this run's mechanism evades.

## Step 6 - Decide: framework bug, agent/process behavior, or project-specific issue?

- **Framework bug** (state silently lost/corrupted, a guardrail with a real logic gap, an unhandled
  subprocess failure in `agents/scrum_team/tools/*.py`) - write it up as a new
  `specs/requirements/SPEC-ISSUE-NNNN.md` in **this** repo (Step 7), the same way a real eval run would
  surface it.
- **Agent/process behavior under a working guardrail** (e.g. an over-optimistic sprint report a
  validation gate correctly rejected and the agent retried, or a human-approval gate doing exactly its
  job) - worth naming as a retro finding, not a code "Issue," unless the guardrail itself has a real
  gap.
- **Project-specific issue** (a bug in `<target_repo>`'s own product code, a scope/requirements
  disagreement) - out of scope for a Horseless Carriage `SPEC-ISSUE`; report it back to whoever asked,
  and note it belongs in `<target_repo>`'s own issue tracker instead, not this one.

## Step 7 - Write the ISSUE doc (framework bugs only)

Filed in **this** repo regardless of which project's sprint surfaced it - same process as
`investigate-eval-run`'s Step 5: find the next free ID (`ls specs/requirements/ | grep -oE
'SPEC-ISSUE-[0-9]+' | sort -u | tail -1`), start from `spec-templates/requirements/TEMPLATE-ISSUE.md`
(never edit the template in place), create `specs/requirements/SPEC-ISSUE-NNNN-Short-Slug.md`. Match
the evidence density of recent issues (e.g. SPEC-ISSUE-0050/0051/0052) - every claim backed by a
quoted error string, a transcript/session-log line, a `.hc/state.json` checkpoint diff, or a source
file:line citation. Same field guidance as the eval skill (Status/Priority/Owner/Overview/Acceptance
Criteria/Test Approach) - note in the Overview which real project/sprint surfaced it, since there's no
run number to anchor the finding to.

Do not implement the fix as part of this investigation unless explicitly asked - this skill is about
pinpointing and documenting.

## Step 8 - Report back

Summarize for whoever asked, independent of whether you filed an ISSUE doc: what actually happened in
plain terms (which project, which sprint, "the team shipped 1 of 4 stories because X"), the root cause
with its strongest single piece of evidence, and where the full write-up lives (the ISSUE file path in
this repo, "see `<target_repo>`'s own issue tracker" if it's a project-specific bug, or "no framework
bug found, see retro notes" if Step 6 concluded agent/process behavior).
