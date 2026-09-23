# agents/scrum_team/tools/base.py
from __future__ import annotations
import ast
import json
import os
import re
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

_SAFE_DIRECTORIES_CONFIGURED: set[str] = set()
_SAFE_DIRECTORY_WILDCARD_SET = False


def _ensure_git_safe_directory(path: Path) -> None:
    """
    Marks `path` as a git safe.directory, once per process. Without this,
    every git command run inside this repo fails with "fatal: detected
    dubious ownership in repository at ..." whenever the process's EUID
    doesn't match the directory's owning UID (git's CVE-2022-24765
    mitigation) - which happens whenever STATE_REPO_PATH/
    INTERNAL_STATE_REPO_PATH is a bind-mounted directory created by a
    different user than the one running inside the container.

    A real ADK eval run hit exactly this: run_adk_eval.py's
    prepare_scratch_state_repo() creates the scratch state repo on the
    HOST, bind-mounted into the agent container at /app/state_repo - and
    both run_adk_eval.py and the test suite (run_tests.py) deliberately
    invoke the container with --entrypoint "" (entrypoint.sh's own git
    config setup is skipped, since the test suite mocks every external
    call and has no need for it - but the ADK eval run does real git
    operations against a real bind-mounted repo, so nothing else in that
    path ever configures safe.directory for it). Every start_feature_branch
    call failed its `git checkout develop` this way, cascading into a
    fabricated "story doesn't exist" derailment instead of ever reaching
    the branch-protection behavior the eval case exists to test (GH issue
    tracking this: see docs/EVALUATION.md).

    Marks `*` rather than just `key` itself: prepare_scratch_state_repo
    also creates a local bare "origin" remote *inside* the same working
    tree (STATE_REPO_SCRATCH_REMOTE_SUBDIR, e.g. `./.state-repo-remote.git`
    - registered via a relative URL so it resolves correctly under both
    the host path and the container's bind-mounted one). git treats that
    remote as its own repository with its own ownership check when a
    push/fetch accesses it over the local filesystem transport - marking
    only the parent working tree safe left git_push failing on exactly
    this nested path ("dubious ownership in repository at
    './.state-repo-remote.git'") even after the working tree itself was
    fixed. There's no fixed set of nested repo paths this function could
    otherwise enumerate (that subdirectory name is an eval-harness detail
    this module has no business knowing about), and the security property
    safe.directory protects against - an unrelated user planting a repo in
    a shared multi-user path - doesn't apply once a real UID mismatch is
    already confirmed on a path this process was explicitly configured to
    operate on: at that point every git repository anywhere under this
    single-tenant container's filesystem is equally this process's own.

    Fixed defensively here, at the single choke point every git-invoking
    tool already calls to resolve which repo to operate in, rather than in
    the container entrypoint/compose plumbing - this self-heals regardless
    of how the container is invoked (which has already changed twice for
    unrelated reasons), and covers any future real deployment where
    STATE_REPO_PATH happens to be a bind-mounted/network volume owned by a
    different user, not just this eval path.

    This function is called from _configured_repo_root on every single
    tool call that resolves a repo path - including every test in this
    suite, since conftest.py's _isolated_repo_root fixture always calls
    through to the real _configured_repo_root first. Shelling out to `git
    config` unconditionally here would mean hundreds of subprocess calls
    per test run, each permanently appending an entry to whatever
    .gitconfig the test process writes to. Checking ownership first (the
    exact condition git itself checks) means the subprocess only ever runs
    when there's a real mismatch to fix - which is never true for a
    same-user tmp_path in a test, only for a genuinely different-owner
    bind mount like the eval's scratch state repo.

    Two independent memoization layers: _SAFE_DIRECTORIES_CONFIGURED (per
    resolved path) means a path already confirmed same-owner needn't be
    re-stat'd on a later call, while _SAFE_DIRECTORY_WILDCARD_SET means
    once the wildcard has actually been written, a *different* mismatched
    path seen later in the same process skips the git config subprocess
    entirely (it's already covered) but still needs its own stat check
    recorded so repeat calls for that same path short-circuit too.

    Every failure mode here - the path not existing yet, os.geteuid() not
    being available (Windows), the stat or git call itself failing - is
    swallowed: this must never be what makes a tool call raise instead of
    returning a normal error result, and the actual git command about to
    run will surface its own clear error anyway if the real problem is
    something else entirely.
    """
    global _SAFE_DIRECTORY_WILDCARD_SET
    key = str(path)
    if key in _SAFE_DIRECTORIES_CONFIGURED:
        return
    _SAFE_DIRECTORIES_CONFIGURED.add(key)
    try:
        if path.stat().st_uid == os.geteuid() or _SAFE_DIRECTORY_WILDCARD_SET:
            return
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "*"],
            capture_output=True,
            timeout=10,
        )
        _SAFE_DIRECTORY_WILDCARD_SET = True
    except Exception:
        pass


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
        resolved = Path(internal_path).resolve()
        _ensure_git_safe_directory(resolved)
        return resolved

    # 2. Public environment variable
    if os.getenv("STATE_REPO_PATH"):
        resolved = Path(os.getenv("STATE_REPO_PATH")).expanduser().resolve()
        _ensure_git_safe_directory(resolved)
        return resolved

    # 3. State-persisted path
    try:
        if tool_context and getattr(tool_context, "state", None):
            repo_cfg = tool_context.state.get("repo", {}) or {}
            p = repo_cfg.get("local_path")
            if p:
                resolved = Path(p).expanduser().resolve()
                _ensure_git_safe_directory(resolved)
                return resolved
    except Exception:
        pass
    return _project_root()

