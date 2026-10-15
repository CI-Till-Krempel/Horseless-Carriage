# Issue

- Issue ID: ISSUE-0064
- Title: Test-Suite Coverage Hardcoded Pytest, Blocking Every JVM Target Project
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-15

## Overview
A live installation (a target project using a JVM build tool, nested per the documented
"horseless-carriage as a submodule of the managed repo" install pattern) had its
`advance_story_stage` Tested gate fail with a collection error: `ModuleNotFoundError: No module
named 'agents'` in `horseless-carriage/agents/scrum_team/tests/conftest.py`. QA raised this as a
blocker requiring a human operator to fix `PYTHONPATH`/install the package at the container level.

**Confirmed root cause - two distinct bugs, not one.** First: `agents/scrum_team/tools/
quality.py::_execute_test_suite_coverage_impl` ran a bare `pytest --cov ...` against the whole
`STATE_REPO_PATH` with no `--ignore`. Nesting `horseless-carriage/` as a submodule *inside* the
managed repo is the documented, intended install pattern (`new_project.py`'s own
`_install_if_nested`/`_configure_for_target_project`) - so pytest's default recursive collection
swept up the harness's own internal test suite (`horseless-carriage/agents/scrum_team/tests/`),
which legitimately isn't importable from a run against the outer repo's root. This half is a
per-project, state-repo-local fix (a `pytest.ini` excluding `horseless-carriage/`), applied directly
on the affected installation, outside this repo.

Second, and the real conceptual gap this issue fixes: `_execute_test_suite_coverage_impl` had **zero
language/build-tool detection at all** - unlike `check_build` (branches on `requirements.txt`/
`package.json`) and `_compute_code_complexity`/`_scan_security_vulnerabilities` (branch on
`_detect_primary_language`, gracefully reporting "not available" for non-Python). Every JVM
(Gradle/Maven) project managed by Horseless Carriage would have hit a variant of this regardless of
the nesting bug - either a confusing error, or a false "no tests collected" / 0% coverage despite a
real, passing native test suite, with no way for QA/DevTeam to tell "the test suite is genuinely
empty" apart from "the harness used the wrong tool entirely." Fixing only the one affected
installation would have left every future JVM installation to hit this same wall from scratch.

## Acceptance Criteria
- `_execute_test_suite_coverage_impl` dispatches to a pluggable adapter registry
  (`agents/scrum_team/tools/test_runners.py`) instead of a hardcoded `pytest` call: Gradle and Maven
  projects get real pass/fail counts (JUnit XML, emitted by both build tools' default `test` task/
  Surefire - no extra plugin needed) and best-effort coverage (JaCoCo, when configured).
  Python/pytest behavior is unchanged (same command, same `PYTHONPATH` handling, same regex parsing -
  moved verbatim into the registry's default/fallback adapter).
- "Tests ran and passed" (`tests_available`/`tests_run`/`tests_failed` - language-agnostic, always
  obtainable for a supported ecosystem) is decoupled from "coverage % was measured"
  (`coverage_available`/`test_coverage` - needs extra project-level config for some ecosystems, e.g.
  JaCoCo). The Tested gate (`advance_story_stage`, `requirements.py`) now only hard-blocks on the
  former; coverage is informational, the same way `code_complexity`/`vulnerability_scan_results`
  already degrade gracefully instead of blocking.
- A repo with a real, recognizable manifest for a language with no adapter yet (Go/Rust/Ruby/PHP/
  .NET) gets an explicit, actionable "not supported yet" note with a pre-filled link to open a
  GitHub issue requesting that adapter - both proactively at `doctor.py`/startup time (so this
  surfaces before a sprint burns a QA cycle on a confusing result, not after) and from
  `_execute_test_suite_coverage_impl` itself as a fallback. Node/npm is deliberately excluded from
  this "unsupported, file an issue" treatment - `package.json` is too common alongside an unrelated
  primary Python test suite to treat its mere presence as "this project is Node"; Node/npm adapter
  support itself remains deferred (unchanged from pre-ISSUE-0064 behavior).
- The agent image can actually execute `./gradlew test`/`./mvnw test` when asked to (no JVM existed
  anywhere in `agent.Dockerfile` before this), behind an opt-in `INSTALL_JDK` build arg (default
  `false` - most managed projects aren't JVM-based, and a JDK adds real image size/build time this
  image shouldn't pay unconditionally). `new_project.py` auto-detects a Gradle/Maven manifest in the
  target repo and defaults `INSTALL_JDK=true` in `.env`; `doctor.py` warns if that drifts out of sync
  (a Gradle/Maven project detected but `INSTALL_JDK` not set).
- Full existing suite (`pytest --cov=agents agents/scrum_team/tests`) stays green; new tests cover
  the Gradle/Maven adapters (real JUnit XML/JaCoCo XML fixtures on disk, only the subprocess boundary
  mocked) and the unsupported-ecosystem detection/issue-link path.

## Notes
- Scope decision (confirmed with the project owner): Gradle + Maven adapters now; npm/Go/Rust
  deferred to an independent follow-up - the registry shape makes adding one later a self-contained
  unit, not a rewrite.
- The affected installation has **no `gradlew`/`mvnw` wrapper committed** - the Gradle adapter's
  primary path depends on a committed wrapper (falls back to a system `gradle`/`mvn` otherwise,
  noted in the result). That's a gap in the affected project, separate from this fix.
- The JVM manifest/unsupported-ecosystem detection logic is deliberately duplicated in small form
  between `test_runners.py` (in-container agent tool, needs litellm/google-adk) and `doctor.py`/
  `new_project.py` (host-side, stdlib-only by design, run before any agent container exists) rather
  than shared via an import - importing anything under `agents/` from a host script would break that
  stdlib-only boundary for the sake of ~15 lines of filesystem sniffing.
- `last_test_suite_result`'s own persisted key is kept as `"available"` (now sourced from
  `tests_available`) rather than renamed, to avoid churn in `ScrumState`'s persisted shape beyond
  what this fix actually requires; `resolve_story_blocker`'s freshness check needed no change.

## Test Approach
- `agents/scrum_team/tests/test_test_runners.py` (new): Gradle/Maven detection, wrapper-vs-system-
  binary command selection, multi-module JUnit XML aggregation, JaCoCo-present vs. JaCoCo-absent
  coverage, unsupported-ecosystem detection + issue-link construction, registry fallback to pytest.
- `agents/scrum_team/tests/test_quality.py`: existing `_execute_test_suite_coverage_*` tests updated
  for the `tests_available`/`coverage_available` split (pytest's own behavior otherwise unchanged).
- `agents/scrum_team/tests/test_requirements.py`: new case confirming the Tested gate now passes
  with `tests_available=True, tests_failed=0, coverage_available=False` (previously blocked).
- `agents/scrum_team/tests/test_story_pipeline_state_machine.py`: added the new
  `test_runners._run` mock alongside the existing `github._run`/`quality._run` boundary mocks (the
  real pytest subprocess call moved modules).
