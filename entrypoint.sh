#!/bin/bash
set -e

# --- GitHub Authentication ---
# Priority:
# 1. GITHUB_TOKEN (Personal Access Token)
# 2. GitHub App credentials

if [ -n "$GITHUB_TOKEN" ]; then
  echo "Authenticating GitHub CLI with GITHUB_TOKEN..."
  echo "$GITHUB_TOKEN" | gh auth login --with-token
  echo "GitHub CLI authentication successful."
elif [ -n "$GITHUB_APP_ID" ] && [ -n "$GITHUB_APP_PRIVATE_KEY" ] && [ -n "$GITHUB_APP_INSTALLATION_ID" ]; then
  echo "Authenticating GitHub CLI with GitHub App credentials..."
  # The gh auth login --app command is not yet available in the stable release.
  # We will use the token generation logic from the tools.
  # This is a temporary workaround until the gh CLI supports this natively.
  echo "WARNING: GitHub App authentication via entrypoint is not yet fully implemented."
  echo "Please use a GITHUB_TOKEN for now."
else
  echo "WARNING: No GitHub authentication credentials found. GitHub tools may fail."
fi

gh auth status

# Execute the main command passed to the container
exec "$@"
