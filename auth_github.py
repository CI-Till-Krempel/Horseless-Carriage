#!/usr/bin/env python
import os
import sys
import time
import jwt
import requests
import subprocess

def mint_installation_token(app_id: str, private_key_str: str, installation_id: str) -> str:
    """Mints a short-lived GitHub App installation access token via the
    REST API: signs a JWT as the App, then exchanges it for an installation
    token. Pure token fetch, no side effects on the host/container's own
    `gh` auth state - that's main()'s job below (actually logging this
    container's gh CLI in as the app). Kept separate so a read-only caller
    (doctor.py's live GitHub access check) can mint a token to test with
    without disturbing whatever `gh` is currently authenticated as."""
    private_key = private_key_str.replace("\\n", "\n").strip()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(url, headers=headers, timeout=10)
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError("GitHub did not return an installation access token.")
    return token


def main():
    """
    Authenticates the GitHub CLI using GitHub App credentials.
    This script generates an installation access token and configures gh.
    """
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key_str = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")

    if not all([app_id, private_key_str, installation_id]):
        print("WARNING: Missing GitHub App credentials. Skipping App authentication.", file=sys.stderr)
        return

    print("Authenticating as GitHub App...")

    try:
        token = mint_installation_token(app_id, private_key_str, installation_id)

        # Authenticate gh CLI with the token
        subprocess.run(["gh", "auth", "login", "--with-token"], input=token, text=True, check=True)
        print("GitHub App authentication successful.")
        subprocess.run(["gh", "auth", "status"], check=True)

    except Exception as e:
        print(f"ERROR: GitHub App authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
