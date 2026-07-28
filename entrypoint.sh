#!/bin/bash
set -e

# --- GitHub Authentication ---
# Priority:
# 1. GITHUB_TOKEN (Personal Access Token)
# 2. GitHub App credentials

if [ -n "$GITHUB_TOKEN" ]; then
  echo "Verifying GitHub CLI authentication via GITHUB_TOKEN..."
  # Deliberately NOT calling `gh auth login --with-token` here (as this used
  # to): gh's own precedence rule is that GH_TOKEN/GITHUB_TOKEN, when set in
  # the environment, is used for every gh invocation and "takes precedence
  # over previously stored credentials" (see `gh help environment`) - and
  # GITHUB_TOKEN is always already set in this process's environment by the
  # time this script runs (docker-compose injects it as a container env var
  # before entrypoint.sh starts). `gh auth login --with-token` detects that
  # and refuses immediately with "The value of the GITHUB_TOKEN environment
  # variable is being used for authentication... first clear the value from
  # the environment" - exit 1, EVERY time, for ANY token, valid or not. The
  # old `gh auth login --with-token && gh auth status` therefore always
  # short-circuited on the login step and never even reached `gh auth
  # status`, so this warning could never NOT fire, regardless of whether
  # GITHUB_TOKEN was actually valid. Nothing in this codebase needs the
  # login step's stored keyring credential either: every git/gh call this
  # project makes injects its own auth from GITHUB_TOKEN directly (see
  # agents/scrum_team/tools/base.py's `_run`), so validating GITHUB_TOKEN
  # itself via `gh auth status` (which gh will use directly, per the same
  # precedence rule) is both sufficient and actually informative.
  # Non-fatal: an invalid/expired token shouldn't prevent the container from
  # starting at all. GitHub-dependent tools will simply fail at the point of
  # use instead; everything else (including the test suite) can still run.
  # set -e must be off for this assignment: it's a bare `var=$(...)`, and
  # under set -e a nonzero exit from the command inside would abort the
  # whole script right here - before the diagnostics below ever print.
  set +e
  gh_auth_output="$(gh auth status 2>&1)"
  gh_auth_status=$?
  set -e
  if [ "$gh_auth_status" -eq 0 ]; then
    echo "GitHub CLI authentication successful."
  else
    echo "WARNING: GitHub CLI authentication failed. GitHub tools may fail, but continuing startup."
    echo "--- gh auth status output (real cause) ---"
    echo "$gh_auth_output"
    echo "-------------------------------------------"
  fi
elif [ -n "$GITHUB_APP_ID" ] && [ -n "$GITHUB_APP_PRIVATE_KEY" ] && [ -n "$GITHUB_APP_INSTALLATION_ID" ]; then
  python3 auth_github.py || echo "WARNING: GitHub App authentication failed. GitHub tools may fail, but continuing startup."
else
  echo "WARNING: No GitHub authentication credentials found. GitHub tools may fail."
fi

# --- Git Configuration ---
if [ -n "$GIT_USER_NAME" ]; then
  git config --global user.name "$GIT_USER_NAME"
fi
if [ -n "$GIT_USER_EMAIL" ]; then
  git config --global user.email "$GIT_USER_EMAIL"
fi

# --- Normalize a CRLF-corrupted run_agent.sh before exec ---
# agents/ is bind-mounted read-only (see docker-compose.yaml/
# docker-compose.local.yaml's "Mount the agent's source code" volume) for
# live development, so it always wins over whatever agent.Dockerfile's own
# build-time CRLF stripping produced - on Windows (git's default
# core.autocrlf=true), that's the host's own CRLF-corrupted run_agent.sh,
# which crashes bash immediately ("set: -: invalid option", "$'\r':
# command not found", "syntax error: unexpected end of file"). The
# mount's :ro flag means it can't be fixed in place, so normalize a
# writable copy in /tmp instead and swap it in for exec.
#
# POSIX sh only here (no arrays, no ${@:N} slicing) - the ENTRYPOINT that
# runs this script invokes `sh`, not `bash`, so the "#!/bin/bash" shebang
# above is never consulted at all.
second_arg="$2"
case "$second_arg" in
  /app/agents/scrum_team/scripts/run_agent.sh|agents/scrum_team/scripts/run_agent.sh)
    if sed 's/\r$//' "$second_arg" > /tmp/run_agent.sh 2>/dev/null && chmod +x /tmp/run_agent.sh; then
      second_arg="/tmp/run_agent.sh"
    fi
    ;;
esac
if [ "$#" -ge 2 ]; then
  first_arg="$1"
  shift 2
  set -- "$first_arg" "$second_arg" "$@"
fi

# Execute the main command passed to the container
exec "$@"
