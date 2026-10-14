# Issue

- Issue ID: ISSUE-0059
- Title: `gh_pr_create`'s Silent Default-To-Main Let DevTeam's Feature-Branch PR Bypass `develop` Entirely, Corrupting GitFlow For The Whole Run
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Follow-up to ISSUE-0058, which left "why did Sprint 1's `create_release_pr` see no commits between
`main` and `develop`" as an open question. Root cause, now confirmed: US-0001's own feature-branch PR
never targeted `develop` at all - it merged **directly into `main`**, via a path that completely
bypasses GitFlow, with no error or warning anywhere.

**Confirmed mechanism.** `start_feature_branch` (`agents/scrum_team/tools/github.py:698`) is the
correct, designed-for-this path - its own docstring says it "opens the per-story feature branch + PR
into develop" and it does, internally, always pass `base=develop` (`agents/scrum_team/tools/
github.py:` `_develop_branch_name`-derived value). In `0.1.0-run65`, DevTeam called it **four times**
for US-0001 and every single attempt failed for a different, legitimate reason:
- `transcript.md:1978-1985`: *"Cannot start work on 'US-0001' - it hasn't been planned into this
  sprint yet."*
- `transcript.md:2654-2661`: *"Cannot start work on 'US-0001' - Architect, DevTeam, QA have not yet
  left a gh_pr_comment/gh_pr_review on this sprint's Sprint Backlog PR."*

After the fourth rejection, DevTeam never retried `start_feature_branch` again - instead it ran its
own `git_push` directly (`transcript.md:2796-2878`, pushing `eval-0.1.0-run65/feature/US-0001-create-
todo-list` with no PR) and then called `gh_pr_create` - a general-purpose tool also directly in
DevTeam's own toolset (`agents/scrum_team/agent.py:2756`) - **without ever passing a `base`
parameter**, twice (`transcript.md:2884-2891`, `2917-2925`). `gh_pr_create`'s own signature
(`agents/scrum_team/tools/github.py:661-692`) silently fills that gap:

```python
base = base or _default_push_branch(tool_context)
```

`_default_push_branch` resolves to **main** (or this eval run's isolated main branch) - there is
nothing in `gh_pr_create` that recognizes "this `head` is a story feature branch, it must target
`develop`, not whatever the generic default happens to be." The first call's own failed `cmd` already
shows it: `"--base", "eval/0.1.0-run65/main"` (`transcript.md:2905-2906`) - that attempt failed only on
an unrelated `gh` CLI usage error (missing `--body`); the *second* call fixed that and succeeded,
silently opening and later merging PR #540 ("US-0001: Create To-Do List") straight into `main`
(confirmed via `gh pr list --repo CI-Till-Krempel/horseless-carriage-eval-todo-app --search run65`:
`{"number": 540, "baseRefName": "main", "headRefName": "eval-0.1.0-run65/feature/US-0001-create-
todo-list", "mergedAt": "2026-10-14T09:57:51Z"}`).

From that point on, `develop` never received Sprint 1's work at all - it sat at the original seed
commit for the rest of the run. `create_release_pr` (develop -> main) later correctly found "No
commits between them" (ISSUE-0058's trigger) because there genuinely were none - the real commits had
already gone around it. This is not merely a reporting inconvenience: it silently defeats the entire
GitFlow structure (story PR review on `develop`, release PR as the actual promotion gate to `main`)
the instant any role falls back to the lower-level tool after the higher-level one rejects it enough
times.

## Acceptance Criteria
- `gh_pr_create` (or a thin wrapper every PR-creating path actually goes through) must never let a
  story feature-branch PR default to anything other than `develop` - either by refusing to default
  `base` at all when `head` matches an entry in `active_feature_branches`/looks like a `feature/*`
  branch (require the caller to pass it explicitly, or force it to the resolved `develop` branch
  mechanically), or by `gh_pr_create` itself recognizing that shape and substituting the correct base
  regardless of what the caller passed or omitted.
- A role falling back to `gh_pr_create` after `start_feature_branch` repeatedly rejects it (for
  whatever reason) must not be able to silently route around GitFlow - the correct outcome is either
  the fallback still lands on `develop` safely, or the attempt is refused with a message pointing back
  at `start_feature_branch`/whatever prerequisite is actually missing, never a silent success against
  the wrong base.
- A new regression test: `gh_pr_create` called with a `head` that is a known active feature branch and
  no `base` argument must resolve to `develop` (the run's configured develop branch), not
  `_default_push_branch`'s value - and, separately, a test confirming non-feature-branch callers
  (release PRs, sprint backlog PRs) are unaffected and keep defaulting to the main/push branch exactly
  as today.
- Consider (not required, but worth the next engineer's judgment call): should DevTeam's toolset even
  include the unrestricted `gh_pr_create`, given `start_feature_branch` already exists specifically to
  make this safe? If the answer is "yes, it's needed as a real escape hatch" (plausible - see Notes),
  the fix must live in `gh_pr_create` itself, not in restricting access to it.

## Notes
- This is the confirmed upstream trigger for ISSUE-0058's entire chain in `0.1.0-run65` - fixing this
  issue would likely have prevented that run from ever reaching the unrecoverable release-PR state in
  the first place. They remain separately worth fixing: other causes of a failed/absent release PR
  (e.g. `0.1.0-run64`'s stale-tag failure, unrelated to this bug) would still need ISSUE-0058's fix.
- Worth asking, separately from this specific bug: why did `start_feature_branch` fail 4 times in a
  row for the *same* story in the *same* sprint before DevTeam gave up on it? The first two failures
  (unplanned into sprint, sprint-backlog-engagement gate) are themselves legitimate, working gates -
  DevTeam correctly satisfied the first by calling `plan_sprint_backlog_item`, but the transcript shows
  no corresponding fix attempt for the second (the "Architect, DevTeam, QA have not yet left a
  gh_pr_comment/gh_pr_review" gate) before DevTeam abandoned the tool entirely - a secondary,
  prompt-level finding (DevTeam didn't ask Architect/QA to leave that sign-off, or request it via
  `transfer_to_agent`, before reaching for the unsafe fallback) worth a retro note, not necessarily a
  second code fix, since the *mechanical* damage here is fully explained by `gh_pr_create`'s own unsafe
  default regardless of why DevTeam reached for it.
- Gap lives in `gh_pr_create` (`agents/scrum_team/tools/github.py:661-692`) and its interaction with
  `_default_push_branch`/`_develop_branch_name` (`agents/scrum_team/tools/base.py:64-101`) - the two
  helpers already exist and already resolve correctly; `gh_pr_create` simply never consults the right
  one based on what it's actually being asked to create a PR for.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: new cases for `gh_pr_create` - a call with `head` matching
  `active_feature_branches` and no `base` resolves to the develop branch; the same call with `head` not
  matching any known feature branch and no `base` keeps today's behavior (defaults to
  `_default_push_branch`).
- A regression test reproducing `0.1.0-run65`'s exact shape: `start_feature_branch` rejected (any
  reason), then a direct `gh_pr_create(head=<that story's branch>)` with no `base` - assert the
  resulting PR targets `develop`, not `main`.
- Full `agents/scrum_team/tests` suite re-run to confirm no existing test relying on `gh_pr_create`'s
  current default-to-main behavior (release PRs, sprint backlog PRs, steering proposals) breaks.
