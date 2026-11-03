#!/usr/bin/env python3
"""
Registers a dedicated GitHub App for Horseless Carriage and installs it on
the configured target repo (GITHUB_REPO_URL in .env) - the automated
alternative to docs/GITHUB-INTEGRATION.md "Option 2"'s fully manual
click-through (create app by hand, copy App ID/Installation ID/private key
one field at a time).

Uses GitHub's own "App Manifest flow" (https://docs.github.com/en/apps/
sharing-github-apps/registering-a-github-app-from-a-manifest): this script
serves a tiny local page that auto-submits a pre-filled app definition to
GitHub, you click GitHub's own "Create GitHub App" confirmation button (the
one human-in-the-loop step GitHub's security model requires - there is no
API that creates an App without it), and GitHub redirects back here with a
one-time code this script exchanges for real credentials (App ID + private
key) - no copy-pasting required. Installing the App onto a specific repo is
a second, separate human click-through (GitHub requires this too, for the
same reason) - this script opens that page for you as well and captures the
resulting Installation ID the same way, via the App's "Setup URL" redirect.

Both browser round-trips talk back to a local HTTP server this script starts
on an ephemeral port, so it only works out of the box when run on the same
machine as the browser completing them. Running this over SSH on a headless
box? Forward the port shown in the printed URL (`ssh -L <port>:localhost:<port>
<host>`) and open it from your local browser instead. Either callback can
also be completed manually: if this script's own local server doesn't see
the expected request within its wait window, it asks you to paste the final
URL your browser landed on instead of giving up.

Writes GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_APP_INSTALLATION_ID into
.env (same single-line "\\n"-escaped PEM convention .env.example already
documents - lib_env.update_env_var's own single-quoting handles the rest),
then verifies the result actually works against the target repo the same
way doctor.py does (lib_github.check_repo_access), when PyJWT/requests
(requirements.txt) happen to be importable on this host - skipped,
not failed, otherwise; run doctor.py inside the agent container (or after
`pip install -r requirements.txt`) to verify in that case instead.

Usage:
  python3 setup_github_app.py [--owner-repo OWNER/REPO] [--timeout SECONDS]
"""
import argparse
import html
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import lib_env
import lib_github

API_BASE = "https://api.github.com"
DEFAULT_WAIT_SECONDS = 600
_PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}


def info(msg: str) -> None:
    print(f">> {msg}")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _build_manifest(owner: str, repo: str, redirect_url: str, setup_url: str) -> dict:
    # GH issue tracker: App names are unique GLOBALLY across GitHub, not just
    # within an org - the random suffix avoids a near-certain collision on
    # an obvious name like "Horseless Carriage"; GitHub's own confirmation
    # page still lets you edit it before creating if you'd prefer something
    # else (or if even this collides).
    suffix = secrets.token_hex(3)
    return {
        "name": f"Horseless Carriage - {owner}-{repo} ({suffix})",
        "url": f"https://github.com/{owner}/{repo}",
        # No "hook_attributes" key at all - this App needs no webhook.
        # GitHub's manifest validator requires hook_attributes.url whenever
        # hook_attributes is present (even with active=false, which only
        # means "don't overwrite the hook URL on conversion", not "no
        # webhook") - omitting the whole key is the actual way to get a
        # webhook-less App, and the real fix for the confusing "'url' wasn't
        # supplied" error a lone {"active": False} produces instead.
        "redirect_url": redirect_url,
        "setup_url": setup_url,
        "public": False,
        # Exactly the permission set docs/GITHUB-INTEGRATION.md's manual
        # "Option 2" instructions already document - keep these two in sync.
        "default_permissions": dict(_PERMISSIONS),
    }


def _manifest_page_html(manifest: dict, state: str) -> str:
    manifest_json = html.escape(json.dumps(manifest), quote=True)
    return f"""<!doctype html>
<html><body onload="document.forms[0].submit()">
<p>Redirecting to GitHub to create the app&hellip;</p>
<form action="https://github.com/settings/apps/new?state={html.escape(state, quote=True)}" method="post">
<input type="hidden" name="manifest" value="{manifest_json}">
</form>
</body></html>
"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Serves exactly the routes one phase of the flow needs: an optional
    `root_html` page (the manifest auto-submit form, phase 1 only) and a
    `callback_path` that captures whatever query params GitHub redirects
    back with. Stores the result on the server instance so the caller can
    read it back once `serve_forever`'s single iteration returns."""

    def log_message(self, *args):  # quieter than the default per-request stderr log
        pass

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/" and self.server.root_html is not None:
            body = self.server.root_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == self.server.callback_path:
            self.server.result = dict(urllib.parse.parse_qsl(parsed.query))
            body = b"<html><body>Done - you can close this tab and return to the terminal.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def _wait_for_callback(port: int, callback_path: str, root_html: str = None, timeout_seconds: int = DEFAULT_WAIT_SECONDS):
    """Starts a local HTTP server on `port`, serving `root_html` (if given)
    at `/` and capturing whichever query params land on `callback_path`.
    Returns that dict, or None if nothing arrived within timeout_seconds
    (Ctrl+C also returns None, treated as "fall back to manual paste" by
    the caller rather than aborting outright)."""
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.root_html = root_html
    server.callback_path = callback_path
    server.result = None
    server.timeout = 1.0
    deadline = time.monotonic() + timeout_seconds
    try:
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server.result


def _manual_paste_fallback(expected_path: str) -> dict:
    print()
    print(f"Didn't see the browser come back within the wait window.")
    print(f"If you completed that step, paste the full URL your browser landed on")
    print(f"(it should contain '{expected_path}?...') here, or press Enter to give up:")
    pasted = input("> ").strip()
    if not pasted:
        return None
    parsed = urllib.parse.urlsplit(pasted)
    return dict(urllib.parse.parse_qsl(parsed.query))


