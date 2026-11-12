#!/usr/bin/env bash
# Smoke test for the react-vite-firebase Development Workbench template
# (US-0022).
#
# NOT run by `python3 run_tests.py` / `pytest agents/scrum_team/tests`: that
# suite runs inside the `agent` Docker Compose service, which sits on
# agent-net (internal, egress restricted to github.com/api.github.com by
# net-proxy's allowlist - see docker-compose.yaml and
# config/net-proxy/allowlist.filter). This script needs real internet
# access to registry.npmjs.org (npm installs) and deb.nodesource.com (Node
# install), which that sandboxed test container deliberately cannot reach.
#
# Run this manually, or wire it into a dedicated CI job with real internet
# access (NOT routed through net-proxy) on a host with Docker:
#   config/development-workbench-templates/react-vite-firebase/smoke-test.sh
#
# What it does: builds a throwaway image using this template's own
# workbench-setup.sh (installing Node.js + firebase-tools, exactly as
# agent.Dockerfile's COPY+RUN does for a real project), scaffolds a minimal
# Vite+React app inside it, adds a trivial Vitest test, and runs the test
# suite - proving the template's install/scaffold/test path works end to
# end.
#
# Scope note: this intentionally does NOT boot the Firebase emulator suite
# (firestore/functions emulators are JVM processes that download and cache
# several hundred MB on first run) - that capability is exercised by
# running `firebase emulators:start --only functions,firestore` by hand
# per workbench-setup.sh's own printed instructions, not as part of this
# fast smoke check.
set -euo pipefail
cd "$(dirname "$0")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/Dockerfile" <<'DOCKERFILE'
FROM python:3.11-slim
WORKDIR /app
COPY workbench-setup.sh /tmp/workbench-setup.sh
RUN chmod +x /tmp/workbench-setup.sh && /tmp/workbench-setup.sh
DOCKERFILE
cp workbench-setup.sh "$WORK/workbench-setup.sh"

docker build -t hc-smoke-react-vite-firebase "$WORK"

docker run --rm hc-smoke-react-vite-firebase sh -c '
  set -e
  npm create vite@latest app -- --template react -y >/dev/null
  cd app
  npm install --no-fund --no-audit >/dev/null
  npm install --no-fund --no-audit --save-dev vitest @testing-library/react jsdom >/dev/null
  mkdir -p src/__tests__
  cat > src/__tests__/smoke.test.jsx <<PY
import { describe, it, expect } from "vitest";

describe("smoke", () => {
  it("adds numbers", () => {
    expect(1 + 1).toBe(2);
  });
});
PY
  npx vitest run
'
echo "react-vite-firebase smoke test passed: scaffolded app installed, tested, and ran successfully."
