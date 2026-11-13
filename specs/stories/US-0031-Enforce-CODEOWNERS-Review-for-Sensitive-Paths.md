# User Story

- Story ID: US-0031
- Title: Enforce CODEOWNERS Review for Sensitive Paths
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-12

## As a repo maintainer, I want a `CODEOWNERS` file naming required reviewers for sensitive, process-critical paths, so human review is required for the files that already carry a "human must look at this" convention elsewhere in this project.

## Acceptance Criteria
- Given `.github/CODEOWNERS`, when it's read, then it designates the real repo owner (`@CI-Till-Krempel`, confirmed via `gh api repos/CI-Till-Krempel/Horseless-Carriage --jq .owner.login`) - not a placeholder - as required reviewer for: `.github/workflows/` (CI/release automation), the CODEOWNERS file itself, `config/github_config.yaml` (declarative repo-policy notes), `config/net-proxy/` (sandboxed dev/eval network allowlist), the `.env*.example`/`.env.test` templates, and `RELEASE.md`/`SECURITY.md`.
- Given each CODEOWNERS entry, when read, then it carries a one-line comment explaining why that path needs a human review.
- Given `gh api repos/CI-Till-Krempel/Horseless-Carriage/codeowners/errors` (read-only), when run against this file, then it reports zero parse errors.
- Given this file alone, when it ships, then it has **no merge-blocking effect** - GitHub only enforces required-reviewer rules once "Require review from Code Owners" is also turned on as a branch protection rule for `main` (Settings → Branches → branch protection rule → "Require review from Code Owners"). This story explicitly does **not** flip that toggle via the GitHub API (repo-wide blast radius, affects every open PR immediately) - it's documented as the one remaining manual step for a human repo-admin, in `docs/GITHUB-INTEGRATION.md`, `docs/CONFIGURATION.md`, and the shipping PR body.

## Notes
- Origin: GH-316.
- This repo already had a strong notion of "files that require human approval" before this story: the Architect's `propose_framework_change`/`propose_steering_change` tools always open a human-reviewed PR for `.hc/`-path and role-identity/steering-doc changes in *target* projects; `docs.py`'s write-block refuses direct writes to `.hc/` paths; ADR-0002 covers network-allowlist proposals the same way. CODEOWNERS adds the equivalent guardrail at the GitHub-review layer for *this* framework repo's own sensitive files.
- `config/github_config.yaml` already existed as a human-facing, non-enforced notes file for intended branch-protection policy; this story adds a `require_code_owner_reviews` note there pointing back at the manual toggle, rather than introducing a second, conflicting source of truth.
- Scope was deliberately limited to paths that exist in *this* repo today (`.hc/`, role-identity docs, and workbench templates are generated per target-project, not present at this repo's own root, so they weren't added as CODEOWNERS entries here).

## Test Approach
- `tests/test_codeowners.py`: asserts the file exists and is non-empty, every entry names a real (non-placeholder) GitHub owner, every pattern resolves to a path that actually exists in the repo (typo guard), and the specific sensitive categories named in GH-316 (`.github/workflows`, `config/net-proxy`, `.env*`) are represented.
- Manually verified `gh api repos/CI-Till-Krempel/Horseless-Carriage/codeowners/errors` returns zero errors for the shipped file.
- Docs-only changes (GITHUB-INTEGRATION.md, CONFIGURATION.md, github_config.yaml comments) verified via PR review, consistent with this repo's Definition of Done.
