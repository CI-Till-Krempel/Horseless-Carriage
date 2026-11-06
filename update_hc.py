#!/usr/bin/env python3
"""
Updates an installed Horseless Carriage checkout (the `horseless-carriage/`
git submodule of a product repo - see new_project.py) to the latest release
tag or the latest `main` commit, configurable per call: cleanly stops its
stack first (if it's running), updates the checkout, rebuilds Docker images
if the update actually touched something that would make them stale, and
restarts the stack again if it was running before.

Before this script, "update Horseless Carriage in my product repo" meant a
hand-rolled `git -C horseless-carriage fetch && git checkout ...` dance, with
no guarantee the running stack was stopped first, no sense of whether a
rebuild was now needed, and no help putting it back the way it was found.
dashboard.py's own "Update" links (one per registered project) call
run_update() directly - this module's CLI is for updating a checkout by
hand, from inside it, the same way setup_project.py/doctor.py are each both
a standalone script and a function other scripts import.

Usage:
  python3 update_hc.py                  # this checkout, channel=release (the default)
  python3 update_hc.py --channel main   # track `main` instead of the latest release tag
  python3 update_hc.py --hc-path ../other-project/horseless-carriage --channel release
  python3 update_hc.py --force          # proceed even if the checkout has local changes
"""
import argparse
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import doctor
import lib_docker
import rebuild_images

CHANNELS = ("release", "main")

# Files whose change since the last update implies the built `agent`/`ollama`
# images could now be stale - the same set of inputs rebuild_images.py's own
# `docker compose build` depends on. Not exhaustive of every file that could
# ever affect the image (e.g. agents/ source is already bind-mounted, not
# baked in) - just the ones that actually change what gets baked in.
_REBUILD_TRIGGER_FILES = {
    "requirements.txt",
    "agent.Dockerfile",
    "ollama.Dockerfile",
    "net-proxy.Dockerfile",
    "docker-compose.yaml",
    "docker-compose.local.yaml",
    "docker-compose.gpu.yaml",
    "docker-compose.local-hostollama.yaml",
    "entrypoint.sh",
    "ollama-entrypoint.sh",
}

_GIT_TIMEOUT_SECS = 60


def _compose_args(hc_path: Path) -> list:
    return lib_docker.compose_file_args(hc_path) + lib_docker.compose_project_args("dev", hc_path)


def _git(hc_path: Path, *args: str, timeout: int = _GIT_TIMEOUT_SECS) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=hc_path, capture_output=True, text=True, timeout=timeout,
    )


def _is_git_checkout(hc_path: Path) -> bool:
    return (hc_path / ".git").exists()


def _working_tree_dirty(hc_path: Path) -> bool:
    """True if a TRACKED file has been modified/staged - deliberately
    ignores untracked files (--untracked-files=no): an installed checkout
    routinely has untracked junk (.env, __pycache__, a local sessions/
    entry, ...) that `git checkout <ref>` never touches and is never at risk
    of being discarded by it, so it has no business blocking an update."""
    result = _git(hc_path, "status", "--porcelain", "--untracked-files=no")
    return result.returncode == 0 and bool(result.stdout.strip())


def _rev_parse(hc_path: Path, ref: str = "HEAD") -> str:
    result = _git(hc_path, "rev-parse", ref)
    return result.stdout.strip() if result.returncode == 0 else ""


def _read_version(hc_path: Path) -> str:
    version_file = hc_path / "VERSION"
    if version_file.is_file():
        text = version_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "unknown"


def _latest_release_tag(hc_path: Path) -> str:
    """Highest `vX.Y.Z`-shaped tag reachable from origin, by version sort -
    requires `git fetch --tags` to have already run. Empty string if none
    exist yet (e.g. a checkout of this repo before its first tagged release -
    RELEASE.md's own procedure cuts v0.1.0 as the first one)."""
    result = _git(hc_path, "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*", "--sort=-v:refname")
    if result.returncode != 0:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def resolve_channel_ref(hc_path: Path, channel: str):
    """(ref, label) to check out for `channel` ("release" or "main"), or
    (None, error_message) if it can't be resolved. ref is always something
    `git checkout` can resolve directly (a tag name, or `origin/main`)."""
    if channel == "main":
        return "origin/main", "main"
    if channel == "release":
        tag = _latest_release_tag(hc_path)
        if tag:
            return tag, tag
        return "origin/main", "main (no release tags found yet - falling back to main)"
    return None, f"Unknown channel '{channel}' - must be one of {CHANNELS}."


def _checkout(hc_path: Path, channel: str, ref: str, force: bool) -> subprocess.CompletedProcess:
    cmd = ["checkout"]
    if force:
        cmd.append("-f")
    if channel == "main":
        # A local branch (not a detached tag) so `git pull`/a later re-run of
        # this same script can fast-forward it normally, same as any other
        # branch checkout - detached HEAD is reserved for pinned releases.
        cmd += ["-B", "main", ref]
    else:
        cmd += ["--detach", ref]
    return _git(hc_path, *cmd)


def _changed_files(hc_path: Path, old_sha: str, new_sha: str) -> list:
    if not old_sha or not new_sha or old_sha == new_sha:
        return []
    result = _git(hc_path, "diff", "--name-only", f"{old_sha}..{new_sha}")
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def needs_rebuild(changed_files: list) -> bool:
    return any(f in _REBUILD_TRIGGER_FILES for f in changed_files)


