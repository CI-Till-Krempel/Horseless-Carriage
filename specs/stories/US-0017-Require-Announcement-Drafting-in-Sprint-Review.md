# User Story

- Story ID: US-0017
- Title: Require Announcement Drafting in Sprint Review
- Status: Draft
- Priority: Could
- Owner: ScrumOrchestrator
- Last Updated: 2026-07-16

## As a ScrumOrchestrator, I want `PO_PROMPT` updated to mandate announcement drafting during Sprint Review, so this step isn't silently skipped the way KPIs/changelog were.

## Acceptance Criteria
- Given the Sprint Review & Release phase, when `PO_PROMPT` (in `agents/scrum_team/prompts.py`) is followed, then drafting a customer-facing announcement is an explicit, mandatory step alongside `create_sprint_report`/`create_release_pr`.
- Given a release with no user-facing changes, when the PO follows the prompt, then it still explicitly confirms "no announcement needed" rather than the step being ambiguously skippable.
- Edge case: prompt wording is specific enough that it can't be satisfied by a generic acknowledgment without actually invoking the US-0016 tool.

## Notes
- Parent epic: EP-0005. This is a prompt-level enforcement story, analogous to how EP-0002/EP-0003 exist precisely because prompt instructions alone ("MANDATORY... containing all sprint changes") weren't backed by code enforcement.
- Design/technical notes: touch point is the "SPRINT REVIEW & RELEASE" block of `PO_PROMPT` in `agents/scrum_team/prompts.py`.

## Test Approach
- This is primarily a prompt-content change; verification is by review of the prompt text and, where feasible, an integration test asserting the announcement tool is invoked during a simulated sprint-review pass.
