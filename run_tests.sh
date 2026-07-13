#!/bin/bash
#
# This script builds the test stage of the Dockerfile and runs pytest.
#

set -e

echo "--- Building and Running Tests ---"

# Build the test image from the 'test' stage of the agent.Dockerfile
docker build --target test -f agent.Dockerfile -t horseless-carriage-test .

# Run the tests using the built image
docker run --rm horseless-carriage-test