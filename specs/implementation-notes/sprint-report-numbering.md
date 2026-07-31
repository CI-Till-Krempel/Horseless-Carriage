# Sprint report numbering

`create_sprint_report` writes every sprint's report to a sequentially numbered
`specs/reports/SPRINT-REPORT-NNN.md` (`001`, `002`, ...), with the number derived by scanning what's
already there (the same approach used for story/ADR IDs, so there's no separate counter to drift out
of sync). `specs/reports/SPRINT-REPORT-LATEST.md` is kept as a convenience pointer to the most recent
one.

Earlier versions of this repo only ever wrote `SPRINT-REPORT-LATEST.md`, so every sprint but the most
recent got silently overwritten and there was no historical record of past sprint reports. The
sequential-numbering scheme fixed that while keeping the "latest" pointer for anything that just wants
the current report.
