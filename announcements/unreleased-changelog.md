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

### `_story_readiness_issues` crashed the entire run with an unhandled `AttributeError` when `user_story` was a dict (0.2.0-run89, SPEC-ISSUE-0087)

A real eval run (0.2.0-run89) stopped after **0 of 5** requested sprints: `report.md` showed
`stop_reason: crashed`, `crash_error: AttributeError: 'dict' object has no attribute 'strip'`, with no
traceback captured anywhere. The raw Action log placed the crash immediately after ProductOwner's
third attempt to satisfy the Definition of Ready for US-0001, described in its own preceding text as
"Updating US-0001 with structured `user_story` field for DoR compliance" - after two prior plain-string
attempts were cleanly rejected.

**Confirmed mechanism:** the cheap eval model, after two string-based DoR rejections, reinterpreted
`DOR.md`'s "As a ... I want ... so that ..." guidance as calling for a structured object (e.g.
`{"as_a": ..., "i_want": ..., "so_that": ...}`) instead of a plain string. Nothing rejects this shape:
`upsert_story`/`_coerce_backlog_item_dict` only coerces the top-level `story` argument into *a* dict,
never validating individual field types, and `ScrumState.product_backlog` is a bare `List[Dict]` with
no nested schema. The dict-shaped `user_story` reaches `_story_readiness_issues`
(`agents/scrum_team/tools/requirements.py:449`), which does
`(item.get("user_story") or "").strip()` - since a non-empty dict is truthy, `or ""` never fires, and
`.strip()` on the dict raises `AttributeError`, uncaught anywhere in the call chain, killing the whole
run. The identical `(item.get(...) or "").strip()` pattern is equally unguarded for `title` and
`overview`/`description` two lines above.

Fixed as SPEC-ISSUE-0087: `_story_readiness_issues` now rejects a non-string `title`/`user_story`/
`overview` with a clean, structured error naming the field and type seen, instead of reaching the old
unguarded `.strip()` call. `_update_story_markdown`'s later rendering code is independently defensive
too (coerces to `str` before use), so a non-string value can't crash the markdown-rendering step even
on a Draft-status story that never reaches the readiness gate at all.
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

### `create_release_pr` deadlocked retrying forever on its own dirty `CHANGELOG.md`, burning an entire sprint's token budget (0.2.0-run90, SPEC-ISSUE-0088)

A real eval run (0.2.0-run90) stopped after 2 of 5 sprints with `stop_reason: budget_critical_halt`.
Sprint 2's own sprint report (logged mid-sprint) showed only 2.66M of its 5M token budget used and
"all planned stories completed within budget" - but the sprint then burned nearly 2.9M more tokens
*after* that report, retrying `create_release_pr` in an identical, never-resolving loop (33+ occurrences
of the same git error in the transcript).

**Confirmed mechanism:** `create_release_pr` calls `generate_changelog`, which writes `CHANGELOG.md`
to disk uncommitted. `integrate_open_changes` (scoped to `specs/`/`.hc/` only) never swept it, so when
`_git_push_impl`'s protected-branch path re-checked-out `develop` internally, it hit git's "local
changes would be overwritten by checkout: CHANGELOG.md" every time - a failure mode neither of
`_checkout_with_auto_integrate`'s two recovery tiers covered (tier 1 out of scope; tier 2,
`_untrack_gitignored_dirty_files`, only handles `.gitignore`-matched files, and `CHANGELOG.md` is
deliberately tracked). `ProductOwner` raised a blocker each time; `Architect`'s `resolve_story_blocker`
cleared it on free-text narrative alone (no mechanical recheck exists for this blocker category) -
never touching the real dirty file - so the identical failure recurred on every immediate retry.

Fixed as SPEC-ISSUE-0088: `_OPEN_CHANGES_PATHS` now includes `CHANGELOG.md` alongside `specs`/`.hc` -
safe because `generate_changelog` is its only writer anywhere in this codebase, never DevTeam's
uncommitted source work. A dirty `CHANGELOG.md` is now committed out of the way by
`integrate_open_changes` before any later checkout can ever see it dirty.
