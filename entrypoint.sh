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
  gh auth status
elif [ -n "$GITHUB_APP_ID" ] && [ -n "$GITHUB_APP_PRIVATE_KEY" ] && [ -n "$GITHUB_APP_INSTALLATION_ID" ]; then
  python3 auth_github.py
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

# Execute the main command passed to the container
exec "$@"