# GH issue #128: general-purpose text redaction for anything about to be
# persisted (transcript/messages state, sprint reports) or logged, distinct
# from _redact_cmd below (which only ever masked the one injected git auth
# header). Conversation text isn't scanned for secrets at all otherwise - a
# user pasting a real credential into a prompt, or a tool result echoing one
# back, would flow straight into ScrumState.transcript/messages and then
# into whatever consumes them, unredacted. Patterns cover the token shapes
# actually used in or by this codebase: GitHub tokens (classic and
# fine-grained PATs), OpenAI/LiteLLM-style "sk-..." keys (including the
# per-agent virtual keys create_litellm_virtual_key mints), and Bearer/Basic
# auth headers. Not a general secret scanner - it can't catch a shape it
# doesn't know about - but closes the specific gaps flagged in SECURITY.md.
_SECRET_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "***REDACTED-GH-TOKEN***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "***REDACTED-GH-TOKEN***"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "***REDACTED-KEY***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_.=]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(authorization:\s*basic\s+)[A-Za-z0-9+/=]+"), r"\1***REDACTED***"),
]


def _redact_secrets(text: str) -> str:
    """Masks known secret shapes (see _SECRET_PATTERNS) in free-form text
    before it's stored/logged. Only the recorded copy is affected - callers
    still act on the real, unredacted text."""
    if not text:
        return text
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


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

# Default ceiling for every subprocess this tool layer invokes (git, gh) -
# see GH issue #113: without any timeout at all, a network stall on a `git
# push`, `gh` falling back to an interactive prompt, or any other hang left
# the entire agent session unresponsive indefinitely, with no way to tell
# a slow LLM call apart from a genuinely stuck subprocess. 120s is generous
# for a single git/gh invocation while still bounding the worst case.
_DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 120


def _looks_like_stale_github_app_token(stdout: str, stderr: str) -> bool:
    """
    True if a git/gh command's output is GitHub rejecting the credential
    itself (not some unrelated 401, e.g. a genuinely wrong repo URL) -
    "Bad credentials" is GitHub's own exact API error string (both `gh` and
    a raw REST call surface it verbatim), and "Authentication failed"/
    "could not read Username" are git's own HTTPS-credential-rejection
    wording. Deliberately string-matched rather than exit-code-matched -
    both tools exit 1 for a wide range of unrelated failures too.
    """
    combined = f"{stdout}\n{stderr}".lower()
    return (
        "bad credentials" in combined
        or "authentication failed" in combined
        or "could not read username" in combined
        or "terminal prompts disabled" in combined
    )


