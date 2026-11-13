# User Story

- Story ID: US-0028
- Title: Close The Loop On Reported Issues - Plan It Or Explicitly Decline It, Never Silence
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-12

## As a maintainer, I want a filed Issue-type backlog item to either get planned into a sprint or get an explicit, justified "Won't" decision, so GH #295's "issues are reported but not fixed, or marked as Won't" silent-failure mode can no longer happen unnoticed.

## Overview
GH issue #295 reports that many reported issues never get fixed, and are not explicitly declined
either - they just go quiet. This repo already shipped four closely-related fixes from investigating
the eval harness and real sprints:

- SPEC-ISSUE-0076 (`_move_item_to_front_of_remaining_work`): a retro-filed, auto-"Must"-prioritized
  Issue now actually jumps the one-story-at-a-time ordering queue instead of landing behind
  already-queued same-priority work.
- SPEC-ISSUE-0078 (`supersedes_issue_ids`): an Issue resolved via a `propose_steering_change` instead
  of real implementation is reconciled, not left orphaned and still blocking.
- SPEC-ISSUE-0081 (`recurs_since_sprint`): a retro Issue that's just a restatement of the team's own
  unfinished Must-priority stories no longer jumps the queue ahead of those very stories, breaking a
  livelock.
- SPEC-ISSUE-0084 (`list_open_issues`, `duplicate_of`/`confirmed_distinct`, the displacement cap):
  differently-worded self-referential retro Issues can no longer evade SPEC-ISSUE-0081's guard and
  starve the real backlog indefinitely.

**All four are scoped to the auto-filed, auto-"Must"-prioritized retro path**
(`_file_retro_items_as_issues`, `agents/scrum_team/tools/budget.py`) - which deliberately does
*not* auto-prioritize at the "Product" interaction level (a real, human-driven sprint), leaving
priority for the human Product Owner to set via their own grooming (per that function's own
docstring, and confirmed by the issue's own maintainer investigation comments). Two concrete gaps
remained, found by tracing what happens to an Issue filed any other way:

1. **No accountability for marking an Issue "Won't".** `set_priority`'s SPEC-ISSUE-0082 justification
   requirement (demoting a real story away from "Must") was explicitly scoped to stories only - an
   Issue could be silently set to `"Won't"` with zero justification or `decision_log` entry, the exact
   unaccountable half of #295's title (confirmed by an existing test,
   `test_demoting_an_issue_is_unaffected`, whose own docstring said so).
2. **No backstop for an Issue that's simply never groomed.** `create_sprint_backlog_pr`'s
   `unaddressed_must_issues` check only ever catches an Issue sitting at `"Must"` priority. An Issue
   filed at any other priority (the default outcome at "Product" level, or after a legitimate
   reprioritization) that never reaches `"Ready"` and never gets an explicit `"Won't"` falls through
   every existing gate and can sit at `Draft` forever - nothing noticed.

## Acceptance Criteria
- Given an Issue-type backlog item, when `set_priority(..., "Won't")` is called with no justification
  (or only a placeholder), then the call is refused the same way demoting a story away from `"Must"`
  already is - a real justification is required and logged to `decision_log`.
- Given an Issue just named as the blocking precedent in an ordering-gate rejection this sprint, when
  it is then set to `"Won't"`, then the call is refused outright, same as the existing story guard.
- Given an open, non-`"Must"`, non-`"Won't"` Issue that has sat `STALE_ISSUE_SPRINT_THRESHOLD`+
  sprints (default 2, configurable) since it was filed without ever reaching `"Ready"`, when
  `create_sprint_backlog_pr` is called, then it is refused, naming the stale Issue(s) and the two
  explicit resolutions (`advance_story_stage(..., "Ready")` or `set_priority(..., "Won't",
  justification=...)`).
- Given an Issue already at `"Must"` priority, `blocked`, `superseded_by_steering`, already at
  `"Ready"`/`"Accepted"`, already explicitly `"Won't"`, or with no `filed_sprint` recorded (legacy
  data), then the new stale-issue gate does not flag it - each of those is already covered by an
  existing, narrower mechanism, or has no honest age to measure.
- Given `list_open_issues`, when called, then each item also reports `filed_sprint` and
  `sprints_open`, so Product Owner's own periodic grooming can get ahead of the mechanical gate.

## Notes
- Deliberately does **not** build a GitHub-issue-import tool (pulling real `gh issue list` items from
  a target repo into `product_backlog`) - the issue's own maintainer investigation comments explicitly
  scoped that out as a separate concern (tracked near GH #272, "sync GitHub Issues and versions to
  roadmap"), since it's a materially different mechanism (external GitHub API sync vs. this repo's own
  `product_backlog` state) that would need its own design, not a drive-by addition here.
- `filed_sprint` is stamped once, only at real creation (`upsert_backlog_item`), and is deliberately
  not backfilled onto pre-existing items with no recorded value - there's no honest age to assign them,
  and guessing one risks a false "stale" flag the first time this check runs against real history.
- Follows this codebase's established "hard mechanical gate where possible, advisory nudge where the
  input can legitimately keep changing" pattern: the `"Won't"`-requires-justification rule and the
  stale-issue publish gate are both hard, code-enforced refusals; `list_open_issues`'s `sprints_open`
  field and the new `ProductOwner-workflow.md` grooming-habit note are advisory, since exactly when an
  Issue is "worth planning now" is a real judgment call, not something to force mechanically.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: `set_priority` refuses an Issue set to `"Won't"`
  with no/placeholder justification, succeeds and logs to `decision_log` with a real one, and still
  refuses outright if the Issue was just named as a same-sprint blocking precedent;
  `upsert_backlog_item` stamps `filed_sprint` on a newly-created Issue, does not reset it on a later
  update, and does not stamp it on a Story; `list_open_issues` surfaces `filed_sprint`/`sprints_open`.
- `agents/scrum_team/tests/test_helpers.py`: unit tests for `stale_issue_sprint_threshold` (default,
  configurable, invalid-value fallback) and `stale_ungroomed_issues` (flags a genuinely stale,
  undecided Issue; does not flag one explicitly `"Won't"`, one already `"Ready"`/`"Accepted"`, one at
  `"Must"` priority, one `blocked`, one `superseded_by_steering`, one with no `filed_sprint`, or any
  Story regardless of age; respects the configurable threshold).
- `agents/scrum_team/tests/test_github.py`: `create_sprint_backlog_pr` refuses while a non-`"Must"`
  Issue sits ungroomed past the threshold, and proceeds once it's either planned to `"Ready"` or
  explicitly set to `"Won't"`, once it's still within the threshold, or if it has no `filed_sprint`
  recorded.
- Full suite (`python3 run_tests.py` - host-script tests plus the Docker-based
  `agents/scrum_team/tests` suite) passes with no regressions.
