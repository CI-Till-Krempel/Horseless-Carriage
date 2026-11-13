# Changelog — Unreleased

Horseless Carriage's own engineering changelog for work merged to `main` since the
`v0.2.1` tag (2026-11-12), not yet cut into a numbered release. See
[RELEASE.md](../RELEASE.md) for the release *process*; see
[0.2.1-changelog.md](0.2.1-changelog.md) for the prior release's own entries. Real
findings from actual eval runs and CI, and the fix each one led to - in the spirit of
[Keep a Changelog](https://keepachangelog.com/).

**When cutting the next release**: rename this file to `<version>-changelog.md`, add
its own "Released YYYY-MM-DD" line (matching `0.2.1-changelog.md`'s header above), and
start a fresh, empty `unreleased-changelog.md` for whatever comes after.

---

### Every retro-filed Issue jumped the queue as "Must", even when it wasn't a blocker (0.2.1-run94, SPEC-ISSUE-0091)

A real eval run (0.2.1-run94) scored Team Efficiency 1/5, trapped in "recursive retro actions...
and endless PR reviews" for 3 of 5 sprints. Direct evidence: the moment Sprint 2 tried to publish,
`create_sprint_backlog_pr` refused outright because a routine retro-filed Issue (a "security scan
findings need triage" advisory, not a real blocker) had been auto-filed at "Must" priority and
mechanically parked at the front of the one-story-at-a-time queue. Product Owner's only way forward
was a manual override: `plan_backlog_item(priority="Should", ..., priority_justification="Demoting
from Must to Should as it is an advisory retro finding/issue rather than a blocker...")`. Every
sprint that files a routine finding forced this same reactive fight just to get back to shipping
stories.

**Confirmed mechanism:** `_file_retro_items_as_issues` (`agents/scrum_team/tools/budget.py:799-852`)
auto-assigned "Must" + front-of-queue placement to every `category="technical"` retro/impediment
finding, unconditionally - with no way to distinguish "this blocks the rest of this sprint's work
right now" from "this is a routine process/tooling gap worth tracking but not urgent." The data to
make that distinction already existed (`add_retro_action`/`add_impediment` already take a
`priority="normal"|"high"` argument) but was silently discarded: neither function ever stored it on
the logged entry, so by the time the retro converts to an Issue (often a different sprint entirely,
at `create_sprint_report` time) the signal was already gone.

Filed as SPEC-ISSUE-0091 and fixed in this change: `add_retro_action`/`add_impediment` now persist
`priority` on the stored entry, and `_file_retro_items_as_issues` only auto-prioritizes "Must" +
jumps the queue when the entry was flagged `priority="high"` (a genuine blocker). The default
`priority="normal"` still files a real, trackable Issue, just at "Should" - so it sorts behind this
sprint's already-planned "Must" stories instead of displacing them, with no further intervention
needed from Product Owner.
