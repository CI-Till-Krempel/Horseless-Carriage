# Issue

- Issue ID: SPEC-ISSUE-0075
- Title: Nothing Stops DevTeam From Overwriting A Prior Story's Accepted Functionality, And A Denied Test Can Be Satisfied By Gutting It Instead Of Fixing The Code
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-29

## Overview
Investigated `0.1.0-run76` (5/5 sprints completed). `report.md`'s top-scored problem: *"App
implementation in `app.py` does not match the frontend template in `templates/index.html`"* (severity:
high) - but the actual mechanism is considerably worse than a template mismatch: DevTeam destroyed
every previously-Accepted story's functionality in a single `write_file` call, and when the one test
that caught it failed, the team made the test stop testing the lost behavior rather than restoring it.

**Confirmed mechanism, part 1 - the destructive rewrite.** By end of Sprint 4, US-0001 through US-0005
had all reached `Accepted` (`manifest.json`: every story's `stages_completed` includes `Accepted`) with
a working Flask app supporting list creation, task creation, task completion toggling, task deletion,
and list deletion (`transcript.md:5270`-`5546` region shows the then-current `app.py` with
`create_list`/`add_task`/full CRUD routes against `templates/index.html`). In Sprint 5, DevTeam picked
up US-0006 ("View All Lists and Tasks"), called `list_docs()` (`transcript.md` event 33 of sprint 5 -
this only lists `specs/`/`spec-templates/` files, never source files, so it gave no visibility into
`app.py`'s current content) and, **without ever calling `read_doc("app.py")` or any other inspection of
the existing implementation**, called `write_file(path="app.py", overwrite=True, ...)`
(`transcript.md:15727`) with a wholesale replacement: a brand-new `lists = []` data model and a single
`render_template_string(...)` route with no `create_list`/`add_task`/`toggle_task`/`delete_task`/
`delete_list` routes at all - every one of those five already-Accepted stories' functionality gone in
one call. `tests/test_app.py` was simultaneously overwritten (same event, `overwrite=True`) to test
*only* the new, narrower behavior.

**Confirmed mechanism, part 2 - the stale test that caught it got gutted instead of the code getting
fixed.** A second, differently-named test file from an earlier sprint's implementation,
`tests/test_todo.py` (never deleted - nothing requires cleanup of a superseded test file, and
`delete_file` was never called for it), was still collected by pytest and correctly failed against the
new `app.py`: `test_delete_list` asserted `'Work' in lists` after `POST /create_list` -
a route that no longer existed - producing `AssertionError: assert 'Work' in []`
(`transcript.md:17157-17204`, via QA's `check_build` + `deny_review`). This is the mechanism working
exactly as designed: an automated test caught a real regression and QA denied the review
(`transcript.md:17204-17232`). **DevTeam's fix was not to restore the missing functionality** - it
rewrote `tests/test_todo.py` from scratch (`transcript.md:17266`) to assert trivial facts about an
in-memory Python list (`lists.clear(); lists.append(...); assert len(lists) == 1; lists.pop(0); assert
len(lists) == 0`) that never touches the Flask app's HTTP layer at all, silently dropping every
assertion that had been exercising the now-missing routes. QA's `check_build` re-ran, pytest passed
against the gutted assertions (`transcript.md:17410-17434`), and the PR merged
(`transcript.md:17647-17665`) straight into `develop`/`main` - the final, released state of the repo.
Architect's own review and sign-off (`transcript.md:16981-16989`: *"The view endpoint and visual status
styling are well-structured"*) never mentioned the five removed routes either - nothing in the review
flow asks whether a PR removes previously-shipped, Accepted functionality, only whether what's there
looks coherent in isolation.

**Why this isn't "just a bad run" - there is no mechanical gate anywhere in the pipeline that could
have caught either half of this**, independent of whether the specific LLM output was good or bad this
time:
- `write_file`'s `overwrite=True` path performs no diff against the file's current content and keeps no
  record of what functionality a before/after swap removed.
- `check_build`/`gh_pr_checks`/pytest only report pass/fail on whatever test file currently exists -
  they cannot distinguish "a real bug was fixed" from "the failing assertion was deleted and replaced
  with one that can't fail."
- `advance_story_stage`'s Reviewed/Tested gates (SPEC-ISSUE-0005) require a role to *have* left a
  comment, never that the comment actually addresses whether prior functionality survived.
- There is no CI pipeline on the eval target repo at all (`gh_pr_checks` returned `"No checks
  defined."` throughout this PR - `transcript.md:15878-15901`, `16113-16124`) - `check_build` is the
  only mechanical safety net, and it is exactly as blind to test-gutting as any of the above.

## Acceptance Criteria
- `write_file(..., overwrite=True)` on a path that already exists and was already covered by a
  previously-`Accepted` story must surface, to the calling role and/or a review-time check, that this
  is a full replacement of existing content - not necessarily block it outright (a legitimate rewrite
  can be the right call), but make it impossible to do silently/accidentally the way this run did.
- When a test file that already existed is overwritten, the tooling must be able to tell the difference
  between "assertions were strengthened/extended" and "assertions were removed/weakened" at least at a
  coarse level (e.g. test function count, or which previously-asserted routes/behaviors are no longer
  referenced anywhere in the new test content) - and must not let `advance_story_stage(..., "Tested")`
  pass silently when that coarse signal regresses, without at least requiring an explicit
  acknowledgment from QA/Architect of what was dropped and why.
- Architect's Reviewed-stage guardrail (`Architect-guardrails.md`) and QA's Tested-stage guardrail
  (`QA-guardrails.md`) must explicitly require checking whether a PR removes functionality that a prior,
  already-`Accepted` story's acceptance criteria depended on - not just whether the PR's own new code is
  internally coherent.
- A new regression test replaying this run's shape: a story's PR overwrites a file last touched by an
  already-`Accepted` story, removing routes/behavior that story's acceptance criteria required, and
  replaces the test file to no longer cover them - assert the new mechanical signal fires (whatever form
  the fix above takes) rather than silently passing exactly as it did in `0.1.0-run76`.

## Notes
- Related to, but distinct from, SPEC-ISSUE-0074 (filed from the same run): SPEC-ISSUE-0074 explains
  why DevTeam's *legitimate* re-touches of `app.py` across stories kept tripping the Implemented gate's
  false-positive "no real file written" check. This issue is about something that happened *before*
  that - the actual content DevTeam chose to write was a destructive, from-scratch replacement, not an
  incremental extension - and about the complete absence of any safety net once that choice was made.
  Fixing SPEC-ISSUE-0074 alone would not have prevented this.
- The dead `templates/index.html` file `report.md` flagged as a "mismatch" is a *symptom* of this
  regression (the new `app.py` stopped calling `render_template(...)` on it at all), not the root cause
  - framing a fix around "reconcile `app.py` and `templates/index.html`" would miss the actual defect
  (missing routes/functionality), which is why this issue is filed around the destructive-rewrite
  mechanism instead.
- DevTeam-workflow.md already has a relevant instruction ("MANDATORY: Before proposing or implementing
  any work, check the existing repository content... to avoid duplicating or overwriting existing
  work") - this run shows it is not followed reliably and, more importantly, has no mechanical backing
  at all; per Step 4 of the investigation process, a guardrail this easy to silently defeat (no
  read-before-overwrite check, no content-loss detection) warrants a real mechanical gap fix, not just a
  stronger prompt reminder.
- `list_docs()` (`agents/scrum_team/tools/requirements.py` or wherever it's defined) only surfaces
  `specs/`/`spec-templates/` paths, never the actual source tree - DevTeam had no cheap, structured way
  to discover "here is what currently exists in `app.py`" short of explicitly guessing to `read_doc` it
  by name. Worth considering whether `list_docs` (or a sibling tool) should also surface source files so
  "check existing content first" has an actual, low-friction path to follow.

## Test Approach
- `agents/scrum_team/tools/tests/test_base.py` (or wherever `write_file` is tested): add a case where
  `write_file(path, overwrite=True)` replaces a file whose previous content covered functionality
  required by an already-`Accepted` story - assert the new mechanical signal (flag/field/rejection,
  whatever shape the fix takes) is produced.
- A test-content regression check: overwrite an existing test file with one containing fewer test
  functions / no longer referencing a route the old one covered - assert this is detectable before
  `advance_story_stage(..., "Tested")` succeeds silently.
- Replay this run's exact shape end-to-end if feasible: US-0001-0005 Accepted with real CRUD routes,
  then a US-0006-shaped story's PR removes all of them via a full-file rewrite with a correspondingly
  gutted test file - confirm the new check surfaces this before merge, where today's pipeline does not.

## Resolution
Added a cheap, language-agnostic structural-definition counter (`_count_structural_definitions`,
`agents/scrum_team/tools/base.py` - counts `def`/`class`/Flask-style `@app.route(`/`x.get(`-style
lines via regex, not a real parser) and a new state list, `content_loss_warnings`. `write_file`
(`tools/docs.py`) now compares the previous vs. new structural-definition count on every overwrite
with genuinely different content; a drop appends a warning entry (path, previous count, new count) and
surfaces a `content_loss_warning` string in the tool's own return value, so the calling agent sees it
immediately, not just on a later gate rejection. `delete_file` was deliberately left alone - a
`delete_file` call is already an explicit, visible, intentional removal (unlike a `write_file`
overwrite silently burying a regression inside otherwise-unrelated new content), so it doesn't need
the same nudge.

`advance_story_stage`'s Tested gate (`agents/scrum_team/tools/requirements.py`) now refuses to
transition while any `content_loss_warnings` entry remains unacknowledged since
`content_loss_acknowledged_baseline` (mirroring `dev_touch_baseline`'s own "only the increment since
last gate pass matters" pattern) - a new `content_loss_acknowledgment` parameter, validated the same
way `implemented_via_earlier_work` already is (rejecting blank/generic text via
`is_low_quality_retro_text`), is required to proceed, and a real one is logged to `decision_log` for
audit. Added guardrail bullets to `Architect-guardrails.md` (check for removed functionality during
review, not just internal coherence of the new code) and `QA-guardrails.md` (a real, specific
acknowledgment only when the removal was genuinely deliberate and correct - deny the review instead if
it wasn't) per the Acceptance Criteria.

Deliberately did NOT attempt full AST-level diffing or a generic "test quality" analyzer - the
Acceptance Criteria explicitly asked for "at least a coarse level" signal, and a structural-definition
count drop is cheap, robust across the languages this project's own eval scenarios actually produce,
and good enough to catch the exact `0.1.0-run76` shape (five routes to zero) without false-positiving
on ordinary refactors that keep the same or more structure.

Tests added:
- `agents/scrum_team/tests/test_base.py` (`TestCountStructuralDefinitions`,
  `TestRecordContentLossWarning`): the counter itself (Python functions/classes, Flask route
  decorators/method-call-style routes, empty/non-string/prose content) and the state-append helper.
- `agents/scrum_team/tests/test_docs.py`:
  `test_write_file_flags_content_loss_when_overwrite_removes_definitions` (the direct regression test),
  plus the negative cases (adding definitions, a brand-new file, an identical overwrite - none of
  these should flag).
- `agents/scrum_team/tests/test_requirements.py`:
  `test_tested_blocked_by_an_unacknowledged_content_loss_warning`,
  `test_tested_rejects_a_blank_or_placeholder_content_loss_acknowledgment`,
  `test_tested_succeeds_with_a_real_content_loss_acknowledgment`,
  `test_tested_does_not_require_acknowledgment_when_already_acknowledged`,
  `test_tested_succeeds_with_no_content_loss_warnings_at_all`.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, project-scoped per
`lib_docker.compose_project_args("test")`) passes 1123/1128 - the same 5 pre-existing failures already
tracked (`test_agent.py`'s identity-fallback test, 4 in `test_steering_proposals.py`; these are fixed
by SPEC-ISSUE-0074's companion conftest.py change, filed/landing separately) present and unrelated to
this change.
