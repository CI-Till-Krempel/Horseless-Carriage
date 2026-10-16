# --- Base Stage ---
FROM python:3.11-slim AS base

WORKDIR /app

# GH issue #95: with no locale set, this base image reads as the "C"
# locale, and Python then decodes the interactive CLI's stdin (piped
# through from `docker compose run`'s tty) using 'surrogateescape' - any
# multi-byte UTF-8 character a user types/pastes (smart quotes, em-dashes,
# accents) gets mangled into a lone surrogate codepoint. That survives
# fine as a Python str right up until the ADK session service tries to
# json-serialize it for storage, where it crashes the whole run with
# UnicodeEncodeError: "surrogates not allowed". C.UTF-8 is a glibc
# built-in pseudo-locale (no `locales` package/locale-gen needed) that
# makes Python decode stdin as real UTF-8 from the start, so those
# characters round-trip correctly instead of becoming surrogates.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=UTF-8 \
    PYTHONUTF8=1

# Install system dependencies
RUN apt-get update && apt-get install -y git curl
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y

# SPEC-ISSUE-0064: per-project "workbench" - whatever toolchain THIS product's
# tech stack needs (JDK, Node, Go, ...), as a plain shell script the
# Architect persists at .hc/workbench-setup.sh in the PRODUCT repo (via
# propose_framework_change - see agents/scrum_team/tools/workflow.py),
# never horseless-carriage's own. A per-language ARG (the previous
# INSTALL_JDK) doesn't generalize - every new stack would need its own flag
# and a horseless-carriage code change forever. `workbench` is a named
# Docker build context (docker-compose's build.additional_contexts, see
# docker-compose.yaml) pointed at the product repo's .hc/ directory -
# ensure_workbench_script_default (check_state_repo.py) guarantees
# workbench-setup.sh always exists there (a harmless placeholder until the
# Architect customizes it), so this COPY never fails the build. This only
# ever runs commands a human has already merged via that gated PR flow -
# nothing here is agent-writable at runtime.
COPY --from=workbench workbench-setup.sh /tmp/workbench-setup.sh
RUN chmod +x /tmp/workbench-setup.sh && /tmp/workbench-setup.sh

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
COPY ./eval ./eval
COPY auth_github.py .
COPY entrypoint.sh .
COPY VERSION .
# Strip any CRLF line endings: on Windows, git's default core.autocrlf=true
# checks these scripts out with CRLF, which corrupts lines like "set -e"
# into "set -e\r" - `sh`/`bash` then reads the trailing \r as part of the
# option string and fails immediately ("entrypoint.sh: 2: set: Illegal
# option -"). Normalizing here fixes it unconditionally, regardless of the
# checkout's line endings (see also .gitattributes, which prevents CRLF
# from being checked out here at all on a fresh clone - this still covers
# an already-cloned working tree).
RUN sed -i 's/\r$//' entrypoint.sh agents/scrum_team/scripts/run_agent.sh
RUN chmod +x agents/scrum_team/scripts/run_agent.sh

# Set the entrypoint for the production container
ENTRYPOINT ["sh", "entrypoint.sh"]

# The command to run when the container starts will be provided by docker-compose
CMD ["/bin/bash", "agents/scrum_team/scripts/run_agent.sh"]