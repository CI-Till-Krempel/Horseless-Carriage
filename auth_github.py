#!/usr/bin/env python
import os
import sys
import time
import jwt
import requests
import subprocess

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
        # Normalize the private key
        private_key = private_key_str.replace("\\n", "\n").strip()

        # 1. Generate JWT
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": app_id,
        }
        encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

        # 2. Exchange for Installation Access Token
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {encoded_jwt}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        token = token_data.get("token")

        if not token:
            print("ERROR: Failed to get installation access token.", file=sys.stderr)
            sys.exit(1)

        # 3. Authenticate gh CLI with the token
        subprocess.run(["gh", "auth", "login", "--with-token"], input=token, text=True, check=True)
        print("GitHub App authentication successful.")
        subprocess.run(["gh", "auth", "status"], check=True)

    except Exception as e:
        print(f"ERROR: GitHub App authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
