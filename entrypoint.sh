#!/bin/bash
set -e

# --- GitHub Authentication ---
# Priority:
# 1. GITHUB_TOKEN (Personal Access Token)
# 2. GitHub App credentials

if [ -n "$GITHUB_TOKEN" ]; then
  echo "Authenticating GitHub CLI with GITHUB_TOKEN..."
  # Non-fatal: an invalid/expired token shouldn't prevent the container from
  # starting at all. GitHub-dependent tools will simply fail at the point of
  # use instead; everything else (including the test suite) can still run.
  if echo "$GITHUB_TOKEN" | gh auth login --with-token && gh auth status; then
    echo "GitHub CLI authentication successful."
  else
    echo "WARNING: GitHub CLI authentication failed (invalid/expired token?). GitHub tools may fail, but continuing startup."
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
