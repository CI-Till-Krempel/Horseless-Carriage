# Architecture Decision Record (ADR)

- ADR-ID: ADR-0002
- Title: Per-project workbench network allowlist proposals
- Status: Proposed
- Date: 2026-11-03
- Owners: Scrum Team (tooling)

## Context
`propose_framework_change` (`agents/scrum_team/tools/workflow.py:302-469`) lets the Architect propose a workbench toolchain change (`.hc/workbench-setup.sh` in the PRODUCT/STATE repo) as a draft PR for human review. It takes an `additional_allowlist_hosts` parameter for any new runtime hosts the toolchain needs (e.g. `services.gradle.org`), but today that list is **purely informational**: it's rendered into the PR body as a bullet list and recorded via `record_blocking_interaction`, never written to any tracked file. The actual enforcement allowlist, `config/net-proxy/allowlist.filter`, lives solely in Horseless Carriage's own repo and is `COPY`'d into the `net-proxy` image at build time (`net-proxy.Dockerfile`) - there is no mount or other runtime connection from `net-proxy` to the product/state repo at all.

We want the set of hosts a given product project's workbench legitimately needs to be project-specific, versioned, and reviewable alongside that project's own `.hc/workbench-setup.sh`, instead of scattered prose across however many `propose_framework_change` PRs a project accumulates over its life - the same durability `.hc/workbench-setup.sh` itself already has.

