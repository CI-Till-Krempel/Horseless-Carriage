# User Story

- Story ID: US-0024
- Title: Check Roadmap/GitHub Milestone Sync
- Status: Done
- Priority: Should
- Owner: DevOps
- Last Updated: 2026-11-12

## As a maintainer, I want an automated check that flags any open GitHub issue whose milestone disagrees with (or is missing from) `specs/ROADMAP.md`'s per-version story lists, so that drift between the roadmap and real GitHub milestones gets caught instead of silently accumulating until someone notices by hand.

## Acceptance Criteria
- Given `specs/ROADMAP.md`'s "### vX.Y ..." sections and the real GitHub issues in this repo, when `python3 check_roadmap_sync.py` runs, then it reports every open issue whose actual milestone doesn't match where (or whether) that issue is listed in the roadmap, and exits non-zero if any mismatch is found.
- Given an open issue that carries a GitHub milestone but isn't listed under any roadmap version section, when the check runs, then it's flagged as `missing_from_roadmap`.
- Given a roadmap entry under a version section whose issue has no GitHub milestone set, when the check runs, then it's flagged as `roadmap_only`.
- Given a roadmap entry under one version whose issue's actual GitHub milestone is a different version, when the check runs, then it's flagged as `milestone_mismatch` (this is the exact drift already observed in practice: GH-513/GH-515 carried the `v0.3.0` milestone before anyone added them to the v0.3 section; GH-456 was milestoned `v0.3.0` but stayed listed under v0.5 until fixed by hand).
- Given an issue listed only under a non-version section (`Research backlog`, `Backlog`), when the check runs, then it's never flagged regardless of its actual GitHub milestone — there's no single "expected" milestone for exploratory/unplanned work.
- Given `gh` isn't installed/authenticated or the GitHub API call fails, when the check runs, then it exits 0 (skips) rather than hard-failing CI on a transient/environmental problem.
- The check runs in CI (`.github/workflows/roadmap-sync.yml`) on PRs touching `specs/ROADMAP.md`, and on a daily schedule plus manual dispatch (so drift introduced purely on the GitHub side — a milestone changed with no accompanying PR — is still caught).
- `specs/ROADMAP.md`'s "How to use" section documents this convention and the checker, so the manual-update discipline (move a `[GH-NNN]` reference when its milestone changes) is written down even though this story doesn't attempt full bidirectional automation.

## Notes
- Resolves GH-272 ("Sync Github Issues and versions to roadmap").
- Source of truth stays `specs/ROADMAP.md`, per GH-272's own PO comment — this script only detects drift, it never writes to GitHub or edits the roadmap. A human still fixes whichever side is wrong.
- Scoped to the outbound "detect drift" direction only. GH-272's own comments flag the larger "sync real external GitHub issues into the backlog as trackable items" direction, and a `fix_version` field on the backlog-item schema (`agents/scrum_team/state.py`), as materially bigger, separable follow-up scope — not attempted here.
- `check_roadmap_sync.py` is a stdlib + `gh`-CLI host script, consistent with `doctor.py`/`watch_roadmap.py`/`check_state_repo.py`'s existing conventions (no ADK/pydantic dependency).

## Test Approach
- `tests/test_check_roadmap_sync.py`: unit tests for `parse_roadmap_entries`/`parse_roadmap_versions`/`parse_roadmap_non_version_ids` (markdown parsing, including the Research-backlog exemption), `version_to_milestone_title`, `fetch_open_issues_with_milestones` (mocked `gh` subprocess output: success, not-installed, non-zero exit, invalid JSON), `compute_drift` (all three drift kinds, matching case, exempt ids, closed issues never appearing), and `main`'s CLI wiring (exit codes, printed output). Also includes a regression test that parses the real `specs/ROADMAP.md` and asserts GH-513/GH-515/GH-456 are under `v0.3`.
- Verified against the real repo (`python3 check_roadmap_sync.py --repo CI-Till-Krempel/Horseless-Carriage`) during development — confirmed it reports no drift post-fix, and caught the GH-96/97/178 false positive (research-backlog issues milestoned `v0.8.0`) before the exemption was added.
