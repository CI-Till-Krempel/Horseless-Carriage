# agents/scrum_team/tools/changelog.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..helpers import is_story_done
from .base import _configured_repo_root

CHANGELOG_PATH = "CHANGELOG.md"

_CHANGELOG_HEADER = (
    "# Changelog\n\n"
    "All notable changes to this project are documented in this file. Each entry is "
    "generated automatically at release time from this sprint's completed backlog "
    "items (`sprint_backlog`), not authored freehand - see RELEASE.md.\n\n"
)


def _changelog_line(item: Dict[str, Any]) -> str:
    """
    US-0013: one line per completed sprint_backlog item. Falls back to the
    item's bare ID (or title, if no ID) when the other of the two is
    missing, rather than silently dropping an item that lacks one.
    """
    item_id = (item.get("id") or "").strip()
    title = (item.get("title") or "").strip()
    if item_id and title and item_id != title:
        return f"- {item_id}: {title}"
    return f"- {item_id or title or 'unknown item'}"


def derive_changelog_entries(
    sprint_backlog: List[Dict[str, Any]], existing_changelog_text: str = ""
) -> List[str]:
    """
    US-0013: derives changelog line items from `ScrumState.sprint_backlog` -
    only items that actually reached a Done/Accepted status (`is_story_done`,
    the same check `advance_story_stage`'s own Accepted stage writes)
    surface here; anything still In Progress/Draft/Ready is excluded.

    `sprint_backlog` is never reset between sprints (it accumulates stage
    progress across the whole run - see specs/ROADMAP.md's KPI-trends note
    for the same underlying fact), so without this, every release's entry
    would re-list every previously-shipped item forever. `existing_changelog_
    text` - the target repo's current CHANGELOG.md content, if any - lets a
    line already recorded in an earlier release's entry be skipped, with no
    new ScrumState field required to track it separately.
    """
    lines = []
    for item in sprint_backlog:
        if not is_story_done(item.get("status")):
            continue
        line = _changelog_line(item)
        if existing_changelog_text and line in existing_changelog_text:
            continue
        lines.append(line)
    return lines


def generate_changelog_entry(
    sprint_number: int, sprint_backlog: List[Dict[str, Any]], existing_changelog_text: str = ""
) -> str:
    """
    US-0012/US-0013: one dated, versioned (by sprint - the target product
    has no SemVer scheme of its own yet, see GH-501/ROADMAP.md v0.3) Keep-a-
    Changelog-style entry. A sprint with nothing newly completed still
    produces an honest, minimal entry rather than an empty or fabricated
    one.
    """
    date = datetime.now().strftime("%Y-%m-%d")
    header = f"## Sprint {sprint_number} - {date}\n\n"
    lines = derive_changelog_entries(sprint_backlog, existing_changelog_text)
    if not lines:
        return header + "- No user-facing changes in this release.\n"
    return header + "\n".join(lines) + "\n"


def _read_existing_changelog(repo_root) -> str:
    path = repo_root / CHANGELOG_PATH
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def generate_changelog(tool_context=None) -> Dict[str, Any]:
    """
    US-0012: generates this release's changelog entry from
    `ScrumState.sprint_backlog` and prepends it to `CHANGELOG.md` in the
    target repo, creating the file (with a standard header) if it doesn't
    exist yet. Entries accumulate in reverse-chronological order, each
    clearly delimited by its own `## Sprint N - YYYY-MM-DD` heading.

    Wired into `create_release_pr` (tools/github.py), before the push that
    lands the release - see that function's own call site for the failure-
    isolation wrapper (US-0014): a changelog-generation error must not
    block the release itself.
    """
    from .docs import write_file

    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    repo_root = _configured_repo_root(tool_context)
    existing = _read_existing_changelog(repo_root)
    body = existing[len(_CHANGELOG_HEADER):] if existing.startswith(_CHANGELOG_HEADER) else existing

    entry = generate_changelog_entry(
        sprint_number=state.get("sprint_number", 0),
        sprint_backlog=state.get("sprint_backlog", []),
        existing_changelog_text=existing,
    )

    new_content = _CHANGELOG_HEADER + entry.strip() + "\n\n" + body.lstrip("\n")
    result = write_file(CHANGELOG_PATH, new_content.rstrip("\n") + "\n", overwrite=True, tool_context=tool_context)
    if result.get("status") == "ok":
        result["entry"] = entry
    return result
