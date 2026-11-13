#!/bin/sh
# Development Workbench: react-vite-firebase (US-0022, EP-0010)
#
# Toolchain for scaffolding a Vite+React frontend backed by Firebase
# Functions + Firestore: installs Node.js, the Firebase CLI (which bundles
# the local emulator suite), and the JRE the Firestore/Auth emulators need
# to run. The base agent image (agent.Dockerfile, FROM python:3.11-slim)
# ships neither Node nor a JRE, so both are installed here from scratch.
set -e

apt-get update
apt-get install -y --no-install-recommends ca-certificates gnupg openjdk-17-jre-headless

# Node.js LTS via NodeSource (base image has no Node at all).
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y --no-install-recommends nodejs

npm install -g firebase-tools

cat <<'EOF'
react-vite-firebase workbench ready: Node.js 20, npm, firebase-tools, and
the JRE the Firestore/Auth emulators need are installed.

Scaffold:
  npm create vite@latest <app-name> -- --template react
  cd <app-name>
  firebase init functions firestore emulators

Install project-declared deps:
  npm install

Test (Vitest - add "vitest" + "@testing-library/react" to package.json if
the scaffold didn't already include them):
  npm run test

Run (Vite dev server):
  npm run dev -- --host 0.0.0.0

Run (Firebase emulator suite - exploratory testing of Functions+Firestore
without touching real Firebase project resources):
  firebase emulators:start --only functions,firestore

Debug:
  node --inspect-brk=0.0.0.0:9229 node_modules/.bin/vite     # frontend/build tooling
  firebase emulators:start --inspect-functions                # Cloud Functions
EOF
