# agents/scrum_team/tools/announcements.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List

from .base import _configured_repo_root
from .changelog import CHANGELOG_PATH

# US-0016: internal-only changelog line items (refactors, test additions,
# tooling/chore work) must not leak into a customer-facing announcement as
# if they were a user-visible improvement. A changelog line is classified
# internal-only if its text matches any of these keywords, case-
# insensitively - a narrow, explicit heuristic (same risk tolerance as
# detect_stubbed_tests/looks_like_role_behavior_finding elsewhere in this
# codebase) rather than deeper NLP classification: false positives here
# just mean a borderline item is excluded, which is the safe direction for
# a non-technical audience.
_INTERNAL_ONLY_KEYWORDS = (
    "refactor", "test", "tests", "testing", "chore", "tooling", "internal",
    "cleanup", "lint", "ci ", "ci:", "infra", "infrastructure", "dependency",
    "dependencies", "dependency bump", "housekeeping",
)


def _is_internal_only(line: str) -> bool:
    lowered = line.lower()
    return any(keyword in lowered for keyword in _INTERNAL_ONLY_KEYWORDS)


def _latest_changelog_entry(changelog_text: str) -> str:
    """Returns the first `## ...` section of a CHANGELOG.md (the most recent entry)."""
    lines = changelog_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
    if start is None:
        return ""
    end = next((i for i, line in enumerate(lines[start + 1:], start + 1) if line.startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


def draft_announcement_from_changelog(changelog_entry: str, version_label: str) -> str:
    """
    US-0016: translates a changelog entry's user-facing line items into a
    `TEMPLATE-ANNOUNCEMENT.md`-shaped draft - internal-only items
    (`_is_internal_only`) are excluded entirely rather than leaking
    implementation detail to a non-technical audience.
    """
    date = datetime.now().strftime("%Y-%m-%d")
    item_lines = [
        line[2:].strip() for line in changelog_entry.splitlines()
        if line.startswith("- ") and not _is_internal_only(line)
    ]

    if item_lines:
        whats_new = "\n".join(f"- {line}" for line in item_lines)
        headline = "New in this release: " + item_lines[0]
    else:
        whats_new = "- No user-facing changes in this release."
        headline = "This release contains no user-facing changes."

    return (
        "# Release Announcement\n\n"
        f"- Release: {version_label}\n"
        "- Audience: customers\n"
        "- Status: Draft\n"
        f"- Last Updated: {date}\n\n"
        "## Headline\n"
        f"{headline}\n\n"
        "## What's New\n"
        f"{whats_new}\n\n"
        "## Fixes & Improvements\n"
        "- See the full changelog for details.\n\n"
        "## Known Issues\n"
        "- None known at this time.\n\n"
        "## Links\n"
        "- [CHANGELOG.md](../../CHANGELOG.md)\n"
    )


def generate_announcement(tool_context=None) -> Dict[str, Any]:
    """
    US-0016: drafts a customer-facing release announcement from the
    changelog entry IP-0004's `generate_changelog` already produced,
    writing it to `specs/announcements/ANNOUNCEMENT-sprint-{N}.md` in the
    target repo. Reports an error (rather than fabricating content) if no
    changelog exists yet - this is meant to run strictly after
    `generate_changelog`/`create_release_pr`, not instead of it.
    """
    from .docs import write_file

    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    repo_root = _configured_repo_root(tool_context)
    changelog_path = repo_root / CHANGELOG_PATH
    if not changelog_path.exists():
        return {
            "status": "error",
            "message": (
                "No CHANGELOG.md exists yet in the target repo - an announcement can't be drafted "
                "until a release has generated one (see create_release_pr)."
            ),
        }
    try:
        changelog_text = changelog_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"status": "error", "message": f"Could not read CHANGELOG.md: {e}"}

    latest_entry = _latest_changelog_entry(changelog_text)
    if not latest_entry:
        return {
            "status": "error",
            "message": "CHANGELOG.md exists but has no entries yet - nothing to draft an announcement from.",
        }

    sprint_number = state.get("sprint_number", 0)
    version_label = f"Sprint {sprint_number}"
    content = draft_announcement_from_changelog(latest_entry, version_label)
    path = f"specs/announcements/ANNOUNCEMENT-sprint-{sprint_number}.md"
    return write_file(path, content, overwrite=True, tool_context=tool_context)
