# Issue

- Issue ID: ISSUE-0015
- Title: entrypoint.sh's GitHub Auth Check Always Fails, Regardless of Token Validity
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
`entrypoint.sh` authenticated the GitHub CLI with:
```
if echo "$GITHUB_TOKEN" | gh auth login --with-token && gh auth status; then
```
`gh`'s own documented precedence rule (`gh help environment`) is that `GH_TOKEN`/`GITHUB_TOKEN`, when
present in the process environment, is used for every `gh` invocation and "takes precedence over
previously stored credentials." `GITHUB_TOKEN` is always already set in this process's environment
by the time `entrypoint.sh` runs - `docker-compose.yaml`/`docker-compose.local.yaml` inject it as a
container environment variable before the entrypoint starts. `gh auth login --with-token` detects
this and refuses immediately, printing "The value of the GITHUB_TOKEN environment variable is being
used for authentication... first clear the value from the environment" and exiting 1 - confirmed by
direct reproduction: this happens instantly, before any network call, for ANY token value, valid or
not. Because of the `&&`, this made `gh auth status` (the part that actually validates the token)
never run at all. The result: this check could never report success, no matter how valid the
configured token was - exactly the symptom reported by a user with a correctly-scoped, valid
fine-grained PAT.

Nothing in this codebase depends on the stored keyring credential `gh auth login --with-token` would
have produced anyway: every git/gh call this project makes (`agents/scrum_team/tools/base.py`'s
`_run`) injects its own `http.extraheader` Basic-auth header directly from
`tool_context.state["github_token"]` for git commands, and gh CLI subcommands (`gh pr create`, `gh
auth status`, etc.) already pick up `GITHUB_TOKEN` from the environment automatically per the same
precedence rule - so the login step was pure dead weight that actively produced a false negative.

## Acceptance Criteria
- `entrypoint.sh` validates `GITHUB_TOKEN` without a `gh auth login --with-token` step that
  unconditionally fails while the token is present in the environment.
- On success, prints a clear success message; on failure, prints `gh`'s actual output (not a guess
  like "invalid/expired token?") so the real cause is visible.
- Remains non-fatal either way - the container still starts and reaches `exec "$@"`.
- Verified by reproducing both paths locally with the real `gh` binary (or a stub matching its
  actual CLI behavior): a stubbed successful `gh auth status` reports success and falls through to
  `exec`; a stubbed failing one prints the real diagnostic and still falls through.

## Notes
- Where the gap lived: `entrypoint.sh` lines 9-18 (the `if echo ... | gh auth login --with-token &&
  gh auth status; then` block).
- Confirmed directly against the installed `gh` CLI (v2.96.0): `gh auth login --with-token` with
  `GITHUB_TOKEN` already set in the environment fails instantly with the "environment variable is
  being used for authentication" notice, distinct from (and reproducible without) any network error.
- Complements ISSUE-0014 (tool/setup errors must be surfaced, not swallowed) - the fix here is the
  entrypoint-level instance of that same principle: don't print a generic guess ("invalid/expired
  token?") when the tool's own error message names the real cause.

## Test Approach
- Not unit-testable via `pytest` (shell script executed at container start, outside the Python test
  suite); verified by direct reproduction: isolating `gh auth login --with-token` vs `gh auth
  status` with and without `GITHUB_TOKEN` pre-set in the environment (confirms the always-fails
  mechanism), then running the fixed `entrypoint.sh` end-to-end with a stubbed `gh` on `PATH` for
  both a simulated valid and invalid token, confirming the correct message and exit-fallthrough in
  each case.

## Resolution
- `entrypoint.sh`: removed the `gh auth login --with-token` call entirely; now validates directly
  via `gh auth status` (which uses `GITHUB_TOKEN` from the environment automatically, per `gh`'s own
  precedence rule) and prints its real output on failure instead of assuming the cause.
- Guarded the output-capturing assignment with `set +e`/`set -e` around it, since a bare
  `var=$(...)` assignment is itself subject to `set -e` at the top of the script - without this, a
  failing `gh auth status` would abort the whole script before the diagnostic could print.
