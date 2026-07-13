# Use the main agent image as a base
FROM horseless-carriage-agent:latest

# Install test dependencies
RUN pip install pytest pytest-cov

# The command to run tests will be passed in by the run_tests.sh script
CMD ["pytest"]