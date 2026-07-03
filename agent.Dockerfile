# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Add the pip bin directory to the PATH, which is where pip installs executables
ENV PATH="/usr/local/bin:${PATH}"

# Install git and gh CLI
RUN apt-get update && apt-get install -y git curl
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the agent source code and entrypoint script
COPY ./agents ./agents
COPY entrypoint.sh .

# Set the entrypoint to our script
ENTRYPOINT ["sh", "entrypoint.sh"]

# The command to run when the container starts, which is passed to the entrypoint
# Use a writable directory for the ADK's internal session database
CMD ["adk", "run", "--session_service_uri", "sqlite:////tmp/adk_sessions.db", "agents"]