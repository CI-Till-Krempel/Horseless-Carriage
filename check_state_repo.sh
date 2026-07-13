#!/bin/bash
#
# This script checks if the state repository is in the expected state for the tools to work.
#

set -e

echo "--- Checking State Repository ---"

# 1. Load environment variables from .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
else
    echo "ERROR: .env file not found. Please copy .env.example to .env and fill in the values."
    exit 1
fi

# 2. Check for the existence of the state repository path
if [ -z "$STATE_REPO_PATH" ] || [ ! -d "$STATE_REPO_PATH" ]; then
    echo "ERROR: STATE_REPO_PATH is not set or the directory does not exist: $STATE_REPO_PATH"
    echo "Please create this directory and ensure it is correctly set in your .env file."
    exit 1
fi

echo "State repository found at: $STATE_REPO_PATH"

# 3. Verify Directory Structure
if [ ! -d "$STATE_REPO_PATH/specs" ]; then
    echo "ERROR: The 'specs' directory is missing from the state repository."
    echo "Please create it: mkdir -p $STATE_REPO_PATH/specs"
    exit 1
fi

echo "  [✔] 'specs' directory exists."

# 4. Check for Stray Templates
STRAY_TEMPLATES=$(find "$STATE_REPO_PATH/specs" -name "TEMPLATE-*.md")
if [ -n "$STRAY_TEMPLATES" ]; then
    echo "WARNING: Found template files in the state repository. These should only be in the main project's 'spec-templates' directory."
    echo "Please remove the following files from your state repository:"
    echo "$STRAY_TEMPLATES"
else
    echo "  [✔] No stray templates found in 'specs' directory."
fi

echo ""
echo "--- State Repository Check Complete ---"
echo "The state repository appears to be in a valid state."