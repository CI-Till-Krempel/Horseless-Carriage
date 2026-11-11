# User Story

- Story ID: US-0022
- Title: Ship Three Reference Framework Workbenches (Flask, Kotlin Multiplatform, React+Firebase)
- Status: Draft
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## As an Architect, I want ready-made workbench templates for Python+Flask, Kotlin Multiplatform (Android & iOS), and React(Vite)+Firebase Functions+Firestore, so that projects on these stacks get a working scaffold/dev/test/debug toolchain without anyone authoring it from scratch.

## Acceptance Criteria
- Given the `python-flask` template, when selected, then the resulting workbench can scaffold a new Flask app, install dependencies, run its test suite, run the app, and support both debugging (e.g. `debugpy` or equivalent) and exploratory testing (e.g. an HTTP client or Flask shell) — plus an MCP config and a network allowlist (at minimum PyPI) declared for it.
- Given the `kotlin-multiplatform` template, when selected, then the resulting workbench can scaffold a KMP module targeting Android & iOS, install the required JDK/Android SDK/Kotlin toolchain, run its test suite (Gradle test tasks for both targets), and run/debug the Android target. iOS build/run realistically requires a macOS host with Xcode — this constraint is documented explicitly rather than silently promised as working inside the Linux-container workbench.
- Given the `react-vite-firebase` template, when selected, then the resulting workbench can scaffold a Vite+React app with Firebase Functions+Firestore, install dependencies, run its test suite, run the dev server and the Firebase emulator suite for exploratory testing, and support debugging — plus an MCP config and a network allowlist (npm registry, Firebase/Google Cloud endpoints) declared for it.
- Each template conforms to the directory/file contract defined by US-0021 (toolchain script, MCP config, network allowlist, each independently reviewable).

## Notes
- Parent epic: EP-0010.
- Depends on US-0021 shipping the template mechanism first — these are instances of that contract, not a separate mechanism.
- iOS is the one stack-specific constraint worth calling out up front: the agent's current Docker-based model is Linux-container-first, so the `kotlin-multiplatform` template should generate iOS-side scaffolding/config but document that actual iOS build/run/debug is host-dependent (macOS + Xcode), not something the workbench container itself can do.
- Design/technical notes: each template directory lives under `config/workbench-templates/<stack-name>/`, following US-0021's contract.

## Test Approach
- Per template: a smoke test that scaffolds a minimal app from the template and runs its test suite inside the resulting workbench image (CI or scripted check).
- Unit tests asserting each template's declared MCP config and allowlist hosts are well-formed and non-empty.
- Explicit test/doc check that the `kotlin-multiplatform` template's iOS limitation is surfaced (not silently dropped) wherever the template's capabilities are described to the Architect.