def _refresh_github_app_token(tool_context) -> bool:
    """
    Re-mints a fresh GitHub App installation access token and stores it in
    session state, for `_run` to retry a git/gh call that just failed with
    a stale/expired one. GitHub hard-caps installation tokens at 60 minutes
    (not configurable, not something raising a limit anywhere fixes) - a
    real incident: a sprint running longer than that saw every subsequent
    `git push`/`gh` call fail with "Bad credentials", because
    configure_github_app (see init_scrum_state) only ever mints a token
    once per session and nothing ever refreshed it.

    Uses GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_APP_INSTALLATION_ID
    from the environment - the same source init_scrum_state's own first
    mint uses - never anything from session.state (the private key is
    deliberately never persisted there - see tools/scrum.py's
    REPO_STATE_KEYS note on secrets). Returns False, doing nothing, if this
    session was never on GitHub App auth in the first place (a plain
    GITHUB_TOKEN personal access token doesn't expire this way and has
    nothing to refresh) or those env vars aren't set - the caller must
    still surface the original failure in that case.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return False
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    if not (app_id and private_key and installation_id):
        return False
    from .github import configure_github_app  # deferred: github.py imports _run from this module
    result = configure_github_app(app_id, private_key, installation_id, tool_context=tool_context)
    return result.get("status") == "ok"


def _run(cmd: list[str], cwd: str | None = None, tool_context=None,
         timeout: float = _DEFAULT_SUBPROCESS_TIMEOUT_SECONDS, env_overrides: dict | None = None,
         _retry_on_auth_failure: bool = True) -> Dict[str, Any]:
    """
    Run a shell command non-interactively and capture output.
    Injects GH_TOKEN if present in session.state.

    stdin is explicitly closed (DEVNULL) - without it, `gh` falling back to
    an interactive prompt for a credential it can't resolve non-interactively
    would otherwise block waiting for input that can never arrive here. A
    timeout (default _DEFAULT_SUBPROCESS_TIMEOUT_SECONDS) bounds every other
    hang (a network stall on `git push`, `gh pr checks --watch`, etc.) -
    see GH issue #113. GIT_TERMINAL_PROMPT=0 below is belt-and-suspenders
    with DEVNULL specifically for git's own HTTPS credential helper, which a
    real incident showed can still stall for the full timeout on a stale
    token rather than failing immediately.

    A stale GitHub App installation token (_looks_like_stale_github_app_token)
    is retried exactly once, transparently, after re-minting a fresh one
    (_refresh_github_app_token) - see that function's docstring. `cmd` is
    the pristine, pre-auth-injection argument the caller passed; the retry
    re-enters this function from scratch with `_retry_on_auth_failure=False`
    so a second consecutive failure (refresh itself failed, or the App's
    permissions were actually revoked rather than just expired) surfaces
    normally instead of looping.

    env_overrides: applied last, after the GH_TOKEN/git-identity injection
    below - see _execute_test_suite_coverage (tools/quality.py, ISSUE-0047)
    for why PYTHONPATH specifically needs this.
    """
    original_cmd = cmd
    env = os.environ.copy()
    if cmd and cmd[0] == "git":
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        # Unconditional, even with no token below: a repo_url can be
        # git@github.com:... (see .env.example's GITHUB_REPO_URL) before any
        # token has ever been seeded into session state (e.g. the very first
        # configure_github_repo call of a session), in which case the HTTPS
        # rewrite below never fires and git falls through to a genuine SSH
        # connection. Without this, that connection hits an unknown-host-key
        # prompt against a container with no known_hosts/SSH agent and a
        # closed stdin (see this function's DEVNULL below) - ssh can't read
        # an answer, so it hangs until the timeout, and the orchestrator
        # just retries the same failing call, appearing to hang forever with
        # no reply. BatchMode=yes makes ssh fail fast instead of prompting;
        # StrictHostKeyChecking=accept-new does TOFU pinning non-interactively
        # (standard for CI/automation) instead of blocking on confirmation.
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=/tmp/.hc_ssh_known_hosts",
        )
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

    if env_overrides:
        env.update(env_overrides)

    try:
        p = subprocess.run(
            cmd,
            cwd=cwd or str(_project_root()),
            text=True,
            capture_output=True,
            check=False,
            env=env,
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        if (
            p.returncode != 0
            and _retry_on_auth_failure
            and original_cmd
            and original_cmd[0] in ("git", "gh")
            and _looks_like_stale_github_app_token(p.stdout, p.stderr)
            and _refresh_github_app_token(tool_context)
        ):
            return _run(original_cmd, cwd, tool_context, timeout, env_overrides, _retry_on_auth_failure=False)
        return {
            "status": "ok" if p.returncode == 0 else "error",
            "returncode": p.returncode,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(),
            "cmd": _redact_cmd(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"Command timed out after {timeout}s: {' '.join(_redact_cmd(cmd))}",
            "cmd": _redact_cmd(cmd),
            "timed_out": True,
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

def _coerce_dict_arg(value: Any, tool_name: str) -> Dict[str, Any]:
    """
    Recovers a dict-typed tool argument from the string shapes a local model
    actually emits instead of a real object - a real eval run crashed
    upsert_story with `TypeError: 'str' object does not support item
    assignment` because a model emitted a JSON-encoded string
    (upsert_story('{"title": "..."}')) instead of a real object, and a
    separate case had update_sprint_report handed a Python repr - i.e.
    str(some_dict), single-quoted with True/False/None
    (update_sprint_report("{'team_effectiveness': ...}")) - which
    json.loads can't parse at all. ast.literal_eval safely covers that
    second shape without eval()'s arbitrary-code-execution risk. Anything
    else that still isn't a dict is a genuine caller error, not a
    serialization quirk, and is raised as ValueError so the caller can turn
    it into a normal tool-level error response instead of an uncaught crash.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parsed, dict):
                return parsed
    raise ValueError(f"{tool_name} expected an object, got {type(value).__name__}: {value!r}")


def _state_file_path(repo_root: Path) -> Path:
    return (repo_root / ".hc" / "state.json").resolve()