#!/bin/bash
#
# Setup script for the Horseless Carriage project.
#
# This script will:
# 1. Create a Python virtual environment.
# 2. Install required dependencies.
# 3. Set up the .env file.
# 4. Start the LiteLLM Docker container.
#

set -e

echo "--- Running Horseless Carriage Setup ---"

# Check for Python 3
if ! command -v python3 &> /dev/null
then
    echo "ERROR: python3 could not be found. Please install Python 3."
    exit 1
fi

# 1. Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment in ./.venv..."
    python3 -m venv .venv
else
    echo "Virtual environment ./.venv already exists."
fi

# Activate virtual environment for this script's context
source .venv/bin/activate

# 2. Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# 3. Set up .env file
if [ ! -f ".env" ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo "IMPORTANT: Please edit the .env file to add your API keys and configuration."
else
    echo ".env file already exists."
fi

# 4. Start LiteLLM proxy via Docker
if ! command -v docker &> /dev/null
then
    echo "WARNING: 'docker' command not found. Cannot start the LiteLLM proxy."
    echo "Please install Docker and run 'docker compose up -d' manually."
    exit 0
fi

echo "Starting LiteLLM proxy via Docker Compose..."
docker compose up -d

echo ""
echo "--- Setup Complete ---"
echo "Next steps:"
echo "1. Edit the .env file with your specific API keys and settings."
echo "2. Run the agent using the ./run.sh script."
