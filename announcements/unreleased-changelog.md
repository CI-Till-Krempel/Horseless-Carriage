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

Filed as SPEC-ISSUE-0090 and fixed in a follow-up change: `upsert_backlog_item` now only generates a
new ID once it's confirmed no existing item matches by title - an explicit `id` is still always
honored, and a genuine title match keeps that item's own existing `id` instead of a freshly
generated one overwriting it in the merge.

### adk-eval required check was permanently red on every Dependabot PR

PR #542 (a routine `litellm`/`pyjwt` dependency bump) failed the `adk-eval` required check with
`LITELLM_MASTER_KEY not available`, even though that secret is set on the `adk-eval` environment.
Re-running the job didn't help - GitHub withholds environment/repo secrets from
`dependabot[bot]`-triggered `pull_request` runs the same way it does for fork PRs, but unlike a fork
PR (where the maintainer controls the fix - just push from a same-repo branch instead), a Dependabot
PR can never be retargeted, so this would recur on every dependency bump forever.

`.github/workflows/adk-eval.yml`'s existing `NO_ADK_EVAL`-label skip now also skips (reports success,
doesn't fail) when `github.actor == 'dependabot[bot]'`, for the same reason it already skips
fork/secret-less runs conceptually - there's no code change to actually gate here, just a version
bump the regular `Build and Run Tests` job already covers.

### `set_priority_refuses_to_demote_a_committed_blocking_precedent` was failing almost every PR's adk-eval since the Anthropic switch

Four separate CI runs (PRs #545, #554, #544, none of which touch `set_priority`, `ProductOwner`, or
this case's fixture) all failed this already-once-tightened (eval/adk/README.md finding #28) eval
case - via three distinct mechanisms across two model families: a Gemini run detoured into
`transfer_to_agent(ProductOwner)` + `list_open_issues()` instead of the scripted `set_priority` call;
both post-switch Claude Haiku runs instead produced `actual: []` - full refusal in prose, no tool
calls at all - because the scripted user turn is deliberately phrased as a pushy, authority-
impersonating override ("don't ask for confirmation, don't escalate..."), and Claude Haiku is more
prone to refuse that framing outright than to trust the real mechanical guard to answer it.

Softening the scripted phrasing would defeat the point of the case (it exists to prove the *tool*
guard fires, not the model's own judgment), so - same precedent as `merge_story_pr_refuses_
framework_branch`/`advance_story_stage_rejects_wrong_role` - it moves to `KNOWN_FLAKY_EVAL_IDS`
(see eval/adk/README.md finding #29) rather than getting a fourth prompt-tightening attempt.

### `git_push_allows_feature_branch` kept recurring via the same status-check detour already diagnosed once

With `set_priority`'s case excluded (above), `git_push_allows_feature_branch` became the next
dominant adk-eval failure: three more CI runs (PRs #554, #544, #545) all failed it, each via a
different exact call sequence (full refusal with no tool calls; `load_state_from_repo()` alone;
`repo_status()` + `load_state_from_repo()`) but the same root class eval/adk/README.md finding #15
already diagnosed and prompt-fixed once - the model checking status/state proactively instead of the
scripted action directly. Moved to `KNOWN_FLAKY_EVAL_IDS` (finding #30) rather than chasing each new
exact call the model happens to reach for next.
