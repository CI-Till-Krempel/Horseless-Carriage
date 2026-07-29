"""
Shared helpers for verifying that the configured GitHub credentials
actually work against the target repo - not just that a credential is
*present* in .env, which is all doctor.py's GitHub section checked before
(GH issue #60). Only the API calls themselves need network access; parsing
is stdlib-only (urllib). Minting a GitHub App installation token reuses
auth_github.mint_installation_token, which needs PyJWT + requests (already
required by requirements.txt for that same reason) - resolve_token()
degrades to "unavailable" rather than raising if those aren't importable,
so a GITHUB_TOKEN-only setup never needs them at all.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Optional, Tuple

API_BASE = "https://api.github.com"


def parse_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    """Extracts (owner, repo) from a git@github.com:owner/repo.git or
    https://github.com/owner/repo(.git) GITHUB_REPO_URL value; None if the
    URL is empty or doesn't look like a github.com repo URL at all."""
    if not url:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def resolve_token(env: dict) -> Tuple[Optional[str], str]:
    """Returns (token, source) - source is "token", "app", or "" (nothing
    configured). token is None if a GitHub App trio is configured but a
    token couldn't actually be minted (PyJWT/requests unavailable, or the
    mint call itself failed) - the caller should report that distinctly
    from "nothing configured at all", already the case doctor.py's
    existing presence-only check reports."""
    if env.get("GITHUB_TOKEN"):
        return env["GITHUB_TOKEN"], "token"

    app_id = env.get("GITHUB_APP_ID")
    private_key = env.get("GITHUB_APP_PRIVATE_KEY")
    installation_id = env.get("GITHUB_APP_INSTALLATION_ID")
    if app_id and private_key and installation_id:
        try:
            import auth_github
            return auth_github.mint_installation_token(app_id, private_key, installation_id), "app"
        except Exception:
            return None, "app"

    return None, ""


def _api_get(path: str, token: str) -> Tuple[int, str]:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def check_repo_access(owner: str, repo: str, token: str) -> Tuple[bool, str]:
    """Best-effort, non-invasive check: confirms the token can read the
    repo, list issues, and list pull requests - the surfaces the agent's
    own GitHub tools actually need (GH issue #60: "without the ability to
    read from, write to and read/write pull requests and issues, the setup
    is not complete"). Reports the repo-level `permissions.push` flag as a
    proxy for write access, but doesn't attempt an actual write - GitHub
    has no safe, side-effect-free way to test write access to a specific
    resource type, and a fine-grained PAT/App installation can restrict
    Issues and Pull requests independently of that one summary flag - so
    "ok" here means "reads succeed and the repo-level summary looks
    writable", not a guarantee every write will succeed."""
    status, body = _api_get(f"/repos/{owner}/{repo}", token)
    if status != 200:
        return False, f"repo read failed (HTTP {status or 'network error'}) for {owner}/{repo}: {body[:200]}"

    push_ok = False
    try:
        push_ok = bool(json.loads(body).get("permissions", {}).get("push"))
    except Exception:
        pass

    status, body = _api_get(f"/repos/{owner}/{repo}/issues?per_page=1&state=all", token)
    if status != 200:
        return False, f"issues read failed (HTTP {status}) for {owner}/{repo}: {body[:200]}"

    status, body = _api_get(f"/repos/{owner}/{repo}/pulls?per_page=1&state=all", token)
    if status != 200:
        return False, f"pull requests read failed (HTTP {status}) for {owner}/{repo}: {body[:200]}"

    if push_ok:
        return True, f"read access to {owner}/{repo} confirmed (issues, pull requests); repo-level permissions include push"
    return False, (f"read access to {owner}/{repo} confirmed (issues, pull requests), but repo-level "
                    f"permissions do NOT include push - writes (PR merges, comments) will likely fail")
