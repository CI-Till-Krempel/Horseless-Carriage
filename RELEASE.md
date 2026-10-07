# Release Process

This document describes the release process for Horseless Carriage itself (this
repo/tool). It does **not** change how Horseless Carriage releases the *target*
products it manages for users (`create_release_pr`, `gh_release_create`, the upcoming
EP-0004 changelog work) — those stay as-is. See [Scope](#scope) below.

## Scope

Two distinct things both use the word "release" in this codebase; this document only
covers the first one:

1. **Releasing Horseless Carriage itself** — tagging a version of *this* repo
   (`CI-Till-Krempel/Horseless-Carriage`) so users know what they're running. This is
   what this document sets up.
2. **Releasing a target product** that the Scrum agents manage on a user's behalf —
   already exists via `create_release_pr()` / `gh_release_create()` in
   `agents/scrum_team/tools/github.py`, and will grow a changelog step per
   `specs/implementation-plans/IP-0004-Changelog-Generation.md`. Not touched here.

The one place these connect: we record *which version of Horseless Carriage* was used
to run a sprint inside the target repo's own state, so a sprint report is traceable
back to the tool version that produced it (see
[Tracking the HC version in the state repo](#tracking-the-hc-version-in-the-state-repo)).

## Versioning

[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`):
- **MAJOR** — breaking change to agent tool contracts, `ScrumState` shape, or CLI/env
  interface that requires user action.
- **MINOR** — new capability, backward-compatible (new tool, new story/epic delivered).
- **PATCH** — bug fix, no behavior/contract change.

The roadmap (`specs/ROADMAP.md`) already plans work in `v0.1`, `v0.2`, ... waves. Those
become the actual git tags/releases: **`v0.1.0` is our first release**, cut from `main`
once EP-0001–EP-0003 (all currently Done) are tagged.

Note: pre-1.0, per SemVer, `0.x.y` is explicitly "anything may change at any time" —
MINOR bumps can include breaking changes until we cut `v1.0.0`. Flagging so it's a
conscious choice, not an oversight.

## Branching model — GitFlow

- **`main`** — always reflects the latest released version. Every commit on `main` is
  tagged. Protected: no direct pushes, only merges from `release/*` or `hotfix/*`.
- **`develop`** — integration branch for the next release. Default base branch for new
  work, **starting after `v0.1.0` ships** (see [Rollout](#rollout)) — until then,
  feature branches keep targeting `main` exactly as today.
- **`feature/*`** — as already used (`feature/us-NNNN-<slug>`), branched from `develop`,
  PR'd back into `develop` (once `develop` exists — see Rollout).
- **`release/vX.Y.Z`** — cut from `develop` when preparing a release. Only version-bump
  / release-note fixups happen here, no new features. Merged into `main` (tagged) *and*
  back into `develop`.
- **`hotfix/vX.Y.Z`** — cut from `main` for an urgent fix that can't wait for the next
  `develop` cycle. Merged into `main` (tagged) *and* `develop`.

```
main      ──●────────────●────────────●──   (tags: v0.1.0, v0.1.1, v0.2.0)
             \          / \          /
release/*     ●──●──●──●   ●──●──●──●
             /              \
develop   ──●────●────●────●─●────●────●──
             \    \    \        /
feature/*     ●────●    ●──────●
```

## Release procedure

1. When `develop` has everything intended for the release, cut `release/vX.Y.Z` from
   `develop`.
2. Bump `VERSION` (see [Where the version lives](#where-the-version-lives)), update
   `CHANGELOG.md` if `EP-0004` has landed by then, open a PR `release/vX.Y.Z → main`.
3. On merge to `main`, tag the merge commit `vX.Y.Z` and push the tag.
4. Pushing the tag triggers the GitHub Action below, which publishes the GitHub
   Release. No manual `gh release create` needed.
5. Merge `release/vX.Y.Z` back into `develop` (or fast-forward `develop` to `main` if
   nothing diverged) so the tag's history isn't lost on the next cycle.

## GitHub Action

New workflow, `.github/workflows/release.yml`, minimal since there's no build/deploy
artifact to publish:

```yaml
name: Release

on:
  push:
    tags:
      - "v*.*.*"

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install host-script test dependencies
        run: pip install pytest pyyaml PyJWT requests
      - name: Run tests
        run: python3 run_tests.py
      - name: Publish GitHub Release
        run: gh release create "${{ github.ref_name }}" --generate-notes
        env:
          GH_TOKEN: ${{ github.token }}
```

- Trigger: any tag matching `v*.*.*` pushed (e.g. from step 3 above).
- Re-runs the test suite before publishing — belt-and-suspenders, since `main` should
  already be green from the `release/*` PR's CI run, but a tag can in principle be
  pushed independent of a PR merge.
- `--generate-notes` uses GitHub's auto-generated release notes (commits/PRs since the
  last tag) — no changelog-authoring effort required. Can be swapped for a real
  `CHANGELOG.md` excerpt once EP-0004 exists.
- No Docker image build/push, no deployment step — matches "no deployments, minimal
  effort".

## Tracking the HC version in the state repo

A new field on `ScrumState` (`agents/scrum_team/state.py`), distinct from the existing
`version` field (which is a *state-schema* version, frozen at `"1.0.0"` since day one
and unrelated to this):

```python
hc_version: str = "unknown"
```

- Added to `REPO_STATE_KEYS` (`agents/scrum_team/tools/scrum.py`) so it's persisted to
  the target repo's `.hc/state.json` alongside everything else.
- Set on every `init_scrum_state()` call to the *currently running* version (not
  `setdefault` — it should always reflect the version that actually ran this session,
  overwriting whatever was previously persisted).
- Surfaced in `create_sprint_report()` (`agents/scrum_team/tools/budget.py`): a line
  under the report title, `**Generated by Horseless Carriage vX.Y.Z**`.

### Where the version lives

**Committed `VERSION` file at repo root**, plain text (`0.1.0`), read at runtime.
Bumped as part of the `release/*` PR (step 2 above). Simple, human-readable, no git
dependency at runtime, works identically in and out of Docker — the container's final
stage doesn't need `.git` copied in, and there's no build-arg plumbing to maintain.

## Migration scaffold

No breaking `ScrumState` changes exist yet, but the hook point is built now so the
*next* one doesn't have to invent this from scratch. New file
`agents/scrum_team/tools/migrations.py`:

```python
# Each entry: the version whose *shape* the migration fixes up TO.
# Applied in order for any state whose recorded hc_version is older.
MIGRATIONS: list[tuple[str, Callable[[dict], dict]]] = [
    # ("0.2.0", _migrate_to_0_2_0),
]

def migrate_state(state: dict, from_version: str) -> dict:
    """Applies any migrations newer than from_version, in order."""
    for target_version, fn in MIGRATIONS:
        if _version_lt(from_version, target_version):
            state = fn(state)
    return state
```

Wired into `load_state_from_repo()` (`agents/scrum_team/tools/scrum.py`): after reading
`.hc/state.json`, compare its recorded `hc_version` against the current one and run
`migrate_state` before merging into `tool_context.state`. `MIGRATIONS` starts empty, so
this is a no-op today — pure scaffolding, deliberately not solving a migration that
doesn't exist yet.

## Rollout

`v0.1.0` ships first, from current `main`, with **no** workflow change — `develop`
doesn't exist yet, so this release is cut directly on `main`. Once `v0.1.0` is tagged:

1. Create `develop` from the `v0.1.0` tag.
2. Future feature-branch PRs target `develop` instead of `main`.
3. `main` becomes merge-only from `release/*`/`hotfix/*` from that point on.

This avoids introducing branch-workflow churn in the same change as the first-ever
release.

## Story workflow

See docs/ARCHITECTURE.md "Story workflow" for the full human-facing writeup (the stage table, the
checklist mapping in `spec-templates/DOD.md`/`DOR.md`); this section is the operational summary the
agent prompts themselves point back to.

Every story passes through exactly 6 stages, in this exact order, no skipping: **Draft** (Product
Owner, supported by Architect - GH issue #94) → **Ready** (Product Owner, supported by Architect) →
**Implemented** (Dev Team) → **Reviewed** (Architect) → **Tested** (QA) → **Accepted** (Product
Owner). `STORY_STAGES`/`STAGE_OWNERS` (`agents/scrum_team/helpers.py`) are the source of truth for
the stage list and ownership.

`advance_story_stage(title_or_id, stage)` (`agents/scrum_team/tools/requirements.py`) is the only
way a stage is marked complete, and it enforces, in code:
- **Order**: rejects the call if the stages before `stage` aren't complete yet.
- **Ownership**: rejects the call if `tool_context.agent_name` isn't that stage's owner.
- **One story at a time**: `product_backlog` list order is priority order; a story can't advance
  past Ready until the immediately-preceding story (`_preceding_story`, skipping Epics) has reached
  Accepted.
- **Content quality**: for Ready and Accepted/legacy-Done, `_story_readiness_issues` refuses the
  write if title/user story/acceptance criteria are missing or still placeholder/blank text - the
  same check `create_from_template`/`upsert_adr` already apply to templates themselves (see
  `_strip_agent_safeguard_comments` in `agents/scrum_team/tools/docs.py`), now applied to content
  quality, not just leftover template markup.
- **No bypass**: `upsert_story`/`upsert_epic`/`plan_sprint_backlog_item` refuse to set `status`
  directly to any of the 6 stage names *or* a legacy done-synonym ("Done"/"completed"/"closed" -
  `_story_stages_completed`'s read-side backward compat treats any of those as every stage complete,
  so setting one directly is an equally complete bypass, just spelled differently) - see
  `blocks_direct_status_set` in `agents/scrum_team/helpers.py`. Only `advance_story_stage` can set
  status this way, since it's the only path that actually enforces the above.

`advance_story_stage` re-renders `specs/ROADMAP.md`'s per-stage checkboxes for that story via
`_sync_roadmap_for_story`/`update_roadmap` in the same call that marks a stage complete - and its
own top-level `status` reflects whether that sync (and the story markdown rewrite) actually
succeeded, not just whether the in-state stage change did. Reporting "ok" while the roadmap update
silently failed would be exactly the kind of gap this mechanism exists to close. Beyond that,
there's no longer a separate "now go tell the roadmap" step for a story once it's moving through
stages at all.

### Denying a review (Reviewed/Tested/Accepted) requires a concrete, actionable reason

`advance_story_stage`'s Reviewed/Tested gates only ever checked that *some* `gh_pr_review`/
`gh_pr_comment` call had been made since the last attempt - never whether it was an approval or a
rejection, let alone what it said. Accepted had no stage-specific gate at all (see
`record_acceptance_check` below - ISSUE-0043). So "denying" a review was mechanically
indistinguishable from "haven't gotten to it yet": there was no tool call representing a rejection,
and whatever reason a model did give only ever existed in free-text conversation, with no guarantee
Dev Team ever saw it or that it said anything concrete.

`deny_review(title_or_id, stage, reason, tool_context=None)` (`agents/scrum_team/tools/
requirements.py`, docs/DEVELOPMENT-WORKFLOW.md's diagram 2) is the mechanical counterpart, callable
only by that stage's owner (Architect for Reviewed, QA for Tested, Product Owner for Accepted) for
exactly those three stages. It refuses a `reason` that's empty, shorter than 15 characters, a
`<...>` template placeholder, or a generic restatement of the verdict itself ("not good", "denied",
"needs work", ...) - a rejection has to actually say what's wrong and what would need to change. The
accepted reason is written onto the story's own record (both backlog copies) and re-rendered into
its Markdown file's Notes section (`_update_story_markdown`) - which Dev Team already has `read_doc`
for - so it's mechanically visible and actionable, not just something said once in a PR comment or a
conversation turn. Cleared automatically by `advance_story_stage` once the story actually advances
past the stage it was denied at, so a resolved denial doesn't linger as stale feedback.

**The denial also posts to the PR itself (GH issue #346).** A real eval run showed QA posting
"Approved for Tested stage" `gh_pr_comment`s immediately alongside `deny_review` calls denying that
exact same attempt - the denial reason only ever reached the story's Markdown file before this, never
the PR, so the only GitHub-visible trail said the opposite of what actually happened. `deny_review`
now posts a `gh_pr_comment` itself, generated from the denial's own reason, instead of relying on a
separate free-text call the model might get wrong or skip - best-effort, same as every other side
notification in this codebase (a comment-post failure doesn't turn an already-recorded denial into an
error).

**Accepted has its own evidence gate (ISSUE-0043).** Unlike Reviewed/Tested, Product Owner doesn't
leave PR reviews, so there was nothing for Accepted's gate to check at all - any role could
previously call `advance_story_stage(id, "Accepted")` on assertion alone.
`record_acceptance_check(title_or_id, note, tool_context=None)` (`agents/scrum_team/tools/
requirements.py`) records that Product Owner actually verified the acceptance criteria, as a
per-story **counter** (`acceptance_check_count`) rather than a one-time flag.
`advance_story_stage`'s Accepted gate now refuses unless
this count is above zero.

**A denial has teeth, for Reviewed/Tested/Accepted (ISSUE-0044).** The Reviewed/Tested gates' own
evidence check (`pr_review_calls[role] > baseline`) is sprint-wide, not scoped to one story - so a
story that was just denied could otherwise still advance right away, as long as *some* review call
from that role (even the very one that led to the denial) already satisfied the sprint-wide count.
`deny_review` now snapshots the denying role's `pr_review_calls` count at deny time
(`review_denial["review_count_at_denial"]`); `advance_story_stage` requires the count to have grown
*past* that snapshot - a genuinely new review, not a reuse of the one that prompted the denial -
before the same story can complete that stage. Accepted uses the same snapshot idea against its own
per-story counter instead (`review_denial["acceptance_count_at_denial"]` vs.
`acceptance_check_count`), since it has no sprint-wide review-call counter to snapshot: a denial there
requires a genuinely new `record_acceptance_check` call, not just a retried `advance_story_stage`.

### BLOCKED stories (a genuinely stuck story, from any stage)

Distinct from `deny_review` above: a denial is a clear, actionable verdict Dev Team can act on
directly. BLOCKED means nobody on the team currently has an answer at all - a real open question, or
a mechanical loop (see [Blocking Interactions & Notifications § Loop detection and BLOCKED
stories](docs/NOTIFICATIONS.md)). Orthogonal to `STORY_STAGES` - it can happen from any stage, not
just a fixed point in the pipeline, and doesn't itself advance or roll back the story's stage.

`raise_story_blocker(title_or_id, question, category, tool_context=None)` (`agents/scrum_team/tools/
requirements.py`) - callable by any role, once it recognizes the team is genuinely stuck. Refuses a
`question` that's empty, a template placeholder, or too short, same as `deny_review`'s `reason` check
(`_is_concrete_denial_reason` is shared between them). `category` is `"technical"` (routed to
Architect) or `"product"` (routed to Product Owner - or, at the "Product" interaction level,
escalated straight to the human User instead, since that human already IS the acting product owner
day-to-day - see `should_escalate_blocker_to_user`, `agents/scrum_team/helpers.py`). Writes a
`blocked` record onto the story (both backlog copies, re-rendered into its Markdown file's Notes
section the same way `review_denial` is) and raises a `blocking_interactions` entry (kind
`"blocked_story"`).

`advance_story_stage` refuses every further call for a BLOCKED story, at any stage, until
`resolve_story_blocker(title_or_id, resolution, tool_context=None)` clears it - callable only by the
category's owning role (mirrors `deny_review` being resolved by a fresh action from the role whose
judgment the stage belongs to). At the "Product" interaction level specifically, a `"product"`-category
blocker's escalation has teeth: `resolve_story_blocker` mechanically refuses Product Owner's own
resolution until the linked `blocking_interaction` has actually been resolved by the human first
(`resolve_blocking_interaction`) - not just a notification Product Owner could route around with its
own judgment.

**The team moves on instead of staying stuck.** `_preceding_story` (the one-story-at-a-time ordering
check) now skips a BLOCKED predecessor when looking for the nearest story that must already be
Accepted - the BLOCKED story itself stays exactly where it is in `product_backlog` (so its priority
position is preserved for whenever it's resolved), but no longer freezes every lower-priority story
behind it. If it's genuinely never resolved this sprint, it isn't silently dropped: `create_sprint_report`
now always includes an "Open Questions for Stakeholder" section (ungated by interaction-level detail
tier, unlike Retrospective Actions/Impediments below) listing every story still BLOCKED when the
sprint closes, so whoever reads the report can give feedback/guidance on it before the next sprint
starts.

### One canonical priority scale, mechanically enforced (GH issue #355)

There was no single, enforced priority scale - `_PRIORITY_RANK`/`_priority_rank` (the sort the whole
backlog ordering gate depends on) only ever understood MoSCoW (`Must`/`Should`/`Could`/`Won't`), but
`ProductOwner-workflow.md`'s own BACKLOG ITEM TEMPLATE told Product Owner to use a completely
different scale ("priority: P0/P1/P2 (or numeric)"), and `set_priority`/`upsert_backlog_item`
performed zero validation on the value. A real eval run used `"P0"`/`"P2"`/`"Must"` interchangeably
across different items - since `"P0"`/`"P2"` aren't real MoSCoW values, `_priority_rank`'s own
fallback silently ranked them as `"Must"` (the highest priority), the *opposite* of what a `"P2"`
(intended low, per the very scale the prompt taught) was meant to convey.

MoSCoW stays the one canonical scale (it's already what the sort mechanism is built around). `set_priority`
and `upsert_backlog_item` (so `upsert_story`/`upsert_epic`/`upsert_issue`, and `plan_backlog_item` which
delegates to `set_priority`) now refuse any `priority` outside `{"Must", "Should", "Could", "Won't"}`
outright, naming the valid values. `_priority_rank`'s existing "no priority set at all defaults to
`Must`'s rank" behavior is deliberately left unchanged - that's a different, already-justified case (a
story nobody has explicitly deprioritized shouldn't be silently pushed to the back of the queue) from a
value someone explicitly tried to set using the wrong scale, which validation now prevents from ever
being saved in the first place.

**Migrating existing data** (PR review follow-up): validation on *new* writes does nothing about a
`priority` value already sitting in an existing repo's `specs/stories/*.md`/`specs/requirements/ISSUE-
*.md` files from before this was enforced - the old BACKLOG ITEM TEMPLATE literally taught "P0/P1/P2 (or
numeric)". `sync_stories_from_markdown` (`agents/scrum_team/tools/requirements.py`) now self-heals this
on every sync: `_migrated_priority` maps a recognized legacy value to its MoSCoW equivalent (`P0`→`Must`,
`P1`→`Should`, `P2`→`Could`, `P3`→`Won't`; `High`/`Medium`/`Low` likewise; a merely-miscased already-valid
value like `"must"` is corrected to canonical casing), falling back to `"Must"` for anything else
unrecognized - the same fallback `_priority_rank` itself already used, so this never makes an item rank
*worse* than it already silently did. `_rewrite_priority_line` then surgically replaces just the
`- Priority:` line in the file on disk (not a full regeneration via `_update_story_markdown`, which would
silently drop any Notes/Test Approach/owner/tasks content `_parse_story_markdown` never round-trips back
into state at all) - so the fix is durable, not just a one-session in-memory correction the very next
sync would otherwise re-derive and then forget again. Idempotent: a file whose value is already valid is
never rewritten. Retro findings that get auto-filed as Issues (`_file_retro_items_as_issues`) are covered
the same way once filed, since a filed Issue is just another `specs/requirements/ISSUE-*.md` file this
same sync already scans - `add_retro_action`/`add_impediment`'s own `priority` field (`"normal"`/`"high"`)
is a separate, unrelated escalation scale that was never part of MoSCoW and needs no migration.

### One-at-a-time ordering also gates the start of work, not just stage completion (GH issue #358)

`advance_story_stage`'s one-story-at-a-time ordering gate only ever refused a story reaching
Implemented-onward before the higher-priority story ahead of it reached Accepted - nothing stopped
`start_feature_branch` (the real work - writing code, opening a PR) from starting on a lower-priority
story first, wasting effort if the higher-priority one later needs rework or gets blocked.
`start_feature_branch` now runs the exact same `_preceding_story` check (same BLOCKED-predecessor
skip, same "not in `product_backlog` at all" data-integrity refusal) one step earlier, at the point
work actually begins, not just when a stage transition is claimed.

### One-at-a-time ordering gate: an Issue still blocks, but now names the reprioritization escape hatch (GH issue #368)

A real eval run (0.1.0-run45) showed two abstract process findings - "ensure robust test isolation",
"US-0001 tests experienced test isolation/fixture failures" - auto-filed as Must-priority Issues
(`_file_retro_items_as_issues`, GH #164), sitting ahead of a real feature story in `product_backlog`
order, and mechanically blocking all unrelated feature work behind them via the one-story-at-a-time
ordering gate (GH #358) until *they* reached Accepted - something an abstract process reminder often
has no concrete way to do. Product Owner found the correct escape hatch this run
(`set_priority(..., "Won't")` on both, once it was clear neither genuinely warranted blocking
priority), but only after real trial and error against the gate's own refusals.

**An earlier version of this fix exempted Issues from the ordering gate entirely - reconsidered and
reverted** (PR review feedback): exempting them removed the *only* mechanical pressure that was
actually pushing a Ready Must-priority Issue all the way to Accepted. GH #164's own enforcement
(`create_sprint_backlog_pr`'s Must-priority gate) only ever demands an Issue reach *Ready*, never
Accepted - once Ready, that gate is satisfied permanently, even if the Issue then sits completely
untouched forever. Removing ordering's pressure too would have recreated #164's original "never
actually acted on" failure one stage later in the pipeline, just for Ready-but-stalled Issues instead
of Draft-but-unplanned ones.

The actual fix: `_preceding_story` still blocks on an Issue exactly like any other unfinished item -
but when the ordering gate's own refusal message (`advance_story_stage`, `start_feature_branch`) names
an Issue specifically as the blocker, it now also names the reprioritization escape hatch. **Resolving
it for real is the lead instruction, not reprioritizing it away** (second round of review feedback):
*"'\<id\>' is an auto-filed process finding - resolve it for real (advance it through Implemented ->
Reviewed -> Tested -> Accepted like any other item) rather than reaching for set_priority('\<id\>',
...) to dodge it. Only reprioritize away from 'Must' if, on genuine reflection, it turns out this
finding never actually warranted blocking priority - not because resolving it is inconvenient right
now."* A cheap model under budget pressure reaches for whichever option reads easiest - leading with
"just deprioritize it" would turn this gate into a convenient dodge for every inconvenient finding,
defeating the entire point of auto-filing these as Must-priority in the first place (GH #164).
Reprioritization stays available (a real eval run showed Product Owner use it correctly), but only
framed as the narrow exception for a finding that genuinely doesn't warrant blocking priority, not a
shortcut around doing the work.

### Blocked-story discipline (GH issue #359)

Two gaps in how BLOCKED stories (`raise_story_blocker`/`resolve_story_blocker`) were handled, beyond
the existing "a BLOCKED story can't advance its own stage" enforcement:

1. **Real work could still start/continue on a BLOCKED story.** `advance_story_stage` already refused
   every further stage transition while `blocked` was set, but nothing stopped `start_feature_branch`
   from opening a branch and writing code for it anyway - wasted effort by definition, since the open
   question blocking it hasn't been answered. `start_feature_branch` now refuses outright on the
   target story's own `blocked` field (not just its predecessor's, see the ordering section above),
   naming the open question and directing to `resolve_story_blocker`.
2. **A blocker left unresolved across a full sprint wasn't mechanically surfaced to the retro.** A
   story still BLOCKED now, that was ALSO already BLOCKED as of the *last* sprint report (genuinely
   stuck across a full sprint, not just raised this sprint), is tracked via a new
   `blocked_story_ids_as_of_last_report` snapshot (updated by both `create_sprint_report` and
   `render_fallback_sprint_report` on every close). `create_sprint_report` now refuses to close while
   any such story isn't mentioned anywhere in this sprint's `retro_actions`/`impediment_log` text -
   the retro is where the team decides what happens next (escalate harder, reprioritize around it,
   accept the delay), not silence.

### Structured backlog dependencies (`depends_on`)

Dependencies between stories/issues were previously just an optional free-text field nothing read
mechanically - priority ordering was only ever as correct as whatever a cheap model remembered to do
by hand (GH issue #343). `upsert_story`/`upsert_issue` now accept a real `depends_on: [story_id, ...]`
list. `advance_story_stage` refuses to let an item start real development (Implemented onward - same
"actual DEVELOPMENT, not Draft/Ready grooming" scoping as the one-story-at-a-time ordering check right
above, so Product Owner can still queue dependent work up ahead of time) while any `depends_on` item
hasn't reached Accepted yet, naming the exact unmet dependency. An unknown/removed dependency id is
treated as a data-integrity problem for Product Owner to fix, not an ordering block, so a stale id
can't deadlock a story forever.

Saving a `depends_on` edge that would create a cycle (a direct self-reference, or transitively - A
depends on B depends on C depends on A) is refused at write time (`upsert_backlog_item`), via a DFS
over the whole backlog's dependency graph - caught here rather than only discovered later as a
permanent mutual deadlock once `advance_story_stage`'s own gate refuses every item in the cycle
forever.

### Sprint retrospective enforcement

`create_sprint_report` (`agents/scrum_team/tools/budget.py`) mechanically refuses to write a
report unless a *new* retro action or impediment has been logged since the last successful report:
`len(retro_actions) + len(impediment_log)` is compared against a `retro_baseline` snapshot taken
the last time the check passed, so a stale entry from three sprints ago can't trivially satisfy
this sprint's requirement forever - `add_retro_action`/`add_impediment` must produce something new,
every sprint. On success the baseline is updated to the new total. The rejection message tells the
caller (Product Owner) exactly what's missing and to transfer to Scrum Master first - turning a
skippable prompt instruction into a hand-off the tooling itself forces.

### Filed retro/impediment Issues must actually get planned

`_file_retro_items_as_issues` (`agents/scrum_team/tools/budget.py`, GH issue #164) files every
retro action/impediment as a real, Must-priority Issue in `product_backlog` - but a real eval run
(0.1.0-run42) showed that alone wasn't enough: a dependency-pinning Issue filed in Sprint 1 sat at
Draft, unaddressed, through Sprints 2-5, while every later sprint's retro just filed more Issues on
top. `create_sprint_backlog_pr` (`agents/scrum_team/tools/github.py`) now mechanically refuses to
open a sprint's backlog PR while any non-blocked Must-priority Issue is still sitting below Ready -
the same choke-point pattern as the Ready-backlog-shortfall check right next to it - with the
rejection message naming exactly which Issue IDs and what to do (`advance_story_stage(..., "Ready")`,
or `set_priority` away from `"Must"` if it turns out not to warrant that).

### Retro-item triage: technical / steering / human (GH issue #342)

Every retro action/impediment used to get auto-filed as a Must-priority Issue uniformly, regardless
of what kind of gap it actually was - correct for a genuine code/process task, wrong for a
role-behavior gap or something only a human can decide. `add_retro_action`/`add_impediment`
(`agents/scrum_team/tools/scrum.py`) now require a `category` - `"technical"`, `"steering"`, or
`"human"` - plus an optional `priority` (`"normal"`/`"high"`):

- **`"technical"`** - unchanged: flows into `_file_retro_items_as_issues` as before, Must-priority,
  planned into the next sprint.
- **`"steering"`** - a role-behavior gap, not a code fix. `_file_retro_items_as_issues` no longer files
  these as Issues at all; instead `create_sprint_report` mechanically refuses to close while an open
  `"steering"` finding has no fresh `propose_steering_change` call behind it since the last report -
  same "must be NEW since last time" baseline pattern as `retro_baseline`/`kpi_baseline`
  (`steering_proposal_count`/`steering_baseline`, bumped by `propose_steering_change` itself on a real,
  non-no-op proposal).
- **`"human"`** - genuinely outside the team's own authority to resolve. Immediately raised as a new
  `state.general_blockers` entry (not story-scoped, unlike `raise_story_blocker`), always recording a
  `blocking_interaction` (kind `"general_blocker"`) the same way story-level blockers already do. Shows
  up in the eval report's "## Blockers" section regardless of story association
  (`_collect_general_blockers`/`_render_blockers_section`, `run_eval_analysis.py`). If `priority="high"`
  and still unresolved, `run_eval.py` stops the run outright (`human_blocker_unresolved` stop reason) -
  same reasoning as the existing `blocked_needs_human` stop reason for story-level blockers: an
  unattended run has no human to act on it, so continuing just burns further sprints' budget.

### Mechanical nudge toward the steering/human retro categories (GH issue #354)

A real eval run after #342 shipped showed the model never once chose `"steering"` or `"human"` across
all 5 sprints - every retro finding, including textbook role-behavior gaps ("Maintain rigorous story
sequencing...", "Keep feature branches synchronized..."), was categorized `"technical"` and silently
dropped into the backlog as an unfixable code task. Prompt-only guidance on what each category means
wasn't enough to get it actually used - the same lesson this codebase has hit repeatedly elsewhere.

`add_retro_action`/`add_impediment` (`agents/scrum_team/tools/scrum.py`) now return a non-blocking
`warning` field when a `"technical"` finding matches either signal (`agents/scrum_team/helpers.py`):

- **Heuristic re-classification**: the text matches a small set of role-behavior/process-discipline
  signal phrases (`looks_like_role_behavior_finding` - "discipline", "synchronized", "in strict
  sequence", a role name, etc.) rather than describing a one-time code task.
- **Recurrence escalation**: the text shares real substance (keyword overlap) with an earlier sprint's
  still-unresolved `"technical"` finding of the same kind (`recurring_technical_finding_sprint`) - the
  same finding keeps getting logged without ever being escalated to a steering change.

Neither blocks the call - a real code-task finding that happens to match must still succeed - but both
are visible enough on the tool's own response, and `ScrumMaster-workflow.md`'s triage guidance now
tells Scrum Master to read and act on a `warning` before moving on. `category` is also now rendered in
both `create_sprint_report` and `render_fallback_sprint_report`'s retro/impediment sections (previously
invisible even there), so a human reviewing the report can audit classification choices directly.

### Sprint report numbering (duplicate files)

A 5-sprint eval run (0.1.0-run42) produced 12+ numbered `specs/reports/SPRINT-REPORT-NNN.md` files
instead of 5: `create_release_pr` unconditionally calls `render_fallback_sprint_report` to land the
report on `develop` before opening the release PR (regardless of whether `create_sprint_report`
already succeeded that sprint), and that function always allocated a *fresh* sequential number even
when only reusing an already-existing report string. `state.sprint_report_path` now records the
numbered path the sprint has already allocated (set by both `create_sprint_report` and
`render_fallback_sprint_report`, cleared each sprint via `sprint_budget_reset_state_delta`) - a
repeat call within the same sprint reuses that path instead of burning a new number.

`_write_conversation_transcript` had the exact same bug (GH issue #345), also called unconditionally
from both `create_sprint_report` and `create_release_pr` - a 5-sprint run (0.1.0-run43) produced 10
numbered `TRANSCRIPT-NNN.md` files instead of 5. `state.transcript_path` mirrors `sprint_report_path`
for this function, same reuse-within-the-sprint fix.

### Budget exhaustion mid-sprint is a normal sprint ending, not a failure

`render_fallback_sprint_report`'s rendered banner used to read "⚠️ Automatically Generated Fallback
Report... before Product Owner could author and close the **real** sprint report" - language (and
matching code comments: "degraded stand-in", "visibly degraded") that framed a sprint ending because
the team used its full token/USD budget on actual implementation work as an exceptional failure
rather than the expected, normal way a sprint can close. Mechanically, nothing was ever wrong here:
`_ensure_sprint_report_on_final_halt_once` (`agents/scrum_team/agent.py`) only ever commits
`specs/` + `.hc/state.json` (via `integrate_open_changes`, itself scoped to exactly those paths) to
`develop` - it never touches, merges, or integrates any open `feature/*` branch, and never changes a
story's stage. Unfinished feature branches are correctly left exactly as they are, to be picked up
again next sprint. The banner and surrounding comments now describe this as what it is: a report
rendered mechanically from logged state (because there's no LLM turn left to author a narrative one
with) rather than a "fake" report standing in for a "real" one.

### Templates for retro items and steering proposals, documented alongside the reports (GH issue #356)

Retro actions, impediments, and steering proposals previously had no durable artifact of their own -
just in-memory state, a terse one-line bullet in the sprint report (`category` not even rendered
there, see #354), and for a steering finding, a `propose_steering_change` PR body that could be lost
once the PR merges/closes. New templates, modeled on `spec-templates/requirements/TEMPLATE-ISSUE.md`:
`spec-templates/reports/TEMPLATE-RETRO-ITEM.md` and `TEMPLATE-STEERING-PROPOSAL.md`.

`create_sprint_report`/`render_fallback_sprint_report` now also render `specs/reports/RETRO-NNN.md`
(every time, from `_render_retro_doc`) and `specs/reports/STEERING-NNN.md` (only once at least one
`propose_steering_change` call has actually succeeded, from `_render_steering_doc` - no empty file
otherwise), alongside the existing `SPRINT-REPORT-NNN.md`/`TRANSCRIPT-NNN.md` pair - same numbered-
path-reuse-within-a-sprint pattern (`state.retro_doc_path`/`steering_doc_path`, cleared each sprint
via `sprint_budget_reset_state_delta`), shared between both call sites via `_write_retro_and_steering_
docs` so they can't drift apart the way `sprint_report_path`'s own bug (above) once did.
`propose_steering_change` (`agents/scrum_team/tools/workflow.py`) now records a structured entry
(`state.steering_proposals` - role, rationale, proposed content, PR link) on every successful call,
since nothing previously retained this once the PR itself did.

**Design decision on the issue's own open question** (one file per sprint vs. one per item): per-
sprint, for consistency with `SPRINT-REPORT-NNN.md`/`TRANSCRIPT-NNN.md`'s existing convention - but
with content accumulated across the *whole run to date* (same scope as the sprint report's own terse
Retrospective Actions/Impediments sections already use), not sprint-isolated: a finding's eventual
resolution is often only known in a later sprint, so the full running history is the more useful
durable record, and avoids inventing new cross-file-synced per-sprint counters alongside the existing
(and previously bug-prone, see #345's own history above) `retro_baseline` mechanism.

### Non-blocking nudge for stubbed "assert True" tests (GH issue #370)

`check_build`/the Tested-stage gate (`advance_story_stage`) only ever counted pass/fail totals from
the real test suite run - it never inspected what a test actually asserts. A real eval run (0.1.0-
run45) showed DevTeam hedge a genuinely flaky real test suite by also writing separate dummy test
files alongside it each sprint (literally `def test_dummy(): assert True`), so the pass count always
had *something* passing regardless of whether the real functionality was actually verified.

`detect_stubbed_tests` (`agents/scrum_team/tools/quality.py`) scans `test_*.py`/`*_test.py` files
(pytest's own discovery convention) for a test function whose body is just `pass`, a bare docstring,
`assert True`, or a tautological `assert <literal> == <same literal>` - no other real assertions or
fixture usage. The Tested-stage gate now calls this right after a story's test suite genuinely passes,
and surfaces a non-blocking `warning` on the result naming the file/function if any are found - it
never refuses the transition (a real, intentional placeholder has legitimate uses too, and false
positives here are harmless; false negatives just mean the nudge doesn't fire). Deliberately a narrow,
explicit AST heuristic rather than deeper static analysis, matching #354's own risk tolerance for this
category of nudge.

### KPI trends report per-sprint deltas, not cumulative totals

`_kpi_time_series`/`_sprint_metrics_table` (`agents/scrum_team/scripts/run_eval_analysis.py`) read
each sprint's `sprint_backlog` snapshot - which is never reset between sprints, so it accumulates
stage progress across the *whole run*. A raw per-sprint count of "items with stage X completed" was
therefore a running total, not that sprint's own throughput: a real run's Velocity/Stories-implemented
KPIs read 1, 3, 5, 6, 6 - monotonically non-decreasing, since sprint 3's "5" still counted sprint 1
and 2's already-accepted items too, hiding the run's actual (tapering-off) velocity. Both functions
now diff each sprint's set of items-at-stage-X (by item ID/title) against the previous sprint's set,
reporting how many items newly reached that stage *this* sprint.

### KPI "no data" message distinguishes never-called from called-but-null

`_render_kpi_graphs` (`agents/scrum_team/scripts/run_eval_analysis.py`) rendered one generic "never
computed... not called in any sprint" message whenever a KPI's time series was empty - but an empty
series has two different causes: `calculate_kpis`/`update_sprint_report` genuinely never ran this
run, or it ran every sprint and correctly reported `None` for one specific sub-metric with its own
explanatory `*_note` (e.g. `defect_escape_rate_note: "not available - no defect/bug-lifecycle
tracking exists yet"`, `tools/quality.py`). A real run (0.1.0-run43) showed the report falsely
claiming QualityGuardian "was not called in any sprint" for defect escape rate, while the same run's
Say-Do Ratio - sourced from the exact same `calculate_kpis` calls - had real values for 4 of 5
sprints. `_kpi_unavailable_message` now checks each sprint's `sprint_report_kpis` for the specific
`*_note` field before falling back to the generic message, so the report states the real,
more-useful reason instead of misrepresenting the team's process.

### Human interaction levels

`record_human_approval`'s two gates (`advance_story_stage(..., "Implemented")` and
`create_release_pr`, see ISSUE-0001 above) support three approval types depending on
`INTERACTION_LEVEL` (`agents/scrum_team/helpers.py`'s `get_interaction_level()`, see
`docs/INTERACTION-LEVELS.md`), via a level-driven lookup
(`required_pre_implementation_approval`/`required_pre_release_approval`):

| Level | Pre-implementation | Pre-release |
|---|---|---|
| Product | `"sprint"` | `"release"` |
| CEO | `"budget"` | none |
| EVAL | none | none |

`record_human_approval` itself gained a third `approval_type`, `"budget"`, for the CEO level - no
new state field was needed: the existing `sprint_approval_baseline`/`release_approval_baseline`
"must be NEW since last time" snapshots (in `create_sprint_report`/`create_release_pr`) now compare
against whichever type the active level actually requires, computed fresh from the environment each
time rather than persisted - a misconfigured or unset `INTERACTION_LEVEL` falls back to `Product`,
the most-supervised level, instead of silently disabling every gate. `run_eval.py` now sets
`INTERACTION_LEVEL=EVAL` explicitly, so the "no human in the loop" property of an eval run is
mechanically guaranteed rather than resting entirely on the model obeying scripted prompt text.

`create_sprint_report` also branches on level via `report_detail_level()`, rather than always
rendering the same unconditional content: `full` (Product, EVAL) keeps everything, `executive` (CEO)
renders budget and headline outcomes only. This is in the report-generation code itself, not left to
the PO agent's
prompt-following, so a human at a given level gets a consistently-shaped report regardless of how
that call happened to be phrased - and no data is silently dropped: an omitted section is still named
explicitly under a "Full Process Detail" heading, pointing at exactly where it still lives - most
sections point at `.hc/state.json`, but the conversation transcript points at
`specs/reports/TRANSCRIPT-LATEST.md` instead (a human-readable Markdown file, not a raw blob in
`.hc/state.json` - see GH issue #127).

### Tool dispatch resilience (hallucinated/disallowed tool calls)

A real eval run (2026-07-24, GitHub Actions run 30099595847) crashed outright: ProductOwner called
`write_file`, a tool only DevTeam/QualityGuardian actually have (see each `LlmAgent`'s `tools=[...]`
in `agents/scrum_team/agent.py`) - ADK's own dispatch code doesn't route that to the tool function at
all, it raises a bare `ValueError` ("Tool 'write_file' not found...") that propagates all the way up
through `runner.run_async` and kills the whole process, discarding every sprint completed so far in
that run. A single hallucinated tool name from one sub-agent, out of dozens of calls across a
multi-sprint run, should not be fatal to the entire session.

ADK (2.4.0+) provides exactly the hook for this: `LlmAgent(on_tool_error_callback=...)`. When tool
dispatch fails because the name isn't found for the calling agent at all, ADK synthesizes a
placeholder `BaseTool(name=<hallucinated name>, description="Tool not found")` before invoking this
callback (see `google.adk.flows.llm_flows.functions._execute_single_function_call_async`); if the
callback returns a dict instead of `None`, that dict becomes the tool's function-response - the model
sees an ordinary tool-error message and can recover (try a real tool, or `transfer_to_agent`) instead
of the process aborting. `on_tool_error_callback` (`agents/scrum_team/agent.py`) checks for exactly
that `"Tool not found"` placeholder (not string-matching the exception text, which could change
across ADK versions) and returns a message naming the tool, the calling role, and the two ways to
recover. It's registered on every `LlmAgent` - all six specialists via `COMMON_AGENT_CALLBACKS`, and
`root_agent` directly (it defines its own callback lists rather than using the shared dict). A
genuine exception raised *inside* a real tool call (its actual description, not the placeholder) is
left alone and still propagates - this only softens dispatch-time "tool not found" errors, not real
bugs.

### Self-transfer resilience (hallucinated agent-to-self transfers)

A real eval run crashed on sprint 2/5: DevTeam's turn produced a `transfer_to_agent` call naming
itself as the target. ADK validates transfer targets in a different code path from tool dispatch -
`resolve_and_derive_transfer_context` (`google.adk.workflow.utils._transfer_utils`) - and raises a
bare `ValueError` ("Agent 'DevTeam' cannot transfer to itself.") before any tool-callback machinery
ever runs, so `on_tool_error_callback` above never sees it. Like the hallucinated-tool-name case,
this propagated straight through `runner.run_async` and killed the whole process.

There's no equivalent ADK callback hook for transfer-resolution errors, so this is handled at the
call site instead: `_run_one_sprint` (`agents/scrum_team/scripts/run_eval.py`) wraps its
`async for event in runner.run_async(...)` loop in a `try`/`except ValueError`, matching only on the
`"cannot transfer to itself"` message (any other `ValueError` still propagates, so a real bug isn't
silently swallowed). On a match, the sprint ends early with `stop_reason: "adk_self_transfer_error"`
instead of crashing - the run continues to the next sprint and still produces a full manifest/report,
the same resilience goal as the tool-dispatch fix above, just via a plain `try`/`except` since ADK
doesn't expose a hook for this particular failure mode.

This is scoped to the eval harness's own driver loop - it's the only place in this codebase that
calls `runner.run_async` directly. An interactive/production run goes through ADK's own runner
(e.g. the ADK web server), which this fix does not touch.

### Transfer-rotation breaker no longer permanently deadlocks a run (GH issue #367)

The rotation-based transfer-loop breaker (`_transfer_rotation_count`, GH issue #191) used to **pin**
its counter at `TRANSFER_ROTATION_THRESHOLD` once it fired, rather than resetting it to 0 the way the
pair-based breaker (`_transfer_loop`) already does. Since every subsequent `transfer_to_agent` call -
to *any* target, by *any* agent - is refused for as long as the counter sits at/above threshold, and
the only reset path is a non-transfer tool call succeeding (which first requires reaching a role
capable of making one, which first requires a transfer succeeding), this pin was unrecoverable: a real
eval run (0.1.0-run45) hit it during the mandatory budget-exhaustion SPRINT CLOSE SEQUENCE's own
correct, system-instructed hand-off chain (not a genuine stuck rotation) and the whole run never
reached ScrumMaster/QualityGuardian/ProductOwner again for the rest of its sprints.

Two related bugs, both in `_detect_transfer_loop` (`agents/scrum_team/agent.py`):
1. The rotation counter now **resets to 0 once it fires**, instead of pinning at the threshold - it
   still breaks an actively-spinning rotation in the moment (its actual job), without permanently
   banning every future hand-off for the rest of the run. A genuinely recurring rotation simply trips
   it again, which is correct.
2. The pair breaker (`_transfer_loop`) firing now **also resets the rotation counter**, since that
   streak's hops were otherwise inherited by the independent rotation budget too - run45's exact
   failure was 3 wasted pair-ping-pong hops (broken by the pair breaker) immediately followed by a
   legitimate 3-hop close-sequence chain, with the two sums (3 + 3) landing exactly on
   `TRANSFER_ROTATION_THRESHOLD` (6) on the chain's own correct final hop.

### Forgotten-implementation nudge (DevTeam transferring away before advancing the stage)

`advance_story_stage`'s Implemented gate already refuses to let a story reach Implemented without a
real `write_file` since the last successful Implemented transition
(`dev_touch_baseline`/`sprint_files_touched`) - but nothing previously caught the opposite: DevTeam
writing the real implementation, then transferring away (to Architect, QA, back to the Orchestrator,
...) without ever calling `advance_story_stage(..., "Implemented")` for it. A story whose code is
actually done but whose stage silently stays at Ready/earlier blocks that story - and, via the
one-story-at-a-time ordering gate, every lower-priority story behind it - from ever reaching Accepted,
with no mechanical signal anything is wrong (GH issue #344).

`_detect_unadvanced_implementation` (`agents/scrum_team/agent.py`) hooks into
`log_tool_invocation_callback`'s existing `transfer_to_agent` interception point (alongside the
self-transfer and loop-breaker checks already there): when DevTeam transfers to a genuinely different
role while `source_touch_count` (the same signal `advance_story_stage`'s own gate tracks) has grown
past both `dev_touch_baseline` *and* a new `unadvanced_write_nudge_baseline`, the transfer is blocked
with a reminder naming the story in progress (`_current_story_in_progress`). The nudge baseline
snapshots the current touch count on firing, so an immediate retry of the same transfer (no new
`write_file` in between) is let through - DevTeam may have a real reason to transfer before finishing
(e.g. a design question for Architect mid-story), and a reminder it has already seen once must not
become a permanent deadlock. Further new `write_file` activity re-arms the nudge.

### Sprint backlog requires real team engagement before implementation starts (GH issue #357)

Product Owner proposes the Sprint Backlog (`create_sprint_backlog_pr`), but that PR merging was
never, by itself, proof the rest of the team actually weighed in on the proposed priority/sequencing
- Dev Team could start implementing the moment it merged. The user's explicit requirement: "The team
gives feedback and finally approves it and commits to it in the planning ritual. Before this is clear
no implementation work should be started."

`start_feature_branch` now mechanically refuses to run until Architect, Dev Team, and QA have each
left a real `gh_pr_comment`/`gh_pr_review` on *this sprint's* Sprint Backlog PR - reusing the
`pr_review_calls` per-role counter the Reviewed/Tested stage gates already track, no new tracking
mechanism needed. The counter accumulates across the whole run rather than resetting per sprint, so a
fresh `sprint_backlog_engagement_baseline` is snapshotted from it the moment this sprint's Sprint
Backlog PR first merges (`create_sprint_backlog_pr`) - the gate then requires each role's count to
have grown past that baseline, not just be nonzero historically. At that point in the sprint this PR
is the only one that could possibly exist yet (no feature branches are open), so any role's review
call is necessarily real engagement with it. A second `create_sprint_backlog_pr` call the same sprint
(Product Owner adding more stories) doesn't re-snapshot the baseline, so engagement already given
isn't silently forgotten.

This is feedback-and-commitment, not a veto: the team's comments don't block the PR from merging, and
Product Owner still owns the final prioritization - only the *start of implementation* is gated on the
team having actually engaged with it.

**Prompt clarification follow-up (GH issue #369)**: a real eval run showed DevTeam correctly identify
that QA needed to act to satisfy this gate, but with no way to tell QA that via `transfer_to_agent`
(no payload beyond a target name), QA transferred back instead - a ping-pong the pair loop-breaker had
to step in and break. DevTeam then tried calling `gh_pr_comment` itself, worded as QA's own sign-off -
this does nothing, since `gh_pr_comment`/`gh_pr_review` always attribute to the *actual calling
agent's* role, never to whatever the text claims. `DevTeam-workflow.md`/`QA-workflow.md`/`Architect-
workflow.md` now say this explicitly: there's no way to post "as" another role, so don't try - and if
transferred to specifically because this gate named you as still missing, the expected response is to
immediately leave your own comment, not transfer further.

### Reading back PR comments/reviews (`gh_pr_comments`)

`gh_pr_comment`/`gh_pr_review` only ever write - there was no way for any role to read back what
another role (or it itself) had already said on a PR, only whether the mechanical team-engagement gate
above considers them to have commented *at all*. A real eval run showed DevTeam unable to tell whether
Architect/QA had already left feedback worth reading, and separately try (and fail) to satisfy QA's own
missing-engagement requirement by posting a comment itself worded as QA's sign-off - `gh_pr_comment`
attributes by the real calling agent, never by what the text claims, so a role had no way to even
*verify* that before assuming it worked.

`gh_pr_comments(pr_id=None)` (`agents/scrum_team/tools/github.py`) reads back `gh pr view`'s own
`comments`/`reviews` JSON - each entry's author, body, and timestamp, in chronological order. Since
every comment this codebase posts is prefixed with the posting role's own name (`**Architect:**`,
`**QA:**`, ...) regardless of the underlying shared GitHub account, that prefix - not the raw GitHub
username - is how a caller tells who said what. Added to DevTeam/QA/Architect/ScrumMaster's tool lists
(the same roles that already have `gh_pr_comment`/`gh_pr_review`), with prompt guidance to check it
before assuming nothing has happened yet, or before repeating feedback another role already gave.

### DevTeam/Architect can actually delete a file they created by mistake (GH issue #376)

`write_file` only ever writes or overwrites - there was no tool anywhere that removed a file from
disk. A real eval run (0.1.0-run46) had DevTeam write a test file with a wrong import
(`tests/test_todo_unit.py`), then narrate "removing" it rather than actually deleting it - the
broken file sat on disk, failing `check_build`'s pytest collection identically on every later
attempt, which permanently blocked that one story at Tested. Because of the (correctly-working)
one-story-at-a-time ordering gate, every other story was then starved behind it for the rest of
the run - 0 stories completed across all 5 sprints, from this one missing capability.

`delete_file(path)` (`agents/scrum_team/tools/docs.py`) actually unlinks a repo-relative file
(same repo-root containment check as `write_file`; errors clearly on a path that doesn't exist or
is a directory) and records it via `_record_touched_file`, so removing a broken file to unblock
the real one still counts as real dev progress toward the Implemented-stage touch-count gate.
Added to DevTeam's and Architect's tool lists (the two roles that already have `write_file`), with
prompt guidance in `DevTeam-workflow.md` to reach for it instead of narrating a no-op removal.

### KPIs sourced from the wrong backlog field, and stories never actually planned into a sprint (GH issue #378)

A real eval run (0.1.0-run47) showed Velocity/Issues-fixed/Stories-implemented flat at 0 across all
5 sprints despite real stage progress happening (several stories reached Accepted). Two compounding
causes: `run_eval_analysis.py`'s `_kpi_time_series` read `sprint_backlog`, which stayed `[]` the
entire run because DevTeam never called `plan_sprint_backlog_item` a single time - nothing
mechanically required it before starting real work. `advance_story_stage` only ever writes stage
progress into `sprint_backlog` if the story was already planned into it; it always writes to
`product_backlog` (the authoritative superset) regardless, which is where the real progress was
sitting the whole time, unread.

Fixed both sides: `_kpi_time_series` now sources from `product_backlog`. `start_feature_branch`
(`agents/scrum_team/tools/github.py`) now mechanically refuses to start work on a story that hasn't
been planned into the current sprint via `plan_sprint_backlog_item` first - so the sprint backlog
(and everything sourced from it, like "Stories Planned" in the per-sprint table) actually reflects
what the team committed to, not just what got silently implemented without ever being planned.

### Sprint-close honesty, and a real release PR even when the budget runs out (GH issue #379)

A real eval run (0.1.0-run47) had the per-sprint report table claim "Sprint Report? yes" for all 5
sprints, and 4 of 5 sprints merged no release PR at all - both traced to the same cause: the
budget-exhaustion safety net (`_ensure_sprint_report_on_final_halt`, agent.py) already guaranteed a
sprint report always exists, but never tried to release it, so Product Owner's own
`create_release_pr` simply never got a turn to run on those 4 sprints. The per-sprint table also
couldn't tell that fallback stub apart from a real Product-Owner-authored report, reporting "yes"
either way.

Two fixes: the safety net now also attempts `create_release_pr` itself, best-effort, right after
committing the fallback report - if this interaction level requires a fresh pre-release approval
that isn't available mechanically, or develop/main are already in sync, it just errors harmlessly
and `sprint_report_pending_release` stays set for a later sprint to clear, exactly as before.
Separately, `run_eval_analysis.py`'s per-sprint table now reads "fallback" instead of "yes" when the
report is this mechanically-rendered stub (detected via its own fixed heading), so the distinction
is visible instead of hidden.

### Sprint Backlog PR no longer races other tools for whichever pending writes land first (GH issue #379)

The same run also showed sprint-backlog and story-spec PRs opening with no actual roadmap/story
edits in them. Root cause: `create_sprint_backlog_pr` committed via `git add -A`, sweeping up
*every* pending write in the shared checkout - including another story's not-yet-committed spec
file `upsert_story` had already written to disk - and merged it into `develop` before the later,
deliberately-scoped `create_story_spec_pr` ever got a chance to claim it, leaving that PR empty.
`git_push`'s own `--allow-empty` fallback (a deliberate fix for a different, earlier bug - see
ISSUE-0050/0.1.0-run34) meant even a sprint with genuinely nothing new yet still silently
opened/merged a content-free "Sprint Backlog" PR.

`create_sprint_backlog_pr` now calls `integrate_open_changes` (scoped to `specs/`+`.hc/` only - the
same pattern `create_release_pr` already uses for exactly this reason) instead of `git add -A`, and
refuses outright with a clear message if there's genuinely no new planning output to publish,
instead of leaning on git_push's generic empty-commit fallback.

Separate from releasing the *code*, `.github/workflows/eval.yml` automatically
evaluates how well the agent team itself performs, against a fixed scenario, so
regressions or improvements in team behavior surface release over release instead
of only being noticed anecdotally.

- **Fixed scenario**: `eval/scenario/PRODUCT-VISION.md` — a deliberately narrow
  to-do-list-web-app product vision, byte-identical across runs so results are
  comparable across versions. Don't edit it to make a run look better; if the
  scenario genuinely needs to change, that's its own deliberate, explained commit.
- **Isolated public state repo**: `CI-Till-Krempel/horseless-carriage-eval-todo-app`.
  Every run creates its own isolated GitFlow `main`+`develop` branch pair
  (`eval/<version>-run<N>/main`, `eval/<version>-run<N>/develop`) rather than
  touching the eval repo's real default branch, so runs never contaminate each
  other or the real target repo you'd use for actual work (see
  `_prepare_local_clone` in `run_eval.py`, and `_develop_branch_name`/
  `_default_push_branch` in `tools/base.py`, which read the run's isolated
  values via `GITHUB_REPO_BRANCH`/`GITHUB_DEVELOP_BRANCH`). Both branches are
  pushed to the remote immediately after being created, before the team does
  anything - `gh_pr_create`/`start_feature_branch`/`create_release_pr` default
  their PR `base` to one or the other, and `gh pr create --base <branch>` fails
  outright if that branch doesn't exist on the remote yet. Without this,
  0.1.0-run4 produced feature branches but zero PRs for the whole run - silently,
  since `create_release_pr` used to always report `"status": "ok"` regardless of
  whether the underlying push/PR-create actually succeeded (also fixed).
  Story-level work happens on `feature/<story-id>-<slug>` branches opened as
  draft PRs into `develop` (`start_feature_branch`), merged by QA's own
  `merge_story_pr` call once a story reaches Tested - real behavior, no harness
  involvement. `create_release_pr` (called every sprint, now the `develop` ->
  `main` "sprint PR") is the one PR the harness itself auto-merges, standing in
  for the human-approval gate real usage requires for it (see below).
- **Driver**: `agents/scrum_team/scripts/run_eval.py` runs the team through 5
  sprints headlessly (no human in the loop) via ADK's `Runner` API directly,
  using the cheap `scrum-eval-cheap` model alias (see `litellm.yaml`) and budgets
  from `EVAL_SPRINT_TOKEN_BUDGET`/`EVAL_USD_BUDGET_PER_SPRINT` in `.env` (reusing the
  same guardrails from ["Budget Management"](docs/BUDGET.md)), currently
  5,000,000 tokens/$3 per sprint (GH issue #81: 2,600,000 was too tight for later,
  more complex sprints - a real run hit ~2.8M tokens and truncated sprints 4-5;
  ISSUE-0045: 4,000,000 was then too tight for a sprint 1 that also has to draft
  and merge the whole product's requirements up front). `eval.yml` also sets
  `READY_BACKLOG_SPRINTS_TARGET=1` for this workflow only (real usage keeps the
  product default of 2, see `helpers.py`'s `ready_backlog_sprints_target`) -
  this fixed scenario's entire backlog is 6 stories, so the product default of
  2 sprints × 3 stories/sprint demanded the *whole* backlog be Ready before
  Sprint 1 could even publish, forcing 100% of requirements engineering into a
  single pre-Sprint-1 burst (see ISSUE-0045).
  The **token** budget resets at the start
  of every sprint (`_run_one_sprint`'s `state_delta` zeroes `token_usage`, the
  harness-side equivalent of the `reset_sprint_budget` tool used in
  interactive/real usage) and `--token-budget` is used as-is, per sprint - a
  sprint that used most of its budget no longer starves later sprints, unlike
  before this was fixed. The **USD** budget stays a whole-run cumulative ceiling
  by design (`--usd-budget` still scales by `--sprints`) - it's enforced by the
  LiteLLM proxy's shared `scrum-sprint-budget` object, a real financial cap that
  intentionally isn't per-sprint.
  On top of that, `--max-duration-minutes` (default 40) is an independent
  wall-clock safety net: if the token/USD guardrails somehow don't stop things
  (a bug, an unexpected model behavior), the run still stops gracefully - it
  writes out whatever's been gathered so far as a real report rather than
  running until the CI job's own hard `timeout-minutes` kills the process with
  no output at all. Verified directly: forcing the deadline to 0 stops the run
  before sprint 1 with `stopped_early: true` and a valid, if empty, report.
  **Stops early on a story blocked with no real way forward** (GH issue #336):
  a real eval run (0.1.0-run39) had a story get BLOCKED by the mechanical
  loop-breaker in sprint 2, then sat blocked through sprints 3-5 with no
  resolution - this scripted driver has no way to call `resolve_story_blocker`
  itself (see below: it "pre-approves every sprint goal/backlog... standing in
  for the human review gate real usage requires"), so those 3 remaining
  sprints burned real tokens/budget on a story that could never move forward.
  `_sprint_needs_human_this_harness_cannot_provide` (`run_eval.py`) now stops
  the run (`stop_reason: "blocked_needs_human"`/`"blocked_unresolved_across_sprint"`)
  the moment either holds: a BLOCKED story's category escalates straight to
  the human User at this interaction level
  (`should_escalate_blocker_to_user`, `agents/scrum_team/helpers.py` - this
  driver has no human to answer it, so an immediate stop, not a wasted
  sprint), or the *same* story is still BLOCKED at the end of a sprint that
  already started with it blocked (the team had a full sprint's own budget to
  resolve it themselves and didn't - further sprints are the same bet with no
  new information).
  **A sprint report always exists, even when the harness's own event/time
  caps cut the close-out sequence off first** (0.1.0-run41): a real run's
  SPRINT CLOSE SEQUENCE grace was making genuine progress (retro logged,
  KPIs computed) but got cut off by `max_events_per_sprint` one turn before
  ProductOwner's `create_sprint_report` - agent.py's own safety net
  (`_ensure_sprint_report_on_final_halt_once`) only fires once a
  grace-eligible role's own turn *also* exceeds its (shrunk) grace
  allowance, which never happened here; the sprint just ran out of
  *host-side* turns first. `_run_one_sprint` now calls the exact same
  mechanism directly as a backstop whenever a sprint ends with
  `critical_halt_notified` set but no report - the in-agent budget grace
  and this harness's own event/time caps are two independent stopping
  mechanisms, and "a report always exists" needs a backstop that doesn't
  depend on which one fires first.
  **The rendered report now surfaces *why* a run stopped early, and lists
  every currently-BLOCKED story** (0.1.0-run41): previously `stop_reason`/
  `blocked_story` lived only in `manifest.json` - a user had to dig through
  raw GitHub Actions logs to find out why a run only completed 1 of 5
  requested sprints. `run_eval_analysis.py`'s `_render_report` now renders
  a "⚠️ Evaluation Stopped Early" callout (stop reason, crash detail, or the
  blocked story - whichever applies) as the very first thing after the run
  header, and a "## Blockers" section listing every story still BLOCKED as
  of the last completed sprint - the same view `create_sprint_report`'s own
  "Open Questions for Stakeholder (Blockers)" section already gives per-sprint.
  **Local runs only** (`GITHUB_ACTIONS` unset): before spending anything, the
  script checks that the LiteLLM proxy is actually reachable, not just
  configured - the USD guardrail above lives entirely in the proxy (see
  docs/BUDGET.md) and silently does not apply without it. If
  it's not reachable, the script prints a loud warning and refuses to proceed
  unless `--dev-mode` is passed, acknowledging that only the local token-count
  guardrail is protecting the run. `eval.yml`'s CI job always brings the proxy
  up and waits for `/health/readiness` first, so this never triggers there and
  `--dev-mode` is never needed in CI.
  Because there's no human to approve PRs, it auto-merges any PR that opens
  against the run's `main` (the sprint-level `develop` -> `main` PR) once each
  sprint's invocation finishes — a deliberate, documented simplification of the
  real "Human Review is mandatory" flow, not a silent one. Story-level
  `feature` -> `develop` PRs are left alone by this auto-merge (narrowed by
  `base_branch` in `_merge_open_prs`) since QA's own `merge_story_pr` call
  already merges those during the sprint, matching real usage. Before merging
  each sprint's PR(s), it posts that sprint's full
  raw agent activity log (every event's author/text and each tool call's actual
  arguments/response, not just the tool name - a cheap model often makes a
  tool call with little or no accompanying free text, so name-only logging
  looked like "just tool calls, no conversation" when the real substance - PR
  comment bodies, code passed to `write_file`, etc. - was one field over the
  whole time; see `_run_one_sprint`) as a PR comment via
  `_format_sprint_transcript`/`_post_sprint_transcript`, capped at
  `MAX_TRANSCRIPT_CHARS` with a pointer to the full, untruncated version. That
  full version - every sprint's complete transcript, all events, no cap - is
  written to `transcript.md` and uploaded as its own CI artifact (see
  `_format_full_transcript`, eval.yml's "Upload report artifact" step) rather
  than only ever existing inside the run manifest. Every ad-hoc branch/PR the
  team creates during an eval run (feature branches) is tagged with the run id
  (`eval-<run-id>/<branch>`, `[eval-<run-id>]` PR title prefix - see
  `_with_eval_branch_prefix`/`_with_eval_title_prefix` in
  `agents/scrum_team/tools/base.py`), set via `EVAL_RUN_ID` and never present
  in real usage, so branches/PRs from different runs sharing the eval repo
  stay distinguishable and the auto-merge only ever matches PRs actually
  targeting *this* run's `main`. The `main`/`develop` pair themselves use a
  different, harness-set naming scheme (`eval/<run-id>/main`,
  `eval/<run-id>/develop` - see above) rather than this prefix, since they're
  resolved directly via `GITHUB_REPO_BRANCH`/`GITHUB_DEVELOP_BRANCH`, not
  treated as ad-hoc branches.
- **Analysis**: `agents/scrum_team/scripts/run_eval_analysis.py` sends the final
  code/specs/sprint-reports to a judge LLM call against a fixed rubric (code
  quality, requirements quality, team efficiency) and writes a report with the
  top problems and suggested fixes, ranked by severity. The report is opened as
  its own small, run-id-tagged PR against the run's `main` and self-merged (the
  harness's own concluding action, after the run itself is already done) rather
  than pushed directly, so it goes through the same PR mechanism as everything
  else and shows up as the run's final PR - and is also uploaded as a CI
  artifact. It then opens a second, run-id-tagged PR from the run's `main`
  (by now containing every sprint's merged work plus that report commit)
  against the eval repo's actual default branch, as a single place to review
  everything the run produced - and deliberately leaves it **open, never
  merged** (title says so explicitly), since merging an eval run into the eval
  repo's real default branch would defeat the point of keeping eval runs
  isolated. See `_open_overview_pr`.
- **Triggers**: automatically on every `v*.*.*` tag (alongside the real release),
  and manually via `workflow_dispatch` for any branch — useful for checking a
  feature branch's effect on team behavior before merging it.
- **Requires maintainer approval before it runs.** Each run is real LLM spend, so
  the `evaluate` job targets the `eval-approval` GitHub Environment, which has a
  required-reviewer protection rule — regardless of trigger (tag push or manual
  dispatch), the job pauses and a maintainer must explicitly approve it in the
  Actions UI before anything actually executes. Manage reviewers under repo
  Settings → Environments → `eval-approval`.

### Required secrets (you must configure these — I can't provision repo secrets)

`eval.yml` needs, as GitHub Actions repository secrets:
- `GOOGLE_API_KEY`, `LITELLM_MASTER_KEY` — same as local `.env`, for the LiteLLM
  proxy the eval run stands up. `adk-eval.yml` (the smaller, cheap ADK gate-
  enforcement eval set — see `eval/adk/README.md`'s "Reproducible model config")
  reuses these same two secrets, no separate provisioning needed.
- `EVAL_GITHUB_APP_ID`, `EVAL_GITHUB_APP_PRIVATE_KEY`, `EVAL_GITHUB_APP_INSTALLATION_ID`
  — a GitHub App installed on the eval repo (**not** necessarily the same App used
  for real target repos) with `Contents` + `Pull requests: Read & write`. The
  installation must specifically include
  `CI-Till-Krempel/horseless-carriage-eval-todo-app` under "Repository access" —
  a GitHub App's own repo-scoped permissions don't extend to new repos
  automatically (this bit me during development: the eval repo returned a real
  403 until I added it to the installation by hand).

### Known limitations (found via real testing, not fixed here — out of scope)

- `litellm.yaml`'s production model aliases (`scrum-po`, `scrum-dev`, etc.) all
  point at `gemini-1.5-pro`, which 404s as a retired model against a live Gemini
  API key today. `scrum-eval-cheap` was deliberately pointed at a model confirmed
  working (`gemini-flash-lite-latest`) instead of reusing a production alias —
  the production aliases need their own follow-up fix.
- `create_litellm_virtual_key()` doesn't handle "key alias already exists"
  gracefully (a real LiteLLM 400 if the same agent name's key was already
  created in that LiteLLM database) — harmless for a fresh eval run's fresh `db`/
  `litellm` containers, but a real gap if a long-lived LiteLLM database is reused
  across many setup attempts.
- The cheap model is not fully reliable at autonomous multi-step execution — it
  sometimes announces a next action ("Next actions: transfer to X") without a
  tool call actually doing it in the same turn. `run_eval.py` sends a bounded
  number of "continue" nudges to recover from this, but a run can still end
  without a sprint report if the model doesn't recover within that budget. This
  is itself a legitimate signal about team reliability, not just a harness bug.

## Non-goals

- No Docker image publishing / container registry.
- No deployment automation (per "no deployments").
- No changes to the target-product release flow (`create_release_pr`,
  `gh_release_create`) beyond what EP-0004 already plans.
- No branch protection rules automation — set up manually in GitHub repo settings if
  desired, not part of this doc.
