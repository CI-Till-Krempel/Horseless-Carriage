# Issue

- Issue ID: SPEC-ISSUE-0100
- Title: setup_all.py never configures GitHub authentication, then calls the setup "ready to start"
- Status: Done
- Priority: Must
- Owner: Horseless Carriage maintainers
- Last Updated: 2026-11-25

## Overview

`setup_all.py` is the guided, first-time setup path. README.md and docs/SETUP.md present it as
running "every setup step in order". The agent can't do anything with the target repo without
GitHub credentials: no issues, no PRs, no pushes. But no step in the guided flow ever configured
them. Before this fix, on `origin/main` at `85d12b4`:

- `setup_all.py:194-204`: `main()` runs `setup_project.main`, then `run_doctor_gate(repo_root)`,
  then `offer_to_start(dev)`. No step touches `GITHUB_TOKEN` or `GITHUB_APP_*`.
- `doctor.py:414-420`: missing GitHub auth is only a `warn(...)`
  (`"No GitHub authentication method fully configured in .env."`), not an `error(...)`. So
  `run_doctor_gate` (which loops only on `result.has_errors`) passes straight through.
- `setup_all.py:132`: `offer_to_start` then prints `"--- Ready to start the agent ---"` and
  defaults `"Start the agent now?"` to yes.
- `setup_project.py:129` (a step of the same flow) ends with
  `"2. Run the agent using: python3 run.py"` and never mentions GitHub auth.
- The automated GitHub App flow already existed as a standalone script (`setup_github_app.py`),
  but `setup_all.py` couldn't call it as-is. Its `main()` called `parser.parse_args()` on the real
  `sys.argv` (`setup_github_app.py:284`), so `setup_all.py --dev` would have crashed argparse, and
  it hardcoded `Path(".env")` (`setup_github_app.py:286`) rather than the `.env` `setup_all.py`
  checked.

Reported as GH issue #565: "The setup_all script does not add the github authentication, and even
suggests to start the agent without this required setup. The user should be able to choose either
the app authentication or the use of a github pat."

## Acceptance Criteria

- `setup_all.py` has a GitHub auth step after `setup_project.py` (which creates `.env`) and before
  the doctor gate.
- The step is skipped, with no prompt, when `.env` already has `GITHUB_TOKEN` or all three of
  `GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/`GITHUB_APP_INSTALLATION_ID`. This is the same rule
  `doctor.py` and `lib_github.github_app_fully_configured` use. A partial App config doesn't count.
- Otherwise the user chooses one of three options:
  1. GitHub App: runs `setup_github_app.main([], env_path=<that .env>)`.
  2. Personal Access Token: read with `getpass`, written only to the git-ignored `.env` via
     `lib_env.update_env_var`, then checked with `lib_github.check_repo_access` when
     `GITHUB_REPO_URL` parses.
  3. Skip.

  If the chosen option didn't produce working config, the step offers a retry.
- Non-interactive-safe: with no TTY on stdin, or with `--skip-github-auth`, the step never calls
  `input()`/`getpass()`. It prints how to configure auth and continues.
- If auth is still missing at the end, `offer_to_start` doesn't print "Ready to start the agent".
  It prints a warning that names both options, and "Start the agent now?" defaults to no.
- `setup_project.py`'s "Next steps" mentions GitHub auth before "Run the agent".
- Tests in `tests/test_setup_all.py` (`TestEnsureGithubAuth`, plus
  `TestMain::test_github_auth_step_runs_after_setup_project_before_doctor_gate`) fail on the old
  code and pass on the new.

## Notes

- GH issue #565.
- `doctor.py`'s severity for missing auth is unchanged (still a warning). Making it an ERROR would
  block `run.py`'s own gate for every existing installation that deliberately runs without auth
  (e.g. a local experiment). That's a broader behavior change than this issue asks for, so the
  guided flow handles it instead.
- No real secret is written to any tracked file. The token goes only into `.env`, which is
  git-ignored (see SECURITY.md "Secret handling"). Tests use dummy values in `tmp_path`.
- GH #562 (`add_project` running `setup_all` in the wrong directory) is being fixed separately.
  This change keeps to the auth step. It resolves `.env` as `repo_root / ".env"`, the same root
  `run_doctor_gate` already uses.

## Test Approach

- Unit tests with `monkeypatch`ed `input`/`getpass`/`setup_github_app.main`/
  `lib_github.check_repo_access` against a `tmp_path` `.env`. They cover: already configured
  (token, App); partial App config; missing `.env`; no TTY; `--skip-github-auth`; PAT written and
  verified; App flow invoked with the right `env_path` and empty argv; failed App flow followed by
  a declined retry; explicit skip; `offer_to_start` warning and defaulting to not start.
- `TestMain` checks the order setup_project -> GitHub auth -> doctor, and that the result reaches
  `offer_to_start(github_auth_ok=...)`.

## Resolution

Implemented in `setup_all.py` (`github_auth_configured`, `ensure_github_auth`, `_setup_github_pat`,
`offer_to_start(github_auth_ok=...)`, `--skip-github-auth`), `setup_github_app.py`
(`main(argv=None, env_path=None)`), and `setup_project.py`'s next-steps text. Documented in
docs/GITHUB-INTEGRATION.md, docs/SETUP.md, README.md and announcements/unreleased-changelog.md.
