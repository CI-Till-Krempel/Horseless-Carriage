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

### Every release audit silently renamed the same security-scan Issue's ID, orphaning its old spec file each time (0.2.1-run94, SPEC-ISSUE-0090)

A real eval run (0.2.1-run94) scored Team Efficiency 1/5: five sprints, only two stories delivered,
and the judge's Top Problems flagged "redundant and duplicate security scan issue files" -
`SPEC-ISSUE-0001`, `SPEC-ISSUE-0003`, `SPEC-ISSUE-0006`, `SPEC-ISSUE-0007` all titled identically
("Release audit: security scan findings need triage"). The judge's suggested fix (add a dedup check)
was aimed at the wrong place - a title-match dedup already exists and ran correctly every time.

**Confirmed mechanism:** `run_release_audit` deliberately calls `upsert_issue` with no `id`, relying
on title-match dedup to update the same Issue each release instead of filing a new one. But
`upsert_backlog_item` (`agents/scrum_team/tools/requirements.py:958-960`) generates and writes a
*fresh* ID into the item **before** checking for a title match, not after. When the title match is
then found, the merge (`requirements.py:1014`, `{**x, **item}`) lets that fresh (wrong) ID overwrite
the existing item's (correct) one in place - silently renaming the same logical Issue every release
(`SPEC-ISSUE-0001` -> `0003` -> `0005` -> `0006` -> `0007` across the run's five sprints). Each rename
makes `_update_story_markdown` write a new `specs/requirements/<new-id>-...md` file; nothing deletes
the old one. `transcript.md` confirms both `SPEC-ISSUE-0001-...md` and `SPEC-ISSUE-0003-...md`
sitting in `specs/requirements/` side by side by sprint 3, byte-identical titles.

Filed as SPEC-ISSUE-0090 (Ready, not yet implemented) - the fix is to only generate a new ID for
`upsert_backlog_item` once no existing item matches by title, never unconditionally up front.
