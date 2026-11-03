# Issue

- Issue ID: SPEC-ISSUE-0083
- Title: `advance_story_stage`'s `review_event="APPROVE"` Default Can Never Succeed - Every Role Shares One GitHub Identity, And GitHub Refuses To Let An Account Approve Its Own Pull Request
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-03

## Overview
Investigated `0.1.0-run82` (`gh run view 41452157966`, commit `979dfa557a03979cbbbd1ce646ba4eeabef18191` -
the first run after GH issue #391's fix - "fold `gh_pr_review`/`merge_story_pr` into the stage-advancement
call itself" - merged). `report.md` flagged **Top Problem #1 (severity: high)**: *"Pull request review
workflows repeatedly failed due to GitHub GraphQL self-approval restrictions"*, and the run's own
`Blockers` section named the final, run-halting one directly: *"US-0003 ... advance_story_stage('US-0003',
'Tested') has been rejected 3 times in a row ... most recently: Cannot mark 'US-0003' Tested - posting the
review via gh_pr_review failed: failed to create review: GraphQL: Review Can not approve your own pull
request (addPullRequestReview)"*.

**Confirmed mechanism - a regression introduced by #391's own design, not an agent mistake.**
`advance_story_stage`'s new `review_comment`/`review_event` parameters (GH #391,
`agents/scrum_team/tools/requirements.py:1607`) default `review_event` to `"APPROVE"`
(`requirements.py:1607`, `1633`), and both the Architect/QA workflow prompts and `RELEASE.md`'s own
changelog entry for #391 present `APPROVE` as the expected default (`agents/scrum_team/prompt_modules/
Architect-workflow.md:27`: *"posts the gh_pr_review on the PR as part of this same call (default event:
APPROVE..."*; `QA-workflow.md:33` identically; `RELEASE.md:953`: *"review_event defaults to APPROVE"*).
Architect followed this guidance exactly as designed (`transcript.md:2556-2561`):

```json
{
  "stage": "Reviewed",
  "review_comment": "Architecture review complete. The implementation of US-0001 through US-0006 ...",
  "title_or_id": "US-0001",
  "review_event": "APPROVE"
}
```

— and the fold's own internal `gh_pr_review` call (`requirements.py:1951`/`1992`) then failed with
GitHub's GraphQL API refusing the approval (`transcript.md:2573`): *"Cannot mark 'US-0001' Reviewed
- posting the review via gh_pr_review failed: failed to create review: GraphQL: Review Can not approve
your own pull request (addPullRequestReview)"*.

**Why this can never succeed, structurally - not a one-off flake.** Every role's git commits/PR comments
are attributed by *metadata* only (`GIT_AUTHOR`/`GIT_COMMITTER` for commits, a `**Role:**` text prefix for
comments - `docs/GITHUB-INTEGRATION.md:7-10`, "Role Attribution"). The actual GitHub API identity
authenticating every `gh`/API call - the one that opened the PR via `start_feature_branch`, pushed every
commit to it, and would now try to "Architect-approve" it - is the **same single credential** for every
role (one `GITHUB_TOKEN` or one GitHub App installation token; see `lib_github.py`'s `resolve_token`).
GitHub's `addPullRequestReview` mutation rejects an `APPROVE`/`REQUEST_CHANGES` review from the same
account that authored the PR, regardless of what role the calling tool narrates itself as. This is not
specific to US-0001/US-0003 - `transcript.md` shows the identical failure recurring across the whole run
on US-0001 Reviewed (`:2567`) and Tested (`:3167`), SPEC-ISSUE-0001 Reviewed (`:7216`) and Tested
(`:7415`), US-0002 Reviewed (`:9302`) and Tested (`:9491`, looped 3x into a BLOCKED story at `:10032`),
SPEC-ISSUE-0004 Reviewed (`:14028`), and finally US-0003 Tested (`:16969`) - the blocker that was still
open when the run hit `budget_critical_halt`. Every one of these is the identical error; `COMMENT`-event
reviews (the pre-#391, pre-fold default `gh_pr_review(event="COMMENT", ...)` has always used) never hit
this restriction, since GitHub only blocks the formal `APPROVE`/`REQUEST_CHANGES` review states on a
PR's own author, not a comment-only review.

**Impact.** This doesn't just degrade one metric - it makes the exact atomic one-call pattern #391
introduced actively *worse* than the two-call pattern it replaced whenever a role follows the documented
default: the old separate `gh_pr_review(event="COMMENT", ...)` call always worked; the new folded call,
used as documented, fails every single time in this project's actual (single-shared-identity) GitHub
auth model. `0.1.0-run82`'s own `report.md` scored Team Efficiency 2/5 largely because of this, and the
run never actually recovered once US-0003 hit the 3x-rejection loop-breaker and was marked BLOCKED with
no real resolution path (there is no legitimate way to "resolve" an architecturally-impossible self-
approval requirement).

## Acceptance Criteria
- `advance_story_stage`'s folded `gh_pr_review` call must succeed for a role following the documented
  default `review_event` in this project's standard (single shared GitHub identity) configuration -
  i.e. the default must not route through GitHub's `APPROVE`/`REQUEST_CHANGES` review states, which
  that identity can never apply to a PR it also authored.
- A regression test replaying `0.1.0-run82`'s exact shape: `gh_pr_review` (mocked) returns GitHub's
  real `"Can not approve your own pull request"` GraphQL error for an `APPROVE`-event call -
  `advance_story_stage(..., "Reviewed"/"Tested", review_comment=...)` using the *default* `review_event`
  must still succeed (either because the default itself no longer attempts `APPROVE`, or because the
  fold detects this specific error and transparently retries as a review type that can succeed).
- `review_event="APPROVE"`/`"REQUEST_CHANGES"` explicitly passed by a caller should still be attempted
  as asked (callers may have a real multi-account setup where it would work) - this fix should not
  remove the parameter or silently rewrite an explicit, non-default request without at least surfacing
  what happened.
- Architect/QA workflow prompts (`Architect-workflow.md:27`, `QA-workflow.md:33`) and `RELEASE.md`'s
  #391 entry (`:953`) must no longer tell every role that `APPROVE` is the safe/expected default once
  the actual default changes - whatever the real default and guidance become, the docs must match.

## Notes
- Distinct from GH #391 itself: the atomic fold mechanism (posting the review as part of the same
  `advance_story_stage` call) works exactly as designed - `transcript.md:2552-2558` shows the tool call
  shaped exactly as documented. The defect is specifically the choice of `APPROVE` as the default/
  recommended review event, which is incompatible with this project's actual GitHub identity model.
- The project's own stated design principle (`docs/ARCHITECTURE.md`, "Design Principle: Enforce
  Mandatory Process Mechanically, Not Just by Prompting") argues against relying on prompt-doc wording
  alone to steer away from `APPROVE` - the more robust fix shape is almost certainly mechanical (e.g.
  the fold itself detecting a self-approval rejection and retrying as a review type that can succeed),
  not just changing which value the docs recommend, since a cheap model can still pass
  `review_event="APPROVE"` explicitly regardless of what the prompt says (as DevTeam did even pre-#391,
  calling raw `gh_pr_review(event="APPROVE", ...)` directly at `transcript.md:1585-1601` - also rejected the
  same way, just outside `advance_story_stage`'s own control in that instance).
- `gh_pr_review`'s own long-standing default (`event: str = "COMMENT"`, `agents/scrum_team/tools/
  github.py:1957`) has never hit this restriction - GitHub only blocks the formal `APPROVE`/
  `REQUEST_CHANGES` states on a PR's own author, not a comment-only review. Any fix should most likely
  converge `advance_story_stage`'s own default back toward this already-safe behavior rather than
  inventing a new one.
- `run82`'s target repo (`CI-Till-Krempel/horseless-carriage-eval-todo-app`, eval branch
  `eval/0.1.0-run82/*`) is an evidence artifact of this run, not a real tracked issue in this codebase -
  referenced here only as the data this investigation is based on.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py` (extends `TestAdvanceStoryStageGates`/
  `TestStageAdvancePushesStateToTheCorrectBranch` from GH #391's own tests): mock `gh_pr_review` to
  return `{"status": "error", "message": "...Can not approve your own pull request..."}` for an
  `APPROVE`-event call and `{"status": "ok", ...}` for a `COMMENT`-event call on the same PR - assert
  `advance_story_stage(..., review_comment=..., tool_context=tc)` called with no explicit
  `review_event` (i.e. using whatever the fixed default is) succeeds, posting a review type that
  actually works.
- A second test: `review_event="APPROVE"` passed explicitly still attempts `APPROVE` (not silently
  rewritten) - whatever the chosen fix shape, an explicit non-default request's behavior must be
  covered by a test, not just the default path.
- Full `agents/scrum_team/tests` suite must still pass with no regressions to GH #391's existing
  `review_comment`/auto-merge-on-Tested tests.
