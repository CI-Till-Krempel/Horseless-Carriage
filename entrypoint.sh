#!/bin/bash
set -e

# Authenticate with GitHub CLI if a token is provided
if [ -n "$GITHUB_TOKEN" ]; then
  echo "Authenticating GitHub CLI with provided token..."
  echo "$GITHUB_TOKEN" | gh auth login --with-token
  echo "GitHub CLI authentication successful."
  gh auth status
else
  echo "WARNING: GITHUB_TOKEN environment variable not set. GitHub tools requiring authentication may fail."
fi

# Execute the main command passed to the container
exec "$@"