This depends on a separate, already-filed fix: `merge_story_pr` (`agents/scrum_team/tools/github.py:1424`) had no check preventing it from merging a `framework/*` or `steering/*` branch itself - the "left as an unmerged draft for human review" guarantee for `propose_framework_change`/`propose_steering_change` was conventional, not enforced (`SECURITY.md` notes the product repo has no branch protection by default). See the fix branch `merge-guard-framework-steering` (PR #494). Any design that relies on a human actually reviewing a proposal before it takes effect requires that fix to be in place first - otherwise this ADR's "agent team cannot change the allowlist without human interaction" goal doesn't actually hold, for the same reason it didn't fully hold for `.hc/workbench-setup.sh` either.

## Decision
1. **Add `.hc/allowlist.filter`** as a new tracked file in the product/state repo, using the exact same format as Horseless Carriage's own `config/net-proxy/allowlist.filter` (newline-separated regexes matched against the CONNECT target host[:port]) so entries can be copied across verbatim.
2. **Extend `propose_framework_change`** so that when `additional_allowlist_hosts` is non-empty, it also appends `^host\.name(:[0-9]+)?$` patterns to `.hc/allowlist.filter` in the same commit/branch as the workbench script change - not just the PR-body bullet list it writes today. Keep the existing PR-body "HUMAN OPERATOR ACTION REQUIRED" section and `record_blocking_interaction` call as-is; the file becomes the versioned source of truth, the PR body remains what makes it immediately actionable.
3. **No new write-protection code needed.** `docs.py`'s existing `.hc/` write-block matches on `relative_parts[:1] == (".hc",)` - any path under `.hc/`, not just `workbench-setup.sh` by name - so `.hc/allowlist.filter` is automatically refused for direct `write_file` calls, same as the script is today. `propose_framework_change` remains the only path to it.
4. **`net-proxy`'s enforcement surface does not change.** Horseless Carriage's own `config/net-proxy/allowlist.filter` stays the sole source `net-proxy` actually reads, still baked into its image at build time, still requiring an explicit `docker compose build net-proxy` + restart. No mount, no live read access from `net-proxy` to the product/state repo.
5. **Add a human-invoked sync helper** (not agent-callable - a plain script run by a person, e.g. `scripts/sync_workbench_allowlist.py <state-repo-path>`) that reads the product repo's merged `.hc/allowlist.filter`, diffs it against Horseless Carriage's own `config/net-proxy/allowlist.filter`, and appends new lines (with a comment crediting the source proposal) after an explicit confirmation prompt. This turns today's by-hand process (what PR #493 just did manually for Gradle) into a one-command step a human still has to deliberately run.
6. This design is **contingent on PR #494 merging first** - without it, nothing stops an agent from merging its own `.hc/allowlist.filter` proposal before a human ever looks at it.

## Options Considered
- **Option A - file-based manifest, human-synced into Horseless Carriage's own build-time file (chosen)**: as above. Zero new runtime wiring on the enforcement side; only the proposal-authoring side changes.
- **Option B - live mount**: bind-mount the product/state repo (or just `.hc/`) into the `net-proxy` container, with `entrypoint.sh` merging Horseless Carriage's own baseline allowlist with the project's `.hc/allowlist.filter` at container start. Rejected for now: gives `net-proxy` - which mediates all agent egress - direct filesystem access to a repo the agent team routinely writes to; a bug in the `.hc/` write-block, or `entrypoint.sh` reading an unmerged/wrong ref instead of the merged `develop`/`main`, would grant egress without a real human merge ever happening. Picking the right ref to trust is a sharp edge this option doesn't resolve on its own. Revisit only if Option A's manual sync step becomes a genuine bottleneck.
- **Option C - CI auto-syncs/rebuilds `net-proxy` when `.hc/allowlist.filter` changes on develop**: rejected. Still avoids net-proxy-to-product-repo wiring, but moves the "a human deliberately acts" step to "CI silently propagates on merge," defeating the operator-sees-ACTION-NEEDED design `propose_framework_change` already relies on.
- **Option D - status quo (PR-body text only)**: rejected per the motivating ask - not project-specific in any durable/versioned/diffable sense, and easy to lose track of across a project's accumulated proposals.

## Consequences
- Positive: the hosts a project's workbench needs become an explicit, versioned, reviewable artifact living with that project, exactly like `.hc/workbench-setup.sh` itself. Repeated `propose_framework_change` calls over a project's life accumulate one canonical list instead of scattered PR-body bullets.
- Positive: no new attack surface on the enforcement side - `net-proxy` behaves exactly as it does today.
- Negative: still two explicit human actions per approved change (merge the product-repo PR, then run the sync+rebuild step here) - intentional, not a gap to close later, since the whole point is a deliberate human check before new egress is enabled.
- Dependency: requires PR #494 (merge_story_pr framework/steering branch guard) merged first.
- Follow-up: if the manual sync step becomes the dominant source of toil (e.g. frequent workbench changes across many product repos), revisit via a new ADR toward Option B, with ref-pinning and blast-radius questions resolved explicitly at that time.

## References
- `agents/scrum_team/tools/workflow.py:302-469` (`propose_framework_change`)
- `agents/scrum_team/tools/docs.py` (`.hc/` write-block)
- `config/net-proxy/allowlist.filter`, `net-proxy.Dockerfile`
- `SPEC-ISSUE-0064-...md` (the original JVM/Gradle incident that motivated `propose_framework_change`)
- PR #493 (manual precedent: Gradle hosts added to Horseless Carriage's own allowlist)
- PR #494 (`merge_story_pr` framework/steering branch guard - hard dependency for this ADR) - **merged 2026-11-05, before this ADR's own 2026-11-03 draft date; the dependency this ADR names as blocking it is already resolved.**

## Update (2026-11-10): the "final application step" friction is already partly addressed

A separate, narrower change (PR #516's follow-up) added `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` - a
comma-separated hostname list in Horseless Carriage's own `.env` (gitignored, local-only), merged
into the live allowlist by `entrypoint.sh` on every `net-proxy` container start, idempotently. This
replaces PR #493's manual precedent (hand-editing `config/net-proxy/allowlist.filter` directly,
which this ADR's Context section cites) and is now what `propose_framework_change`'s own PR-body
"HUMAN OPERATOR ACTION REQUIRED" text asks a human to do.

This changes two factual premises above, not the overall decision:
- **Decision item #4** said applying a change "still requir[es] an explicit `docker compose build
  net-proxy` + restart." That's no longer accurate for a `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` addition
  specifically - no rebuild needed, just `docker compose up` (container recreate, not merely
  `restart`, so entrypoint.sh's startup logic actually re-runs). A tracked-file edit to
  `config/net-proxy/allowlist.filter` itself (a genuinely new *default*, not a per-project addition)
  still needs the rebuild step as before.
- **Decision item #5**'s proposed human-invoked sync helper should target `NET_PROXY_EXTRA_
  ALLOWLIST_HOSTS` in `.env`, not append lines to `config/net-proxy/allowlist.filter` directly - the
  whole point of this update is that a per-project addition should never need a tracked-file edit at
  all, the same reasoning Option B's rejection already applied to a live mount.

**Still open, unaddressed by this update:** the actual per-project, versioned, reviewable manifest
this ADR's Decision (`.hc/allowlist.filter` in the product/state repo) and Consequences
("explicit, versioned, reviewable artifact living with that project") describe. Today's
`NET_PROXY_EXTRA_ALLOWLIST_HOSTS` is still a flat, uncommented, single-operator-local list with no
audit trail of which proposal/PR justified each host - exactly the gap this ADR's Option D
(status quo) rejection already named. This update only fixes *how a human applies* an approved
addition, not *what a reviewer can see* about why each host is there. The sync-helper step (#5)
remains the natural place to close that gap once this ADR is picked up.
