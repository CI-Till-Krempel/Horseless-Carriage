# Changelog — Unreleased

Horseless Carriage's own engineering changelog for work merged to `main` since the
`v0.2.0` tag (2026-11-10), not yet cut into a numbered release. See
[RELEASE.md](../RELEASE.md) for the release *process*; see
[0.2.0-changelog.md](0.2.0-changelog.md) for the prior release's own entries. Real
findings from actual eval runs and CI, and the fix each one led to - in the spirit of
[Keep a Changelog](https://keepachangelog.com/).

**When cutting the next release**: rename this file to `<version>-changelog.md`, add
its own "Released YYYY-MM-DD" line (matching `0.2.0-changelog.md`'s header above), and
start a fresh, empty `unreleased-changelog.md` for whatever comes after.

---

### An unvalidated list-valued `rationale` field crashed `_update_story_markdown` and took down the entire run after 1 of 5 sprints (0.2.0-run87, SPEC-ISSUE-0085)

A real eval run (0.2.0-run87) stopped after only 1 of 5 requested sprints: `report.md` showed
`stop_reason: crashed`, `crash_error: TypeError: can only concatenate str (not "list") to str`, with
no traceback captured anywhere to say where. The raw Action log placed the crash immediately after
DevTeam called `plan_sprint_backlog_item` on `SPEC-ISSUE-0001` (itself a prior eval-filed process
Issue), right after ProductOwner had drafted that Issue's content through several retries.

**Confirmed mechanism, reproduced directly:** `_update_story_markdown`
(`agents/scrum_team/tools/requirements.py:1419,1472`) builds its "Notes" section from
`item.get("value_hypothesis", "") or item.get("rationale", "")` and then concatenates it straight
into the output string, with no type coercion - unlike `acceptance_criteria` a few lines above, which
already guards against exactly this. Passing `rationale` as a list of strings (a natural shape for an
LLM free-text field to drift into) makes `notes` a list, and the next `str + notes` concatenation
raises the exact observed `TypeError`, reproduced standalone outside the eval harness. The mirror bug
exists for `test_approach` (`requirements.py:1442-1444`) the other way around (`list += str`). Because
every caller (`plan_sprint_backlog_item`, `upsert_issue`, `upsert_story`, `advance_story_stage`) funnels
through this one function with no surrounding try/except around the content-building step, the
exception propagates uncaught all the way up through the ADK session and crashes the whole eval
process, not just that one tool call.

Filed as SPEC-ISSUE-0085 (Ready, not yet implemented) - the fix is to coerce `value_hypothesis`/
`rationale`/`test_approach` to `str` the same defensive way `acceptance_criteria` already is, so a
list-shaped free-text field renders as readable text instead of crashing the run.

### The follow-up run's own stop-reason headline falsely blamed a "critical token/USD budget halt" for a harness nudge-ceiling stall (0.2.0-run88, SPEC-ISSUE-0086)

The very next run after SPEC-ISSUE-0085 was filed (0.2.0-run88) got further - 2 of 5 sprints - and
did not hit that crash again. It still stopped early, though, and `report.md`'s headline read: *"a
sprint hit a critical token/USD budget halt with no clean close-out afterward"*. False:
`manifest.json`'s sprint 2 entry shows `critical_halt: false` and `stop_reason:
"max_nudges_exhausted"` - no budget wall was ever hit. Sprint 2 simply ran out of this harness's own
fixed per-sprint retry ceiling before Scrum Master's close-out sequence (report done, release PR
never opened) finished.

**Confirmed mechanism:** `_sprint_should_abort_run` (`agents/scrum_team/scripts/run_eval.py:438-504`)
already knows the difference internally (`if sprint_result["critical_halt"]: ... else: ...`), but its
caller (`run_eval.py:1140-1148`) ignores which branch fired and unconditionally hardcodes
`manifest["stop_reason"] = "budget_critical_halt"` whenever it returns `True` - which
`run_eval_analysis.py` then maps straight to the misleading "token/USD budget halt" sentence. This is
the same diagnostic-wording defect SPEC-ISSUE-0077 already fixed once, in a sibling call site
(the in-sprint fallback report's own wording) - this outer, run-level label was never updated the
same way.

Fixed by threading the existing `critical_halt` distinction through to the run-level `stop_reason`/
report headline, the same way SPEC-ISSUE-0077 already did one level down: a new
`_abort_run_stop_reason` helper (`agents/scrum_team/scripts/run_eval.py`) returns
`"budget_critical_halt"` only when a real budget halt was notified, else `"sprint_closeout_stalled"` -
and `run_eval_analysis.py`'s explanation for the new reason makes no mention of budget/cost. See
SPEC-ISSUE-0086 for the full mechanism and fix. Also noted as a secondary, not-yet-actionable
observation: this run's Sprint 1 wrote the full CRUD implementation for all 6 planned user stories in
one PR it only ever tracked as `US-0001`, leaving `US-0002`-`US-0006` fully working in the shipped
code but permanently stuck at `Ready` in the backlog - flagged for whoever investigates the next run
where it recurs, since no code-level guardrail can currently detect "this diff also satisfies another
story's acceptance criteria" without real semantic analysis.
