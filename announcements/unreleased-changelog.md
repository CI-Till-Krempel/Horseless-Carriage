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

### ADK web UI never came up after the google-adk 2.15.0 bump - blocked on a telemetry prompt (SPEC-ISSUE-0095)

After Dependabot's `google-adk` `2.4.0` -> `2.15.0` bump (#549), every installation's agent
container reported `Up`, but the ADK web UI was unreachable. The agent log ended at
`Help improve the ADK (CLI and Web UI) by allowing Google to collect pseudonymized usage data?`.

**Confirmed mechanism:** ADK 2.15's CLI asks `input("Enable telemetry? [Y/n]: ")` before running
any subcommand whenever stdin is a TTY and `~/.adk/config.json` has no recorded answer. The `agent`
service runs with `tty: true` (needed for CLI mode), and a fresh container never has that file. In
web/daemon mode nobody is attached, so `adk web` blocked forever before binding port 8000.

`agent.Dockerfile` now runs `adk telemetry disable` at build time, right after installing
dependencies, so no `adk` subcommand in any stage ever prompts. Opting out is deliberate: the
prompt's own default is "yes". Verified with a PTY-driven `adk web` in a throwaway container: it
stops at the prompt without the opt-out and reaches `Uvicorn running` with it. A new
`tests/test_agent_dockerfile.py` case guards the line.

### A sprint stuck on a budget halt had no live recovery path - only .env + a container restart (SPEC-ISSUE-0092)

A real production run got stuck: the agent container answered every turn with
`🚫 [TOKEN BUDGET EXCEEDED]`, and nothing typed into the running session could unstick it. The
halt's own message named the only documented recovery - raise `SPRINT_TOKEN_BUDGET` in `.env` and
restart the agent container - a heavyweight round-trip for "let this one sprint keep going right
now."

**Confirmed mechanism:** `check_cost_budget_callback` is a `BeforeModelCallback` - it synthesizes its
halt response and returns *before* the LLM is ever invoked, so no chat message or tool call from a
human can reach a live model turn to resolve it; the existing `resolve_blocking_interaction`
human-in-the-loop tool is exactly as unreachable as everything else once the halt trips.
`sync_budgets_from_env` also re-syncs `state.budgets.total`/`total_usd` from `.env` unconditionally
on *every* turn (a deliberate anti-staleness fix from a prior incident), so even a direct write to
the live session's budget would be silently undone the next turn.

Added `python3 run.py override-budget --tokens N [--usd N] --reason "..."`: execs
`agents/scrum_team/scripts/override_budget.py` inside the already-running agent container to bump
`budgets.total`/`total_usd` directly on the live ADK session, no restart needed.
`sync_budgets_from_env` now checks a `budget_human_override` marker and skips re-syncing whichever
key(s) a human has pinned, so the override isn't immediately clobbered - and
`sprint_budget_reset_state_delta` clears that marker at the next sprint's start, so it's scoped to
the sprint it was applied during and can never mask a later, real `.env` fix. The override is
audited: it appends a `budget_override` entry to `blocking_interactions` with the operator's given
reason. See [docs/BUDGET.md](../docs/BUDGET.md) "Recovering From A Stuck Budget Halt".

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