_ENV_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _sync_new_env_keys(hc_path: Path) -> list:
    """Appends any KEY=... line present in .env.example but entirely absent
    from .env (an update that introduces a new config variable) - never
    touches a key that's already there, even if its .env.example default
    has since changed. Returns the list of keys actually added. A no-op (not
    an error) if either file is missing - this project's .env is expected to
    already exist by the time an installed checkout gets updated; inventing
    one from scratch here is setup_project.py's job, not an update's."""
    env_path = hc_path / ".env"
    example_path = hc_path / ".env.example"
    if not env_path.is_file() or not example_path.is_file():
        return []

    env_text = env_path.read_text(encoding="utf-8")
    added = []
    new_lines = []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        m = _ENV_KEY_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if re.search(rf"^{re.escape(key)}=", env_text, re.MULTILINE):
            continue
        new_lines.append(line)
        added.append(key)

    if new_lines:
        with env_path.open("a", encoding="utf-8") as f:
            if not env_text.endswith("\n"):
                f.write("\n")
            f.write("\n".join(new_lines) + "\n")
    return added


def run_update(hc_path: Path, channel: str = "release", force: bool = False) -> str:
    """Stop (if running) -> update -> recreate if needed -> restart (if it
    was running). Never raises - every failure mode becomes part of the
    returned message instead, the same convention dashboard.py's own
    _start_stack/_stop_stack already use, since this is meant to be safely
    callable from a web request handler (dashboard.py's /update route).

    Local import of dashboard (not a top-level one) to reuse its
    _start_stack/_stop_stack rather than duplicating that logic here -
    dashboard.py itself imports this module at the top level to call
    run_update() from its own /update route, and a top-level `import
    dashboard` here would make that circular. Same workaround
    rebuild_images.py already uses for its own `import run`, for the same
    reason - see rebuild_images.rebuild's docstring."""
    import dashboard

    hc_path = Path(hc_path)
    if channel not in CHANNELS:
        return f"Unknown channel '{channel}' - must be one of {CHANNELS}."
    if not _is_git_checkout(hc_path):
        return f"{hc_path} doesn't look like a git checkout (no .git) - cannot update."

    lines = []
    compose_args = _compose_args(hc_path)
    was_running = bool(lib_docker.compose_running_services(compose_args, cwd=hc_path))
    if was_running:
        lines.append(dashboard._stop_stack(hc_path))

    if _working_tree_dirty(hc_path) and not force:
        lines.append(
            f"{hc_path} has local changes - not updating. Commit/stash them yourself and retry, "
            "or re-run with force to discard them."
        )
        return "\n".join(lines)

    fetch = _git(hc_path, "fetch", "origin", "--tags", "--prune")
    if fetch.returncode != 0:
        lines.append(f"git fetch failed:\n{fetch.stdout}\n{fetch.stderr}")
        return "\n".join(lines)

    ref, label = resolve_channel_ref(hc_path, channel)
    if ref is None:
        lines.append(label)
        return "\n".join(lines)

    old_sha = _rev_parse(hc_path)
    old_version = _read_version(hc_path)

    checkout = _checkout(hc_path, channel, ref, force)
    if checkout.returncode != 0:
        lines.append(f"git checkout {ref} failed:\n{checkout.stdout}\n{checkout.stderr}")
        return "\n".join(lines)

    new_sha = _rev_parse(hc_path)
    new_version = _read_version(hc_path)

    if old_sha and old_sha == new_sha:
        lines.append(f"Already up to date ({label}, v{new_version}, {new_sha[:8]}).")
    else:
        lines.append(f"Updated v{old_version} ({old_sha[:8] or 'unknown'}) -> {label} v{new_version} ({new_sha[:8]}).")

        new_keys = _sync_new_env_keys(hc_path)
        if new_keys:
            lines.append(
                f"Added new .env key(s) introduced by this update: {', '.join(new_keys)} - "
                "review/fill these in (see .env.example) before relying on them."
            )

        changed = _changed_files(hc_path, old_sha, new_sha)
        if needs_rebuild(changed):
            lines.append("Dockerfile/requirements/compose changed since the last build - rebuilding images...")
            new_compose_args = _compose_args(hc_path)
            rc = rebuild_images.rebuild(new_compose_args, no_cache=False)
            lines.append("Image rebuild complete." if rc == 0 else f"Image rebuild failed (exit {rc}) - see output above.")

    # dashboard.py's own _doctor_items helper redirects this same output away
    # for the same reason - check() prints a full human-readable report to
    # stdout on every call (meant for a one-off `python3 doctor.py` run from
    # a terminal), which would otherwise spam the dashboard server's own
    # console on every single update. The returned summary line below is
    # what a caller actually needs; `python3 doctor.py` is the pointer to
    # the full report when there's something to fix.
    with contextlib.redirect_stdout(io.StringIO()):
        doctor_result = doctor.check(hc_path, skip_llm_probe=True, dry_run=True)
    if doctor_result.has_errors:
        lines.append(
            f"doctor.py found {len(doctor_result.errors())} error(s) after updating - "
            "run `python3 doctor.py` inside this checkout for details."
        )

    if was_running:
        lines.append(dashboard._start_stack(hc_path))
    else:
        lines.append("Stack was not running before the update - leaving it stopped.")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--hc-path", type=str, default=None,
        help="Path to the Horseless Carriage checkout to update (default: this script's own directory).",
    )
    parser.add_argument(
        "--channel", choices=CHANNELS, default="release",
        help="Update to the latest release tag (default), or track the `main` branch instead.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Proceed even if the checkout has local changes (discards them).",
    )
    args = parser.parse_args()

    hc_path = Path(args.hc_path).expanduser().resolve() if args.hc_path else Path(__file__).resolve().parent
    message = run_update(hc_path, channel=args.channel, force=args.force)
    print(message)
    if "failed" in message.lower() or "not updating" in message.lower():
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Cancelled.")
        sys.exit(0)
