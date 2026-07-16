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

# 5. Validate state.json structure
STATE_FILE="$STATE_REPO_PATH/.hc/state.json"
if [ -f "$STATE_FILE" ]; then
    echo "--- Validating state.json ---"
    
    # First attempt: Run via Docker to ensure all dependencies (pydantic) are present
    if command -v docker >/dev/null 2>&1 && [ -f "docker-compose.yaml" ]; then
        echo "Running validation via Docker..."
        docker compose run --rm agent python3 agents/scrum_team/scripts/validate_state.py /app/state_repo/.hc/state.json
        VALIDATION_EXIT_CODE=$?
    elif command -v python3 >/dev/null 2>&1; then
        echo "Running validation via local python3..."
        python3 agents/scrum_team/scripts/validate_state.py "$STATE_FILE"
        VALIDATION_EXIT_CODE=$?
    else
        echo "WARNING: Could not find Docker or python3 to validate state.json."
        VALIDATION_EXIT_CODE=0
    fi

    if [ $VALIDATION_EXIT_CODE -ne 0 ]; then
        echo "ERROR: state.json validation failed. Your state might be corrupted or outdated."
        echo "If you recently updated the project, you might need to manually fix the state.json"
        echo "or initialize a new one."
        exit 1
    fi
else
    echo "INFO: .hc/state.json not found. This is normal if the agent hasn't run yet."
fi

echo ""
echo "--- State Repository Check Complete ---"
echo "The state repository appears to be in a valid state."