def _api_post(path: str, data: dict = None) -> dict:
    body = json.dumps(data).encode("utf-8") if data is not None else b""
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=body, method="POST",
        headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        die(f"GitHub API call to {path} failed (HTTP {e.code}): {detail[:300]}")


def _free_port() -> int:
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_port
    server.server_close()
    return port


def register_and_install(owner: str, repo: str, env_path: Path, timeout_seconds: int) -> None:
    port = _free_port()
    redirect_url = f"http://localhost:{port}/callback"
    setup_url = f"http://localhost:{port}/installed"
    state = secrets.token_hex(16)
    manifest = _build_manifest(owner, repo, redirect_url, setup_url)

    info(f"Creating a new GitHub App for {owner}/{repo} - opening your browser so you can review and")
    info("confirm it on GitHub's own page (permissions: contents/pull_requests write, metadata read).")
    info(f"If nothing opens, visit http://localhost:{port}/ yourself.")
    root_url = f"http://localhost:{port}/"
    opened = webbrowser.open(root_url)
    if not opened:
        warn(f"Could not auto-open a browser - visit {root_url} manually.")

    result = _wait_for_callback(port, "/callback", root_html=_manifest_page_html(manifest, state), timeout_seconds=timeout_seconds)
    if result is None:
        result = _manual_paste_fallback("/callback")
    if not result or "code" not in result:
        die("No app-creation code received - aborting. Re-run this script to try again.")
    if result.get("state") != state:
        die("The 'state' GitHub echoed back doesn't match what this run sent - aborting for safety. Re-run to try again.")

    info("Exchanging the one-time code for real App credentials...")
    conversion = _api_post(f"/app-manifests/{result['code']}/conversions")
    app_id = str(conversion.get("id") or "")
    pem = conversion.get("pem") or ""
    slug = conversion.get("slug") or ""
    if not app_id or not pem or not slug:
        die(f"GitHub's response was missing expected fields: {json.dumps(conversion)[:300]}")

    escaped_pem = pem.replace("\n", "\\n")
    lib_env.update_env_var(env_path, "GITHUB_APP_ID", app_id)
    lib_env.update_env_var(env_path, "GITHUB_APP_PRIVATE_KEY", escaped_pem)
    info(f"App '{conversion.get('name')}' created (App ID {app_id}) - GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY written to .env.")

    install_url = f"https://github.com/apps/{slug}/installations/new"
    info(f"Now install it on {owner}/{repo} - opening the installation picker. Choose 'Only select")
    info(f"repositories' and pick {repo} specifically, not every repo you own.")
    info(f"If nothing opens, visit {install_url} yourself.")
    opened = webbrowser.open(install_url)
    if not opened:
        warn(f"Could not auto-open a browser - visit {install_url} manually.")

    result = _wait_for_callback(port, "/installed", timeout_seconds=timeout_seconds)
    if result is None:
        result = _manual_paste_fallback("/installed")
    if not result or "installation_id" not in result:
        die(
            "No installation id received - the App was created (GITHUB_APP_ID is already set) but "
            "isn't installed on the repo yet. Re-run this script, or install it manually at "
            f"{install_url} and copy the Installation ID from the post-install URL into "
            "GITHUB_APP_INSTALLATION_ID in .env yourself."
        )

    lib_env.update_env_var(env_path, "GITHUB_APP_INSTALLATION_ID", result["installation_id"])
    info(f"Installed on {owner}/{repo} (Installation ID {result['installation_id']}) - GITHUB_APP_INSTALLATION_ID written to .env.")

    _verify(owner, repo, env_path)


def _verify(owner: str, repo: str, env_path: Path) -> None:
    env = lib_env.load_env_file(env_path)
    token, source = lib_github.resolve_token(env)
    if source != "app":
        warn("Could not mint a token to verify with (PyJWT/requests not importable on this host - this "
             "is expected outside the agent container). Run doctor.py inside the container, or after "
             "`pip install -r requirements.txt`, to verify access.")
        return
    if token is None:
        warn("Credentials are written to .env, but minting a token with them just failed - double-check "
             "the App was actually installed, then re-run doctor.py to see the real error.")
        return
    ok, detail = lib_github.check_repo_access(owner, repo, token)
    (info if ok else warn)(detail)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-repo", help="Target 'owner/repo' - defaults to .env's GITHUB_REPO_URL.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_WAIT_SECONDS,
                         help=f"Seconds to wait for each browser round-trip before offering to paste the URL manually (default {DEFAULT_WAIT_SECONDS}).")
    args = parser.parse_args()

    env_path = Path(".env")
    if not env_path.is_file():
        die(".env not found - run setup_project.py/setup_llm.py first.")

    if args.owner_repo:
        if "/" not in args.owner_repo:
            die("--owner-repo must look like 'owner/repo'.")
        owner, repo = args.owner_repo.split("/", 1)
    else:
        repo_url = lib_env.read_env_var(env_path, "GITHUB_REPO_URL")
        parsed = lib_github.parse_owner_repo(repo_url)
        if not parsed:
            die("GITHUB_REPO_URL isn't set in .env (or doesn't look like a github.com repo URL) - run "
                "setup_llm.py first, or pass --owner-repo owner/repo explicitly.")
        owner, repo = parsed

    if lib_github.github_app_fully_configured(lib_env.load_env_file(env_path)):
        warn("GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_APP_INSTALLATION_ID are already set in .env.")
        if input("Create and install a NEW GitHub App anyway, overwriting these? [y/N]: ").strip().lower() not in ("y", "yes"):
            info("Leaving the existing configuration untouched.")
            return

    register_and_install(owner, repo, env_path, args.timeout)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        die("Cancelled.")
