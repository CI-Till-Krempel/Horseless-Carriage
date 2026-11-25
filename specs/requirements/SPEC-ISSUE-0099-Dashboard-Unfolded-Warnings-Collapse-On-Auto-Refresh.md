# Issue

- Issue ID: SPEC-ISSUE-0099
- Title: Dashboard - unfolded doctor warnings snap shut on every 10s auto-refresh
- Status: Done
- Priority: Should
- Owner: Till Krempel
- Last Updated: 2026-11-25

## Overview

GH issue #564: "When I unfold warnings in the dashboard, and it refreshes, the warnings are closed
again. This makes them unreadable."

`dashboard.py`'s per-project **Doctor** column renders its warnings/errors punch list as a
collapsed `<details>`/`<summary>` (`_doctor_badge`, `dashboard.py:487`, emitting
`<details><summary ...>&#9888; N warning(s)</summary><ul>...</ul></details>` at
`dashboard.py:506` before this fix). The page reloads itself in full every 10 seconds via
`<meta http-equiv="refresh" content="10">` in `_render_page`'s template (`dashboard.py:650` before
this fix). A full reload re-parses fresh server HTML, and a `<details>` without the `open`
attribute always renders closed - the browser does not carry an element's open state across a
navigation. Nothing on the page (no script, no server-side state) remembered which `<details>` a
user had unfolded, so any list opened between refreshes snapped shut within at most 10s. With
several doctor items (each a full sentence) that's often not enough time to read them.

**Confirmed mechanism:** stateless full-page meta refresh + `<details>` defaulting to closed + no
persisted open state. Not a server bug - `_doctor_badge`'s output is identical on every refresh.

## Acceptance Criteria

- An unfolded doctor warnings list stays unfolded across the dashboard's auto-refresh, for as long
  as the user keeps it open in that tab; folding it again also persists.
- The open state is keyed by project, not by row position, so a project being added/removed between
  refreshes doesn't open a different project's list.
- The dashboard still needs no server-side state for this and still works (falling back to the old
  collapsed behavior) if `sessionStorage` is unavailable.
- Tests (`tests/test_dashboard.py`) that fail before the fix and pass after:
  `TestDoctorBadge::test_carries_a_stable_per_project_fold_key` and
  `TestRenderPage::test_warnings_fold_state_survives_the_auto_refresh`.

## Notes

- GH issue #564.
- Fix: `_doctor_badge` takes a `fold_key` (the project name, HTML-escaped) and emits
  `<details data-fold-key='doctor:<name>'>`; `_render_page` passes `fold_key=snap['name']` and
  appends a small inline `_FOLD_STATE_SCRIPT` that, on load, re-opens every
  `details[data-fold-key]` recorded as open in `sessionStorage` (`hc-dashboard-open-folds`) and
  updates that record on each `toggle` event. The script runs at the end of `<body>`, before first
  paint in practice, so there's no visible collapse/re-open flicker.
- Alternative considered: replace the meta refresh with a `fetch()` + partial DOM swap that skips
  open `<details>`. Rejected for this fix - a much larger change to how the page refreshes (and to
  the Start/Stop/Update action links' POST/redirect flow) for the same user-visible result.
  `sessionStorage` (per-tab) rather than `localStorage`: the state is a reading convenience, not a
  preference that should leak into every future dashboard tab.
- Any future `<details>` on the dashboard gets the same persistence for free by carrying a
  `data-fold-key`.

## Test Approach

- Host-script unit tests in `tests/test_dashboard.py` (no browser): assert the badge carries the
  escaped per-project key, and that the rendered page (which still contains the meta refresh)
  includes the keyed `<details>` plus the restore script using `sessionStorage` and a `toggle`
  listener. Both fail on `main` before the fix (`TypeError` on the unknown `fold_key` argument /
  missing `data-fold-key`).
- The script's JS was syntax-checked with `node --check`; the toggle/restore behavior itself is
  plain DOM API and was not exercised in a headless browser.
