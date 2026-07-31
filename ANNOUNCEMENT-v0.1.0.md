# Release Announcement

- Release: v0.1.0
- Audience: customers
- Status: Draft
- Last Updated: 2026-07-21

## Pitch
Horseless Carriage gives an LLM a simulated Scrum team — Product Owner, Scrum Master,
Dev, QA, and Architect — that plans, builds, and ships real code against your actual
GitHub repo. You set the budget and how much of a human stays in the loop, and the
team runs the sprint end to end.

## Headline
Horseless Carriage v0.1.0 is here: a multi-agent Scrum team you can point at a real
repo today, with trustworthy sprint reports instead of placeholder numbers.

## What's New
- **A full multi-agent Scrum team, ready to run.** Product Owner, Scrum Master, Dev
  Team, QA, and Architect agents, orchestrated by a root "ScrumOrchestrator" — backlog
  management, sprint planning, implementation plans, and sprint reviews, out of the box.
- **Your whole team's conversation is captured, not just the headlines.** Every
  sub-agent's turns are recorded into a shared transcript and persisted to your state
  repo, so you can trace exactly which agent decided what — not just the
  Orchestrator's summary of it.
- **Sprint reports reflect what actually happened.** Test coverage, code complexity,
  and security scan results in your sprint report now come from real test runs and
  real static analysis / scanner output against your repo — not hardcoded placeholder
  numbers.
- **Release PRs can't silently drop your sprint's work.** Every file the team writes
  during a sprint is tracked, and a release PR is checked against that tracking before
  it opens — so a forgotten `git add` can't quietly leave real work out of a release.
- **Dual-layer budget control.** A hard token budget (enforced locally, zero-latency)
  plus a real USD budget (enforced by your LiteLLM proxy) keep a sprint from running
  away, with per-agent spend visibility in the LiteLLM admin UI.
- **GitHub integration that acts like a real teammate.** Commits, PR comments, and
  reviews are attributed to the specific agent role that made them, using either a
  personal access token or a dedicated GitHub App identity.

## Fixes & Improvements
- Sub-agent turns are no longer dropped from the shared conversation history — the
  full multi-agent exchange is captured and trimmed to fit your token budget rather
  than silently truncated.
- `calculate_kpis()` now runs your actual test suite, a real complexity analyzer, and
  a real security scanner, and clearly flags any metric it couldn't compute rather
  than substituting a fake number.
- `create_release_pr()` now diffs the release against what the sprint actually
  touched, and only auto-stages files the sprint is responsible for — stray,
  unrelated local edits are flagged for human review instead of being swept in.
- Horseless Carriage now records its own version in your state repo and in every
  sprint report, so you always know which version of the tool produced a given
  result.

## Known Issues
- Changelog generation for your target product, customer-facing announcement
  drafting, and end-user product-doc tooling are planned for v0.2 — not yet
  available.
- This is an early release: no automated secret-scanning in CI yet, and no formal
  security audit has been performed (see `SECURITY.md` for the basic review that
  has been done).
- GitFlow branching (`develop`, `release/*`, `hotfix/*`) is documented in `RELEASE.md`
  but not yet in effect — this release ships directly from `main`; the branch model
  switch happens right after this tag.

## Links
- [README.md](README.md) — what's in this repo and how it works.
- [MANUAL.md](MANUAL.md) — user manual: concepts and day-to-day usage.
- [RELEASE.md](RELEASE.md) — versioning and release process.
- [SECURITY.md](SECURITY.md) — secret-handling notes and how to report a vulnerability.
- [specs/ROADMAP.md](specs/ROADMAP.md) — what shipped in this release and what's next.
