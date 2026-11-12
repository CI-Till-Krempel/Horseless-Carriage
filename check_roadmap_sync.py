#!/usr/bin/env python3
"""
GH issue #272: "Sync Github Issues and versions to roadmap."

specs/ROADMAP.md hand-lists which `[GH-NNN]` GitHub issues target which
release (e.g. under its "### v0.3 ..." section). Separately, this repo's
real GitHub milestones (`v0.1.0` ... `v0.8.0`) get assigned to issues
directly on GitHub. Nothing keeps those two in sync automatically, so they
silently drift apart - the exact, already-observed failure mode this script
exists to catch: issues #513 and #515 carried the `v0.3.0` GitHub milestone
for a while before anyone added them to ROADMAP.md's v0.3 section, and #456
was milestoned `v0.3.0` on GitHub but the roadmap still listed it under v0.5
until someone noticed and fixed it by hand.

specs/ROADMAP.md stays the source of truth (see #272's own PO comment:
making GitHub authoritative would mean reconciling two mutable stores,
which is a bigger and riskier change than this issue's scope) - this
script never writes to GitHub or edits ROADMAP.md, it only flags
disagreement so a human fixes whichever side is wrong. Same "detect, don't
silently auto-fix" shape as the rest of this repo's host tooling
(doctor.py, watch_roadmap.py).

Usage:
  python3 check_roadmap_sync.py                  # `gh`'s own repo detection
  python3 check_roadmap_sync.py --repo owner/name
  python3 check_roadmap_sync.py --roadmap path/to/ROADMAP.md

Exit code: 0 if no drift is found, OR if GitHub couldn't even be reached
(no `gh`, not authenticated, API error - this never hard-fails CI on a
transient network/auth problem, only on real, confirmed drift); 1 if at
least one open GitHub issue's actual milestone disagrees with where (or
whether) specs/ROADMAP.md lists it.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ROADMAP_PATH = REPO_ROOT / "specs" / "ROADMAP.md"

# Only a "### <something>" heading resets which version section subsequent
# bullet lines belong to; "## "/"---"/etc. never appear inside the per-
# version "Release plan" list, so they're not special-cased here.
_SECTION_HEADER_RE = re.compile(r"^###\s+(.*)$")
# A section heading counts as a real *version* section only if it starts
# with "vN", "vN.N", etc. (e.g. "v0.3 — Pluggable ..."). Anything else
# ("Research backlog (exploratory...)", "Backlog (unplanned...)", or the
# Kanban sub-headings like "v0.1 Kanban") either isn't a real GitHub
# milestone target, or (Kanban) has no bracketed `[GH-NNN]` checklist lines
# to begin with - see parse_roadmap_versions's docstring.
_VERSION_LABEL_RE = re.compile(r"^(v\d+(?:\.\d+)*)\b")
_CHECKLIST_LINE_RE = re.compile(r"^-\s*\[[ xX~R]\]")
_GH_ID_RE = re.compile(r"\[GH-(\d+)\]")


def parse_roadmap_entries(markdown_text: str) -> Dict[str, str]:
    """Returns {"GH-513": "v0.3 — Pluggable Development ...", "GH-96":
    "Research backlog (exploratory, not a committed release)", ...} - which
    "### <header>" section each `[GH-NNN]` checklist entry is listed under,
    for EVERY section (not just real "vX.Y" version ones - see
    parse_roadmap_versions, which filters this down to just those), read
    directly from each section's own bullet lines (NOT the "Index of story
    references" summary near the bottom of ROADMAP.md, which is itself
    just a hand-maintained mirror of this and can drift from the real
    section bodies the very same way ROADMAP.md as a whole can drift from
    GitHub - parsing the authoritative section bodies instead means this
    check isn't fooled by the index falling out of sync too).

    Only a line that is itself a checklist entry (`- [ ]`, `- [x]`, `- [~]`,
    `- [R]`) whose own `[GH-NNN]` is the entry counts - a GH-NNN mentioned
    only in a *different* entry's prose aside (e.g. "(see GH-286 for
    related but distinct scope)") is deliberately not picked up, since
    that's not this issue's own placement in the roadmap.
    """
    result: Dict[str, str] = {}
    current_label: Optional[str] = None
    for raw_line in markdown_text.splitlines():
        header_match = _SECTION_HEADER_RE.match(raw_line)
        if header_match:
            current_label = header_match.group(1).strip()
            continue
        if current_label is None:
            continue
        stripped = raw_line.strip()
        if not _CHECKLIST_LINE_RE.match(stripped):
            continue
        for id_match in _GH_ID_RE.finditer(stripped):
            result[f"GH-{id_match.group(1)}"] = current_label
    return result


def parse_roadmap_versions(markdown_text: str) -> Dict[str, str]:
    """Returns {"GH-513": "v0.3", ...} - parse_roadmap_entries's output,
    filtered down to just the entries listed under a real "vX.Y ..."
    version section header (dropping "Research backlog"/"Backlog"/Kanban
    sub-table entries, which aren't real GitHub-milestone targets - see
    parse_roadmap_non_version_ids)."""
    result = {}
    for gh_id, label in parse_roadmap_entries(markdown_text).items():
        label_match = _VERSION_LABEL_RE.match(label)
        if label_match:
            result[gh_id] = label_match.group(1)
    return result


def parse_roadmap_non_version_ids(markdown_text: str) -> set:
    """Returns the set of `[GH-NNN]` ids listed under some roadmap section
    OTHER than a real "vX.Y ..." version one (e.g. "Research backlog
    (exploratory, not a committed release)", "Backlog (unplanned, no
    GitHub issue yet)"). These are exempt from the "has a GitHub milestone
    but isn't in the roadmap at all" check in compute_drift - ROADMAP.md's
    own "How to use" section says every open issue should carry a
    milestone "matching one of the versions below (or the `Research
    backlog` wave)", and since GitHub milestones only exist per-version
    (no milestone named "Research backlog" exists, or could usefully
    exist), there's no single "expected" milestone value to compare
    against for these - whatever real milestone (if any) a research-
    backlog/unplanned issue happens to carry on GitHub is not drift."""
    entries = parse_roadmap_entries(markdown_text)
    versioned = parse_roadmap_versions(markdown_text)
    return {gh_id for gh_id in entries if gh_id not in versioned}


def version_to_milestone_title(version: str) -> str:
    """Roadmap sections use short version labels ("v0.3"); this repo's real
    GitHub milestones are full three-part tags ("v0.3.0"). Pads with zero
    patch components until the convention matches - "v0.3" -> "v0.3.0",
    "v1" -> "v1.0.0", "v0.3.1" (already three parts) -> unchanged."""
    parts = version[1:].split(".")
    while len(parts) < 3:
        parts.append("0")
    return "v" + ".".join(parts)


def fetch_open_issues_with_milestones(repo: Optional[str] = None) -> Optional[List[dict]]:
    """Returns [{"number": 513, "title": "...", "milestone": "v0.3.0" | None}, ...]
    for every OPEN issue in the configured (or `gh`-inferred) repo, via the
    `gh` CLI. Returns None - not [] - if `gh` isn't installed, isn't
    authenticated, or the call otherwise fails for any reason, so a caller
    can tell "nothing to report" apart from "couldn't check at all" and
    degrade to a no-op rather than crash (same distinction
    watch_roadmap.py's develop_branch_head draws for a failed `git
    fetch`)."""
    cmd = ["gh", "issue", "list", "--state", "open", "--limit", "500",
           "--json", "number,title,milestone"]
    if repo:
        cmd += ["--repo", repo]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        raw = json.loads(result.stdout)
    except Exception:
        return None
    if not isinstance(raw, list):
        return None

    issues = []
    for item in raw:
        if not isinstance(item, dict) or item.get("number") is None:
            continue
        milestone = item.get("milestone")
        milestone_title = milestone.get("title") if isinstance(milestone, dict) else None
        issues.append({
            "number": item["number"],
            "title": item.get("title", ""),
            "milestone": milestone_title,
        })
    return issues


@dataclass
class DriftItem:
    """One confirmed disagreement between specs/ROADMAP.md and an open
    GitHub issue's actual milestone. `kind` is one of:
      - "missing_from_roadmap": GitHub has a milestone, ROADMAP.md doesn't
        list this issue under any version section at all.
      - "roadmap_only": ROADMAP.md lists this issue under a version, but
        the GitHub issue itself has no milestone set.
      - "milestone_mismatch": both sides have an opinion, but they disagree.
    """
    gh_id: str
    kind: str
    detail: str


def compute_drift(
    roadmap_versions: Dict[str, str],
    issues: List[dict],
    exempt_ids: Optional[set] = None,
) -> List[DriftItem]:
    """Compares every OPEN issue's actual GitHub milestone against
    roadmap_versions (parse_roadmap_versions's output). Deliberately only
    iterates `issues`, not `roadmap_versions` - a `[GH-NNN]` the roadmap
    still lists under a version after that issue was closed on GitHub
    (e.g. a `- [x]` Done entry from an already-shipped release) is normal,
    not drift, and simply never shows up in the open-issues list fetched
    by fetch_open_issues_with_milestones.

    exempt_ids (parse_roadmap_non_version_ids's output) are skipped
    entirely, regardless of their actual GitHub milestone - they're listed
    in the roadmap under a non-version section (Research backlog/Backlog),
    which has no single "expected" milestone to compare against."""
    exempt_ids = exempt_ids or set()
    drift = []
    for issue in issues:
        gh_id = f"GH-{issue['number']}"
        if gh_id in exempt_ids:
            continue
        actual_milestone = issue["milestone"]
        roadmap_version = roadmap_versions.get(gh_id)
        expected_milestone = version_to_milestone_title(roadmap_version) if roadmap_version else None

        if roadmap_version is None and actual_milestone:
            drift.append(DriftItem(
                gh_id, "missing_from_roadmap",
                f"{gh_id} ({issue['title']!r}) carries GitHub milestone '{actual_milestone}' but isn't "
                f"listed in any specs/ROADMAP.md version section - add it under the matching version."
            ))
        elif roadmap_version and actual_milestone is None:
            drift.append(DriftItem(
                gh_id, "roadmap_only",
                f"{gh_id} is listed under {roadmap_version} in specs/ROADMAP.md but has no GitHub "
                f"milestone set - run: gh issue edit {issue['number']} --milestone {expected_milestone}"
            ))
        elif roadmap_version and actual_milestone and actual_milestone != expected_milestone:
            drift.append(DriftItem(
                gh_id, "milestone_mismatch",
                f"{gh_id} is listed under {roadmap_version} (expects milestone '{expected_milestone}') in "
                f"specs/ROADMAP.md, but GitHub's actual milestone is '{actual_milestone}' - fix whichever "
                f"side is wrong."
            ))
    return drift


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=None, help="owner/repo to check (default: `gh`'s own git-remote detection)")
    parser.add_argument("--roadmap", default=str(DEFAULT_ROADMAP_PATH), help="Path to ROADMAP.md (default: specs/ROADMAP.md)")
    args = parser.parse_args(argv)

    roadmap_path = Path(args.roadmap)
    if not roadmap_path.is_file():
        print(f"NOTE: {roadmap_path} not found - nothing to check.")
        return 0
    roadmap_text = roadmap_path.read_text(encoding="utf-8", errors="replace")
    roadmap_versions = parse_roadmap_versions(roadmap_text)
    exempt_ids = parse_roadmap_non_version_ids(roadmap_text)

    issues = fetch_open_issues_with_milestones(args.repo)
    if issues is None:
        print("NOTE: could not fetch open GitHub issues via `gh` (not installed, not authenticated, or a "
              "network/API error) - skipping the roadmap/milestone sync check.")
        return 0

    drift = compute_drift(roadmap_versions, issues, exempt_ids)
    if not drift:
        print(f"OK: all {len(issues)} open GitHub issue(s) checked against {roadmap_path} with no drift found.")
        return 0

    print(f"Found {len(drift)} roadmap/GitHub milestone mismatch(es):")
    for item in drift:
        print(f"  [{item.kind}] {item.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
