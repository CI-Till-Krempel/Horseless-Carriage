# --- Base Stage ---
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git curl
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y

# Set the PATH for pip executables
ENV PATH="/root/.local/bin:${PATH}"

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Test Stage ---
FROM base AS test

# Set the PYTHONPATH to the project root
ENV PYTHONPATH="/app"

# Install test-specific dependencies
RUN pip install pytest pytest-cov

# Copy source code and tests
COPY . .

# Default command to run tests with coverage
CMD ["pytest", "--cov=agents", "agents/scrum_team/tests"]


# --- Final Stage (Production) ---
FROM base AS final

# Copy only the necessary application code from the base stage
COPY --from=base /app /app
COPY ./agents ./agents
COPY ./spec-templates ./spec-templates
COPY auth_github.py .
COPY entrypoint.sh .
RUN chmod +x agents/scrum_team/scripts/run_agent.sh

# Set the entrypoint for the production container
ENTRYPOINT ["sh", "entrypoint.sh"]

# The command to run when the container starts will be provided by docker-compose
CMD ["/bin/bash", "agents/scrum_team/scripts/run_agent.sh"]