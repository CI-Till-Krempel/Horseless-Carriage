#!/bin/bash
#
# Doctor script for the Horseless Carriage project.
#
# This script will:
# 1. Check for Docker and Docker Compose.
# 2. Check if .env exists and contains essential variables.
# 3. Check if the STATE_REPO_PATH directory exists.
# 4. Check gh CLI authentication.
#

set -e

echo "--- Running Horseless Carriage Doctor (Docker Edition) ---"

# 1. Check for Docker and Docker Compose
if ! command -v docker &> /dev/null; then
    echo "ERROR: 'docker' command not found. Please install Docker."
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "ERROR: 'docker-compose' or 'docker compose' command not found. Please install Docker Compose."
    exit 1
fi

# 2. Check .env file
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Please copy .env.example to .env and fill in the values."
    exit 1
fi

# Load environment variables to check them
set -a
source .env
set +a

if [ -z "$LITELLM_MASTER_KEY" ]; then
    echo "ERROR: LITELLM_MASTER_KEY is not set in .env. Please set it."
    exit 1
fi

if [ -z "$STATE_REPO_PATH" ]; then
    echo "ERROR: STATE_REPO_PATH is not set in .env. Please set it."
    exit 1
fi

# 3. Check GitHub configuration
if [ -z "$GITHUB_REPO_URL" ]; then
    echo "WARNING: GITHUB_REPO_URL is not set in .env. The agent might not know which repository to use."
fi

if [ -n "$GITHUB_TOKEN" ]; then
    echo "GitHub Authentication: Using Personal Access Token."
elif [ -n "$GITHUB_APP_ID" ] && [ -n "$GITHUB_APP_PRIVATE_KEY" ] && [ -n "$GITHUB_APP_INSTALLATION_ID" ]; then
    echo "GitHub Authentication: Using GitHub App."
else
    echo "WARNING: No GitHub authentication method fully configured in .env."
    echo "Please set either GITHUB_TOKEN or (GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID)."
fi

# 4. Check if the STATE_REPO_PATH directory exists
if [ ! -d "$STATE_REPO_PATH" ]; then
    echo "ERROR: The directory specified by STATE_REPO_PATH does not exist: $STATE_REPO_PATH"
    echo "Please create this directory before running the agent."
    exit 1
fi

# 4. Check gh CLI authentication (still useful for local development and setup)
if ! command -v gh &> /dev/null; then
    echo "WARNING: 'gh' command not found. This may be needed for initial GitHub setup."
else
    if ! gh auth status &> /dev/null; then
        echo "WARNING: gh CLI is not authenticated. Please run 'gh auth login' if you need to interact with GitHub locally."
    fi
fi

echo ""
echo "--- Doctor Check Complete ---"
echo "Setup looks good. You can now run the agent with ./run.sh"