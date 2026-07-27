# agents/scrum_team/tools/base.py
from __future__ import annotations
import os
import subprocess
import base64
from pathlib import Path
from typing import Any, Dict

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]

def _hc_version() -> str:
    """
    Reads the Horseless Carriage version from the VERSION file at the
    project root (see RELEASE.md). Recorded per-session into
    ScrumState.hc_version rather than fabricated, so a sprint report is
    traceable back to the tool version that actually produced it.
    """
    try:
        return (_project_root() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"

def _record_touched_file(rel_path: str, tool_context=None) -> None:
    """
    Records a repo-relative path in ScrumState.sprint_files_touched, so a
    later release-PR check can verify every sprint write actually landed in
    the release. De-duplicates repeated writes to the same path.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return
    touched = list(tool_context.state.get("sprint_files_touched", []))
    if rel_path not in touched:
        touched.append(rel_path)
        tool_context.state["sprint_files_touched"] = touched

def _eval_run_id() -> str | None:
    """
    Set only by the eval harness (run_eval.py) via EVAL_RUN_ID - never by
    real/production usage. Lets branch/PR-creating tools tag their output
    with the run it belongs to, so multiple eval runs sharing one eval repo
    don't produce indistinguishable branches/PRs (see _with_eval_branch_prefix
    and _with_eval_title_prefix).
    """
    return os.environ.get("EVAL_RUN_ID") or None

def _with_eval_branch_prefix(branch: str) -> str:
    run_id = _eval_run_id()
    if not run_id:
        return branch
    prefix = f"eval-{run_id}/"
    return branch if branch.startswith(prefix) else f"{prefix}{branch}"

def _with_eval_title_prefix(title: str) -> str:
    run_id = _eval_run_id()
    if not run_id:
        return title
    tag = f"[eval-{run_id}] "
    return title if title.startswith(tag) else f"{tag}{title}"

def _default_push_branch(tool_context=None) -> str:
    """
    The repo's configured default branch for pushes/PR bases (e.g. seeding,
    release PRs). Preference order mirrors _configured_repo_root: the
    branch configure_github_repo recorded in state, then the
    GITHUB_REPO_BRANCH env var, then "main". Exists so an eval/test run
    can point every push/PR at an isolated branch via GITHUB_REPO_BRANCH
    instead of a hardcoded "main" contaminating the real default branch.
    """
    try:
        if tool_context and getattr(tool_context, "state", None):
            repo_cfg = tool_context.state.get("repo", {}) or {}
            branch = repo_cfg.get("default_branch")
            if branch:
                return branch
    except Exception:
        pass
    return os.getenv("GITHUB_REPO_BRANCH") or "main"

def _develop_branch_name(tool_context=None) -> str:
    """
    The integration branch feature-branch PRs target (GitFlow: feature/* ->
    develop -> main). Exact mirror of _default_push_branch's resolution
    order: state config -> GITHUB_DEVELOP_BRANCH env var -> "develop". Like
    _default_push_branch, an eval run points this at an isolated,
    run-specific value via GITHUB_DEVELOP_BRANCH rather than relying on
    _with_eval_branch_prefix (that helper is for ad-hoc branches like
    feature/* instead - see run_eval.py).
    """
    try:
        if tool_context and getattr(tool_context, "state", None):
            repo_cfg = tool_context.state.get("repo", {}) or {}
            branch = repo_cfg.get("develop_branch")
            if branch:
                return branch
    except Exception:
        pass
    return os.getenv("GITHUB_DEVELOP_BRANCH") or "develop"

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

def _redact_cmd(cmd: list[str]) -> list[str]:
    """
    Returns a copy of cmd with the injected git AUTHORIZATION header (see
    _run below) masked out. The real cmd is still what's actually executed -
    only the copy returned to callers is redacted, so a tool result never
    echoes a reversible base64-encoded GitHub token back into a transcript,
    sprint report, or log.
    """
    redacted = []
    for arg in cmd:
        if isinstance(arg, str) and "AUTHORIZATION: Basic " in arg:
            prefix = arg.split("AUTHORIZATION: Basic ", 1)[0]
            redacted.append(prefix + "AUTHORIZATION: Basic ***REDACTED***")
        else:
            redacted.append(arg)
    return redacted

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
                    "-c", f"http.https://github.com/.extraheader=AUTHORIZATION: Basic {auth_value}",
                    "-c", "url.https://github.com/.insteadOf=git@github.com:",
                    "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/",
                    "-c", "url.https://github.com/.pushInsteadOf=git@github.com:",
                    "-c", "url.https://github.com/.pushInsteadOf=ssh://git@github.com/"
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
            "cmd": _redact_cmd(cmd),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "cmd": _redact_cmd(cmd)}

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