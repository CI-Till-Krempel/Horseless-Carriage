#!/bin/bash
#
# Setup script for the Horseless Carriage project.
#
# This script will:
# 1. Check for Docker and Docker Compose.
# 2. Guide the user through GitHub CLI setup if needed.
# 3. Set up the .env file.
# 4. Start the database and LiteLLM containers.
#

set -e

echo "--- Running Horseless Carriage Setup ---"

# 1. Check for Docker and Docker Compose
if ! command -v docker &> /dev/null; then
    echo "ERROR: 'docker' command not found. Please install Docker."
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "ERROR: 'docker-compose' or 'docker compose' command not found. Please install Docker Compose."
    exit 1
fi

# 2. Guide the user through GitHub CLI setup
if ! command -v gh &> /dev/null; then
    echo "WARNING: 'gh' command not found."
    echo "The GitHub CLI is recommended for the best experience."
    echo "Please visit https://cli.github.com/ to install it."
elif ! gh auth status &> /dev/null; then
    echo "WARNING: gh CLI is not authenticated."
    echo "Please run 'gh auth login' to authenticate with your GitHub account."
fi

# 3. Set up .env file
if [ ! -f ".env" ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo "IMPORTANT: Please edit the .env file to add your API keys and configuration."
else
    echo ".env file already exists."
fi

# 4. Start the database and LiteLLM containers
echo "Starting database and LiteLLM containers via Docker Compose..."
docker compose up -d db litellm

echo ""
echo "--- Setup Complete ---"
echo "Next steps:"
echo "1. Edit the .env file with your specific API keys and settings."
echo "2. Run the agent using the ./run.sh script."