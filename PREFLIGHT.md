# Pre-flight Checklist

This document provides a checklist to ensure your environment is correctly set up before running the Horseless Carriage agent.

## 1. Environment Setup

- [ ] **Docker is installed:**
  - Run `docker --version` to verify.
- [ ] **Docker Compose is installed:**
  - Run `docker compose version` to verify.

## 2. Configuration

- [ ] **`.env` file exists:**
  - If not, run `./setup.sh` to create it from `.env.example`.
- [ ] **API keys are set:**
  - Open the `.env` file and ensure that you have set at least one provider API key (e.g., `OPENAI_API_KEY`, `GEMINI_API_KEY`).
- [ ] **`LITELLM_MASTER_KEY` is set:**
  - This is required for the LiteLLM proxy.

## 3. Services

- [ ] **Docker is running:**
  - Run `docker ps` to verify.
- [ ] **`litellm-proxy` container is running:**
  - Run `docker ps | grep litellm-proxy` to verify. If it's not running, run `docker compose up -d`.

## 4. Authentication

- [ ] **`gh` CLI is installed:**
  - Run `gh --version` to verify.
- [ ] **`gh` CLI is authenticated:**
  - Run `gh auth status` to verify. If you're not logged in, run `gh auth login`.

## 5. Validation

- [ ] **Run the doctor script:**
  - Run `./doctor.sh` to validate your setup. Address any errors before proceeding.

Once all these checks have passed, you are ready to run the agent using `./run.sh`.
