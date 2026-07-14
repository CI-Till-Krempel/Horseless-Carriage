# agents/scrum_team/tools/base.py
from __future__ import annotations
import os
import subprocess
import base64
from pathlib import Path
from typing import Any, Dict

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]

def _configured_repo_root(tool_context=None) -> Path:
    """
    Determine which repository directory to operate in.
    Preference order:
    - INTERNAL_STATE_REPO_PATH environment variable (Docker mount point)
    - STATE_REPO_PATH environment variable (User override)
    - tool_context.state['repo']['local_path'] if present
    - current project root (fallback)
    """
    # 1. Internal path (highest priority for Docker environment)
    internal_path = os.getenv("INTERNAL_STATE_REPO_PATH")
    if internal_path:
        return Path(internal_path).resolve()
        
    # 2. Public environment variable
    if os.getenv("STATE_REPO_PATH"):
        return Path(os.getenv("STATE_REPO_PATH")).expanduser().resolve()
    
    # 3. State-persisted path
    try:
        if tool_context and getattr(tool_context, "state", None):
            repo_cfg = tool_context.state.get("repo", {}) or {}
            p = repo_cfg.get("local_path")
            if p:
                return Path(p).expanduser().resolve()
    except Exception:
        pass
    return _project_root()

def _run(cmd: list[str], cwd: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Run a shell command non-interactively and capture output.
    Injects GH_TOKEN if present in session.state.
    """
    env = os.environ.copy()
    if tool_context and getattr(tool_context, "state", None):
        token = tool_context.state.get("github_token")
        if token:
            env["GH_TOKEN"] = token
            env["GITHUB_TOKEN"] = token
            
            # If it's a git command, inject authentication and SSH-to-HTTPS translation
            if cmd and cmd[0] == "git":
                auth_value = base64.b64encode(f"x-access-token:{token}".encode()).decode()
                git_overrides = [
                    "-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth_value}",
                    "-c", "url.https://github.com/.insteadOf=git@github.com:",
                    "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/"
                ]
                # Insert overrides right after 'git' but before the subcommand
                cmd = [cmd[0]] + git_overrides + cmd[1:]

        # Set git user name/email based on current agent if available
        agent_name = getattr(tool_context, "agent_name", None)
        if agent_name:
            env["GIT_AUTHOR_NAME"] = agent_name
            env["GIT_AUTHOR_EMAIL"] = f"{agent_name.lower().replace(' ', '_')}@horseless-carriage.local"
            env["GIT_COMMITTER_NAME"] = agent_name
            env["GIT_COMMITTER_EMAIL"] = f"{agent_name.lower().replace(' ', '_')}@horseless-carriage.local"

    try:
        p = subprocess.run(
            cmd,
            cwd=cwd or str(_project_root()),
            text=True,
            capture_output=True,
            check=False,
            env=env,
            errors="replace",
        )
        return {
            "status": "ok" if p.returncode == 0 else "error",
            "returncode": p.returncode,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(),
            "cmd": cmd,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "cmd": cmd}

def _normalize_private_key(key: str) -> str:
    """
    Robustly normalize a PEM private key from various formats.
    """
    if not key:
        return ""
    
    # 1. Handle escaped newlines (e.g. from .env parsing)
    clean = key.replace("\\n", "\n").replace("\\r", "").strip()
    
    # 2. If it's a smashed single line but has PEM headers, try to restore structure
    if "-----BEGIN" in clean and "\n" not in clean:
        # Common headers
        headers = [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN ANY PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----"
        ]
        footers = [
            "-----END RSA PRIVATE KEY-----",
            "-----END PRIVATE KEY-----",
            "-----END ANY PRIVATE KEY-----",
            "-----END OPENSSH PRIVATE KEY-----"
        ]
        
        for h in headers:
            if h in clean:
                # Re-insert newlines
                clean = clean.replace(h, h + "\n")
                break
        for f in footers:
            if f in clean:
                clean = clean.replace(f, "\n" + f)
                break
                
    # Ensure it ends with a newline if it doesn't
    if not clean.endswith("\n"):
        clean += "\n"
        
    return clean

def _state_file_path(repo_root: Path) -> Path:
    return (repo_root / ".hc" / "state.json").resolve()