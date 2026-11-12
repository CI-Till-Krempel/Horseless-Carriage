"""
Regression tests for GH-316: .github/CODEOWNERS designates required
reviewers for sensitive, process-critical paths (CI/release automation,
the network egress allowlist, .env templates, release/security docs).

These tests only check that the file exists, is well-formed, and its
patterns actually point at real paths in this repo - they cannot (and
should not try to) verify GitHub's branch-protection enforcement, which is
a manual Settings toggle this story deliberately does not flip (see
docs/GITHUB-INTEGRATION.md).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEOWNERS_PATH = REPO_ROOT / ".github" / "CODEOWNERS"


def _entries():
    """Return (pattern, owners) for every non-comment, non-blank line."""
    entries = []
    for line in CODEOWNERS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) >= 2, f"malformed CODEOWNERS line (no owner listed): {line!r}"
        pattern, owners = parts[0], parts[1:]
        entries.append((pattern, owners))
    return entries


def test_codeowners_file_exists_and_is_non_empty():
    assert CODEOWNERS_PATH.is_file(), ".github/CODEOWNERS is missing (GH-316)"
    assert CODEOWNERS_PATH.read_text(encoding="utf-8").strip(), ".github/CODEOWNERS is empty"


def test_every_entry_has_a_real_github_owner():
    entries = _entries()
    assert entries, ".github/CODEOWNERS has no path entries (only comments/blank lines)"
    for pattern, owners in entries:
        for owner in owners:
            assert owner.startswith("@"), (
                f"CODEOWNERS owner {owner!r} for pattern {pattern!r} should be a "
                "GitHub @username or @org/team, not a placeholder"
            )
            assert "your-org" not in owner and "your-team" not in owner, (
                f"CODEOWNERS pattern {pattern!r} still has a placeholder owner {owner!r} "
                "- it must name the real repo owner"
            )


def test_codeowners_patterns_reference_paths_that_actually_exist():
    """Catch typo'd paths: every pattern must resolve to a real file or
    directory in this repo (independent of the comment text next to it)."""
    missing = []
    for pattern, _owners in _entries():
        # CODEOWNERS patterns are gitignore-style; strip a leading "/" (repo-root
        # anchor) and a trailing "/" (directory marker) to resolve on disk.
        rel = pattern.lstrip("/").rstrip("/")
        if not (REPO_ROOT / rel).exists():
            missing.append(pattern)
    assert not missing, f"CODEOWNERS references path(s) that don't exist in this repo: {missing}"


def test_sensitive_paths_called_out_in_gh_316_are_covered():
    """Spot-check that the specific sensitive categories named in GH-316 are
    actually represented, not just that *some* entries exist."""
    patterns = {pattern for pattern, _owners in _entries()}
    expected_substrings = [
        ".github/workflows",  # CI / release automation
        "config/net-proxy",  # network egress allowlist
        ".env",  # env templates
    ]
    for expected in expected_substrings:
        assert any(expected in pattern for pattern in patterns), (
            f"CODEOWNERS has no entry covering {expected!r}, which GH-316 calls out "
            "as a sensitive path category"
        )
