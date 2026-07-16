# Epic

- Epic ID: EP-0003
- Title: Enforce Full Sprint Increment in Release PRs
- Status: Draft
- Priority: Must
- Owner: Product Owner
- Last Updated: 2026-07-16

## Overview
`create_release_pr()` in `agents/scrum_team/tools/github.py` pushes and opens a PR by relying on `git_push(add_all=True)` to pick up "whatever is in the working tree" at call time. `prompts.py` instructs the LLM to bundle all sprint changes into this PR, but nothing in code verifies that the PR actually contains the complete sprint increment. This epic closes that gap so a release PR can never silently drop completed work.

## User Stories / Features
- US-0009 — Track Sprint-Touched Files
- US-0010 — Verify Release PR Diff Against Sprint Tracking
- US-0011 — Block Release on Uncommitted Sprint Work

## Acceptance Criteria
- Every file written or committed during a sprint is recorded in state as it happens.
- `create_release_pr()` cross-checks the actual git diff against that tracked list and surfaces a clear warning/failure on mismatch.
- Uncommitted or untracked sprint-related files are detected and flagged before a release PR is opened.

## Notes
- Primary touch points: `agents/scrum_team/tools/github.py` (`create_release_pr`, `git_push`), and every write path in `agents/scrum_team/tools/docs.py` / `agents/scrum_team/tools/requirements.py` (these need to record what they touched).
- Requires a new `sprint_files_touched`-style field in `ScrumState` (`agents/scrum_team/state.py`).
- This epic is a prerequisite for EP-0004 (Changelog Generation) — a changelog can only be trusted once "what's actually in this release" is verified rather than assumed.

## Roadmap
- Targeted for v0.1 — Trust & Integrity Fixes (see `specs/ROADMAP.md`).
