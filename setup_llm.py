#!/usr/bin/env python3
"""
Interactive LLM provider/model setup for Horseless Carriage.

Walks you through:
  1. Picking a provider (Google Gemini, Anthropic Claude, OpenAI, or fully
     local via Ollama).
  2. Entering the provider's API key (cloud providers only) and fetching its
     CURRENT list of available models via the provider's own API, so you're
     not stuck picking from a hardcoded/stale list.
  3. Picking a main model (used by all scrum-team roles) and, for cloud
     providers, an optional cheaper/faster model for the automated eval
     harness's "scrum-eval-cheap" alias. Re-running this script prefills
     whatever's already configured as the default, rather than always
     resetting to the freshly fetched list's first (newest) entry.
  4. For the local/Ollama provider only: detecting whether this machine has
     a usable NVIDIA GPU (via nvidia-smi) and asking whether to enable GPU
     acceleration, recommending "yes" if one was detected - see docs/SETUP.md's
     "GPU Support" section.
  5. Writing the result into .env and into the active litellm.yaml (or, for
     the local/Ollama provider, into config/model-templates/litellm.local-ollama.yaml).
  6. Setting the Git user name/email used for commits the agent makes on
     your behalf, and setting up the STATE_REPO_PATH "state repository"
     itself: creates the directory if missing, then either clones
     GITHUB_REPO_URL into it (if empty and a URL is given) or initializes a
     fresh local git repo there - so it's ready to use with no extra manual
     steps (see README.md "State Repository").
  7. Setting a human interaction level and sprint token/USD budgets +
     maximum process overhead percentage (see docs/INTERACTION-LEVELS.md and
     .env.example's "Sprint Budget & Resource Configuration") - also
     prefilled from whatever's already configured on a re-run.
  8. Starting the db + litellm (+ ollama, + the GPU override if enabled)
     containers and sending one real, minimal test request through the
     proxy to confirm the new configuration actually works end-to-end.
     For the Local/Ollama provider with --dev passed (or setup_all.py's
     own upfront developer-mode question - see its docstring), the ollama
     image is rebuilt fresh (see rebuild_images.py) before this test
     starts it, rather than testing a stale image a later dev-mode
     rebuild would just replace anyway.

This script only touches LLM/provider configuration. Run setup_project.py
separately (before or after this) for the Docker/GitHub CLI checks.

Usage:
  python3 setup_llm.py        Interactive, guided provider/model setup.
  python3 setup_llm.py --dev  Same, but (Local/Ollama only) rebuilds the
                              ollama image fresh before the live test.

Stdlib-only - no pip install required, and works identically on
macOS/Linux/Windows (that's the whole point of this being Python rather
than a shell script).
"""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import lib_docker
import lib_env
import lib_llm_test
import rebuild_images

ROLES = [
    "scrum-orchestrator", "scrum-po", "scrum-sm", "scrum-dev",
    "scrum-qa", "scrum-arch", "scrum-quality",
]

OLLAMA_CURATED_MODELS = [
    "llama3.2:3b", "llama3.1:8b", "qwen2.5:7b",
    "qwen2.5:14b", "mistral-nemo:12b", "llama3.1:70b",
]

_CHEAP_HINTS = ("mini", "flash", "lite", "haiku", "nano", "small")

_OPENAI_EXCLUDED = (
    "embedding", "whisper", "tts", "dall-e", "moderation",
    "davinci", "babbage", "ada", "image", "audio", "realtime",
)

_TEST_MOCK_AND_GENERAL_SETTINGS = """\
  - model_name: scrum-test-mock
    litellm_params:
      model: openai/gpt-3.5-turbo
      api_key: sk-123
      mock_response: "This is a permanent mock response for integration testing."

general_settings:
  num_retries: 3
  master_key: os.environ/LITELLM_MASTER_KEY
  store_model_in_db: true
  store_prompts_in_spend_logs: true
"""


def info(msg: str) -> None:
    print(f">> {msg}")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# --- Generic validated-input prompt (used for Git identity + budgets) ---

def prompt_text(label: str, default_value: str, pattern: str = None,
                 error_msg: str = "Invalid value.") -> str:
    compiled = re.compile(pattern) if pattern else None
    while True:
        value = input(f"{label} [{default_value}]: ").strip() or default_value
        if compiled is None or compiled.match(value):
            return value
        print(error_msg, file=sys.stderr)


def prompt_number(label: str, default_value: str, pattern: str) -> str:
    return prompt_text(label, default_value, pattern, "Please enter a number.")


def _git_ssh_host(repo_url: str) -> str | None:
    """Extracts the host from an SSH-style git URL (git@host:owner/repo.git
    or ssh://[user@]host/owner/repo.git); None for anything else (e.g. an
    HTTPS URL), which needs no SSH check."""
    m = re.match(r"^git@([^:/]+)[:/]", repo_url) or re.match(r"^ssh://(?:[^@/]+@)?([^/:]+)", repo_url)
    return m.group(1) if m else None


def _check_git_ssh_auth(host: str) -> bool:
    """Best-effort pre-flight check that SSH auth to `host` actually works,
    before attempting a clone - a missing/misconfigured key or agent (a
    common stumbling block on Windows, where ssh-agent isn't running by
    default) otherwise only surfaces as a much more confusing failure deep
    inside `git clone` itself.

    GitHub (and similar hosts) deliberately exit non-zero from `ssh -T`
    even on a *successful* auth ("Hi <user>! You've successfully
    authenticated, but GitHub does not provide shell access."), so success
    is detected from the output text, not the exit code. Any failure to
    even run the check (no `ssh` binary, unexpected host behavior, etc.) is
    treated as "could not verify" rather than "failed" - this is a
    best-effort hint, not a hard gate, and must never itself crash the
    setup script."""
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"git@{host}"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return True
    output = (result.stdout + result.stderr).lower()
    if "successfully authenticated" in output:
        return True
    if "permission denied" in output or "could not resolve hostname" in output:
        return False
    return True


def _clone_state_repo(repo_url: str, branch: str, state_repo_path: Path) -> bool:
    """Clones repo_url into state_repo_path. For an SSH-style URL, checks
    SSH auth first (see _check_git_ssh_auth) so a broken key/agent gets a
    clear, actionable message instead of a raw git error - and, either way,
    never raises: any failure is reported with manual-clone instructions
    rather than crashing the rest of setup_llm.py.

    Returns whether the clone actually succeeded - the caller must not
    create anything else inside state_repo_path on failure (see GH issue
    #108: creating the specs/ skeleton regardless of this result turned a
    single failed clone into a permanent lockout, since the manual `git
    clone` instructions just printed then failed too, on a directory that's
    no longer empty)."""
    ssh_host = _git_ssh_host(repo_url)
    if ssh_host and not _check_git_ssh_auth(ssh_host):
        warn(f"SSH authentication to '{ssh_host}' doesn't seem to be set up - skipping the clone.")
        print(f"Once `ssh -T git@{ssh_host}` reports \"successfully authenticated\", clone manually:")
        print(f"  git clone {repo_url} {state_repo_path}")
        return False

    info(f"Cloning {repo_url} into {state_repo_path}...")
    try:
        subprocess.run(["git", "clone", "--branch", branch, repo_url, str(state_repo_path)], check=True)
        return True
    except subprocess.CalledProcessError:
        warn("git clone failed - check the URL and that your git/gh credentials are set up.")
        print(f"Clone it manually once that's sorted: git clone {repo_url} {state_repo_path}")
        return False


def _setup_state_repo(env_path: Path) -> None:
    """Prompts for STATE_REPO_PATH (the team's "source of truth" repo - see
    README.md "State Repository") and gets it into a working state with no
    further manual steps: creates the directory if missing, and either
    clones GITHUB_REPO_URL into it (if it's empty and a URL is given) or
    initializes a fresh local git repo there. Leaves an already-initialized
    directory alone."""
    print()
    print("--- State repository ---")
    print("A dedicated git repo where the team's Scrum artifacts (specs, roadmap,")
    print("reports) get written - see README.md \"State Repository\".")

    current_path = lib_env.read_env_var(env_path, "STATE_REPO_PATH")
    path_str = prompt_text("State repository path", current_path or "../Horseless-Carriage-State")
    state_repo_path = Path(path_str).expanduser().resolve()

    current_url = lib_env.read_env_var(env_path, "GITHUB_REPO_URL")
    current_branch = lib_env.read_env_var(env_path, "GITHUB_REPO_BRANCH")
    repo_url = prompt_text(
        "GitHub repo URL to clone into it (leave blank to just set up a local-only repo for now)",
        current_url or "",
    )
    branch = prompt_text("Default branch", current_branch or "main")

    if shutil.which("git") is None:
        warn("'git' command not found - skipping state repository setup.")
        print(f"Once git is installed, create {state_repo_path} yourself (see README.md \"State Repository\").")
        lib_env.update_env_var(env_path, "STATE_REPO_PATH", str(state_repo_path))
        if repo_url:
            lib_env.update_env_var(env_path, "GITHUB_REPO_URL", repo_url)
        lib_env.update_env_var(env_path, "GITHUB_REPO_BRANCH", branch)
        return

    state_repo_path.mkdir(parents=True, exist_ok=True)
    is_git_repo = (state_repo_path / ".git").is_dir()
    is_empty = not any(state_repo_path.iterdir())

    # Whether state_repo_path ended up in a usable state - gates creating
    # the specs/ skeleton below (see GH issue #108). A failed clone must
    # NOT get the skeleton: state_repo_path would then have real content in
    # it (the specs/ directory itself) without ever being a successful
    # clone, so a retry falls into the "already has files, isn't a git repo"
    # branch below forever - a permanent lockout from one transient failure
    # (e.g. a missing SSH key), since the manual `git clone` instructions
    # already printed would then also fail on a no-longer-empty directory.
    repo_usable = False

    if is_git_repo:
        info(f"{state_repo_path} is already a git repository - leaving it as-is.")
        repo_usable = True
        if repo_url:
            existing_remote = subprocess.run(
                ["git", "-C", str(state_repo_path), "remote", "get-url", "origin"],
                capture_output=True, text=True,
            ).stdout.strip()
            if existing_remote and existing_remote != repo_url:
                warn(f"Its 'origin' remote ({existing_remote}) does not match the URL you entered ({repo_url}) - left unchanged.")
    elif repo_url and is_empty:
        repo_usable = _clone_state_repo(repo_url, branch, state_repo_path)
    elif repo_url and not is_empty:
        warn(f"{state_repo_path} already has files in it and isn't a git repository - not cloning automatically.")
        print(f"To make it a clone of {repo_url}, move its contents aside and re-run this script.")
    else:
        info(f"Initializing a local git repository at {state_repo_path} (no GitHub remote yet)...")
        subprocess.run(["git", "init", "-b", branch, str(state_repo_path)], check=False)
        print("No GitHub repo URL given, so there's no 'origin' remote yet - the agent needs")
        print("one to push branches/open PRs. Add it whenever you're ready:")
        print(f"  cd {state_repo_path} && git remote add origin <your-repo-url> && git push -u origin {branch}")
        repo_usable = True

    # Required by check_state_repo.py / the agents regardless of which path
    # above was taken - safe to create even on an existing/cloned repo, but
    # NOT on a failed clone (see repo_usable's docstring note above).
    if repo_usable:
        (state_repo_path / "specs").mkdir(parents=True, exist_ok=True)

    lib_env.update_env_var(env_path, "STATE_REPO_PATH", str(state_repo_path))
    if repo_url:
        lib_env.update_env_var(env_path, "GITHUB_REPO_URL", repo_url)
    lib_env.update_env_var(env_path, "GITHUB_REPO_BRANCH", branch)

    if repo_usable:
        print(f"State repository ready at: {state_repo_path}")
    else:
        print(f"STATE_REPO_PATH set to {state_repo_path}, but it isn't ready yet - see the message above.")


_INTERACTION_LEVEL_CHOICES = {"1": "Product", "2": "Stakeholder", "3": "CEO", "4": "EVAL"}


def current_interaction_level_choice(env_path: Path) -> str:
    """Which numbered choice ("1"-"4") to default the Human Interaction
    Level prompt to - whichever matches INTERACTION_LEVEL already set in
    .env, or "1" (Product) if unset/unrecognized. Re-running setup_llm.py
    previously always defaulted back to "1" here regardless of what was
    already configured."""
    current = lib_env.read_env_var(env_path, "INTERACTION_LEVEL")
    for choice_key, level in _INTERACTION_LEVEL_CHOICES.items():
        if level == current:
            return choice_key
    return "1"


def _host_git_identity() -> tuple[str, str]:
    """Best-effort read of the host machine's own global git identity
    (`git config --global user.name`/`user.email`) - used as a smarter
    starting default than the generic "DevTeam"/"devteam@company.com"
    placeholder when nothing is configured in .env yet: most people running
    this already have their own git identity configured, and it's a more
    plausible default than a made-up one. Harmless either way - real commits
    the agent makes are attributed per-role via GIT_AUTHOR_NAME/
    GIT_COMMITTER_NAME overrides (see agents/scrum_team/tools/base.py), not
    this value; GIT_USER_NAME/GIT_USER_EMAIL is only the global git config
    fallback (see entrypoint.sh) for anything that doesn't get that
    per-role override. Never raises - a missing git binary or nothing
    configured just falls back to ("", ""), same as before this existed."""
    name, email = "", ""
    try:
        r = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            name = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            email = r.stdout.strip()
    except Exception:
        pass
    return name, email


def prompt_project_settings(env_path: Path, is_local: bool = False) -> None:
    """Git identity + state repository + human interaction level + sprint
    budget/overhead prompts. Same vars as .env.example / .env.local.example's
    "Git Configuration", "Project Configuration", "Human Interaction Level",
    and "Sprint Budget & Resource Configuration" sections - see
    docs/INTERACTION-LEVELS.md.

    is_local=True (the local/Ollama flow) skips the USD budget prompt
    entirely rather than asking a question whose answer never matters - a
    self-hosted model has no real per-token price, so TOTAL_USD_BUDGET is a
    no-op there (check_cost_budget_callback skips it outright when
    LLM_LOCAL_PROVIDER=true - see ISSUE-0033/GH issue #75, #81)."""
    print()
    print("--- Git identity ---")
    print("Used for commits the agent makes on your behalf.")
    current_git_name = lib_env.read_env_var(env_path, "GIT_USER_NAME")
    current_git_email = lib_env.read_env_var(env_path, "GIT_USER_EMAIL")
    host_git_name, host_git_email = _host_git_identity()

    git_name = prompt_text("Git user name", current_git_name or host_git_name or "DevTeam")
    lib_env.update_env_var(env_path, "GIT_USER_NAME", git_name)

    git_email = prompt_text(
        "Git user email", current_git_email or host_git_email or "devteam@company.com",
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "Please enter a valid email address.",
    )
    lib_env.update_env_var(env_path, "GIT_USER_EMAIL", git_email)

    _setup_state_repo(env_path)

    print()
    print("--- Human interaction level ---")
    print("How much of a human needs to be in the loop before the team may")
    print("implement stories / release an increment (see docs/INTERACTION-LEVELS.md):")
    default_choice = current_interaction_level_choice(env_path)
    level_descriptions = {
        "1": "Product     - most supervised",
        "2": "Stakeholder",
        "3": "CEO",
        "4": "EVAL        - fully automated, no human gate (used by the eval harness)",
    }
    for choice_key, desc in level_descriptions.items():
        marker = " (current)" if choice_key == default_choice else ""
        print(f"  {choice_key}) {desc}{marker}")
    # GH issue #117: an out-of-range choice here used to kill the entire
    # wizard (die()), well after several other questions had already been
    # answered - inconsistent with every other prompt in this flow, which
    # retries on bad input instead. Answers are prefilled from .env on a
    # rerun, so this wasn't destructive, just a jarring, disproportionate
    # penalty for a typo.
    interaction_level = None
    while interaction_level is None:
        level_choice = input(f"Choice [{default_choice}]: ").strip() or default_choice
        interaction_level = _INTERACTION_LEVEL_CHOICES.get(level_choice)
        if interaction_level is None:
            warn(f"Invalid choice: {level_choice!r}. Pick one of {sorted(_INTERACTION_LEVEL_CHOICES)}.")
    lib_env.update_env_var(env_path, "INTERACTION_LEVEL", interaction_level)

    print()
    print("--- Sprint budget & resource configuration ---")
    print("SPRINT_TOKEN_BUDGET is PER-SPRINT - it resets automatically at the start of")
    print("every new sprint (see docs/BUDGET.md).")
    current_token_budget = lib_env.read_env_var(env_path, "SPRINT_TOKEN_BUDGET")
    token_budget = prompt_number("Sprint token budget", current_token_budget or "1000000", r"^[0-9]+$")
    lib_env.update_env_var(env_path, "SPRINT_TOKEN_BUDGET", token_budget)

    # TOTAL_USD_BUDGET is the canonical name (GH issue #81); fall back to the
    # older SPRINT_USD_BUDGET when prefilling so re-running this wizard on an
    # existing .env still shows what was actually configured.
    current_usd_budget = lib_env.read_env_var(env_path, "TOTAL_USD_BUDGET") or lib_env.read_env_var(env_path, "SPRINT_USD_BUDGET")

    if is_local:
        # A self-hosted model has no real per-token price, so this budget
        # would never be enforceable in any meaningful way (ISSUE-0033) -
        # asking the question would only invite false confidence. Still
        # written (harmlessly ignored) so .env stays consistent, and any
        # config from a previous cloud setup on this same .env isn't lost.
        usd_budget = current_usd_budget or "0.50"
        print(
            "TOTAL_USD_BUDGET does not apply to a local/Ollama setup - a self-hosted model has no "
            "real per-token price, so this is skipped (see docs/BUDGET.md). SPRINT_TOKEN_BUDGET "
            "above is your real guardrail here."
        )
    else:
        usd_budget = prompt_number(
            "Total USD budget (whole engagement, does not reset per sprint)",
            current_usd_budget or "0.50", r"^[0-9]+(\.[0-9]+)?$",
        )
    lib_env.update_env_var(env_path, "TOTAL_USD_BUDGET", usd_budget)
    # SPRINT_USD_BUDGET is no longer read in preference to TOTAL_USD_BUDGET
    # (get_env_with_deprecated_fallback checks TOTAL_USD_BUDGET first), but a
    # stale line from before this rename can otherwise look like it's still
    # the active value on a re-run - clear it so there's only one source of
    # truth in .env going forward.
    lib_env.update_env_var(env_path, "SPRINT_USD_BUDGET", "")

    current_overhead = lib_env.read_env_var(env_path, "PROCESS_OVERHEAD_PERCENTAGE")
    overhead = prompt_number("Maximum process overhead percentage", current_overhead or "20", r"^[0-9]+(\.[0-9]+)?$")
    lib_env.update_env_var(env_path, "PROCESS_OVERHEAD_PERCENTAGE", overhead)

    print()
    print(
        f"Set GIT_USER_NAME={git_name}, GIT_USER_EMAIL={git_email}, "
        f"INTERACTION_LEVEL={interaction_level}, SPRINT_TOKEN_BUDGET={token_budget}, "
        f"TOTAL_USD_BUDGET={usd_budget}, PROCESS_OVERHEAD_PERCENTAGE={overhead}"
    )


# --- Fetch current model lists from each provider's own API ---

def fetch_gemini_models(key: str) -> list:
    url = ("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key="
           + urllib.parse.quote(key, safe=""))
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        warn(f"Google API returned HTTP {e.code} while listing models.")
        return []
    except Exception as e:
        warn(f"Could not reach the Google API: {e}")
        return []

    names = []
    for m in data.get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        name = m.get("name", "").split("/")[-1]
        if name:
            names.append(name)
    return sorted(set(names), reverse=True)


def fetch_anthropic_models(key: str) -> list:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        warn(f"Anthropic API returned HTTP {e.code} while listing models.")
        return []
    except Exception as e:
        warn(f"Could not reach the Anthropic API: {e}")
        return []

    models = data.get("data", [])
    models.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return [m["id"] for m in models if m.get("id")]


def fetch_openai_models(key: str) -> list:
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        warn(f"OpenAI API returned HTTP {e.code} while listing models.")
        return []
    except Exception as e:
        warn(f"Could not reach the OpenAI API: {e}")
        return []

    models = data.get("data", [])
    models.sort(key=lambda m: m.get("created", 0), reverse=True)
    result = []
    for m in models:
        mid = m.get("id", "")
        low = mid.lower()
        if mid and not any(x in low for x in _OPENAI_EXCLUDED):
            result.append(mid)
    return result


# --- Interactive numbered-menu selection over a fetched/curated list ---

def select_model(label: str, options: list, current: str = "") -> str:
    """Prints a numbered menu of `options` plus a manual-entry choice.
    `current` (the model already configured by a previous setup_llm.py
    run, if any) is marked "(current)" when it's in `options`, and used as
    the no-input default either way - even when it's not in `options`
    (e.g. a freshly-fetched/curated list that no longer includes it),
    pressing Enter keeps it rather than silently resetting to option 1."""
    default_idx = None
    for i, m in enumerate(options, 1):
        marker = " (current)" if current and m == current else ""
        print(f"  {i:2d}) {m}{marker}")
        if current and m == current:
            default_idx = i
    custom_idx = len(options) + 1
    print(f"  {custom_idx:2d}) Enter a model id manually")

    default_display = str(default_idx) if default_idx else (current or "1")
    # GH issue #117: an out-of-range numeric choice here used to kill the
    # entire wizard (die()) instead of reprompting like every other question
    # in this flow - retry instead, matching the rest of the wizard's style.
    while True:
        choice = input(f"{label} [{default_display}]: ").strip() or default_display
        if choice.isdigit():
            n = int(choice)
            if n == custom_idx:
                return input("Enter model id: ").strip()
            if 1 <= n <= len(options):
                return options[n - 1]
            warn(f"Invalid selection: {choice!r}. Pick a number from 1-{custom_idx}.")
            continue
        return choice  # a free-typed model id - covers keeping a `current` not in `options`


def detect_cheap_hint(options: list):
    for i, m in enumerate(options, 1):
        low = m.lower()
        if any(h in low for h in _CHEAP_HINTS):
            return i
    return None


# --- YAML generation: same role list / structure as config/model-templates/ ---

def emit_model_entry(role: str, provider: str, model: str) -> str:
    lines = [f"  - model_name: {role}", "    litellm_params:"]
    if provider == "gemini":
        lines += [
            f"      model: gemini/{model}",
            "      api_key: os.environ/GOOGLE_API_KEY",
            "      safety_settings:",
            "        - category: HARM_CATEGORY_HARASSMENT",
            "          threshold: BLOCK_NONE",
            "        - category: HARM_CATEGORY_HATE_SPEECH",
            "          threshold: BLOCK_NONE",
            "        - category: HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "          threshold: BLOCK_NONE",
            "        - category: HARM_CATEGORY_DANGEROUS_CONTENT",
            "          threshold: BLOCK_NONE",
        ]
    elif provider == "anthropic":
        lines += [f"      model: anthropic/{model}", "      api_key: os.environ/ANTHROPIC_API_KEY"]
    elif provider == "openai":
        lines += [f"      model: openai/{model}", "      api_key: os.environ/OPENAI_API_KEY"]
    elif provider == "local":
        lines += [f"      model: ollama/{model}", "      api_base: http://ollama:11434"]
    lines.append("")
    return "\n".join(lines)


def write_litellm_yaml(provider: str, main_model: str, cheap_model: str, out_file: Path) -> None:
    parts = [f"# Generated by setup_llm.py - provider: {provider}, main model: {main_model}", "model_list:"]
    for role in ROLES:
        parts.append(emit_model_entry(role, provider, main_model))
    parts.append(emit_model_entry("scrum-eval-cheap", provider, cheap_model))
    parts.append(_TEST_MOCK_AND_GENERAL_SETTINGS)
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text("\n".join(parts), encoding="utf-8")


def current_model_for_role(yaml_path: Path, role: str) -> str:
    """Best-effort: the bare model tag (provider/ prefix stripped) already
    configured for `role` in a litellm.yaml-style file this script
    previously wrote (see emit_model_entry) - "" if the file doesn't exist
    or doesn't mention this role yet. Used to prefill the model-selection
    prompts with whatever's already configured on a re-run, instead of
    always defaulting back to a freshly fetched list's first entry."""
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        return ""
    text = yaml_path.read_text(encoding="utf-8")
    m = re.search(
        rf"-\s*model_name:\s*{re.escape(role)}\s*\n\s*litellm_params:\s*\n\s*model:\s*[a-zA-Z0-9_.\-]+/(\S+)",
        text,
    )
    return m.group(1) if m else ""


# --- GPU detection (Local/Ollama provider only) ---

def detect_nvidia_gpu() -> bool:
    """Best-effort, cross-platform check for a usable NVIDIA GPU: runs
    `nvidia-smi` (installed alongside the NVIDIA driver on Windows/Linux)
    and treats a successful, non-empty result as "yes". Never raises - a
    missing binary, a driver issue, or any other failure just means "no
    GPU detected", not a setup error. Always "no" on macOS: Docker Desktop
    for Mac has no NVIDIA GPU passthrough support at all (see docs/SETUP.md's
    "GPU Support" section)."""
    if sys.platform == "darwin":
        return False
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def gpu_default_enable(gpu_detected: bool, current_value: str) -> bool:
    """Whether to default the "enable GPU?" prompt to yes: an explicit
    prior choice (re-running setup_llm.py) takes priority over the fresh
    detection result - prefilling the user's own current setup wins over a
    recommendation. Detection only drives the default on a first-time
    configuration (current_value not yet "true"/"false")."""
    if current_value in ("true", "false"):
        return current_value == "true"
    return gpu_detected


# --- Bring up db + litellm (+ ollama for the local provider) and send one
# real, minimal (max_tokens=5) request through the proxy to the "scrum-po"
# alias, to confirm the configuration just written actually works.

def _docker_compose_available() -> bool:
    try:
        subprocess.run(["docker", "compose", "version"], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def run_configuration_test(provider: str, model_label: str, env_path: Path, dev: bool = False) -> None:
    print()
    print("--- Testing the new configuration ---")

    if shutil.which("docker") is None or not _docker_compose_available():
        warn("Docker/Docker Compose not found - skipping the live test.")
        print("Once installed, verify with: python3 doctor.py")
        return

    compose_args = []
    extra_service = None
    if provider == "local":
        compose_args = ["-f", "docker-compose.local.yaml"]
        extra_service = "ollama"
        if lib_env.read_env_var(env_path, "OLLAMA_GPU_ENABLED") == "true":
            compose_args += ["-f", "docker-compose.gpu.yaml"]

    services = ["db", "litellm"] + ([extra_service] if extra_service else [])

    # Developer mode must be settled before any container work, not after:
    # "ollama" (the only locally-built image this test ever starts - db is
    # postgres, litellm is a pulled release image) needs to be freshly
    # rebuilt BEFORE this live test runs against it, not after - otherwise
    # this test silently validates a stale image that a later dev-mode
    # rebuild (e.g. via `python3 run.py dev`) replaces anyway, wasting a
    # real model pull and giving a misleading "it works" signal for an
    # image about to be discarded.
    if dev and provider == "local":
        info("Developer mode: rebuilding the ollama image fresh before starting it for this test...")
        rebuild_exit_code = rebuild_images.rebuild(compose_args)
        if rebuild_exit_code != 0:
            warn("Rebuilding the ollama image failed - continuing with whatever image is already present.")

    # A leftover stack from an earlier run (or from switching between
    # docker-compose.yaml and docker-compose.local.yaml, which share the
    # same default project name and several service names) can make
    # `docker compose up` fail outright with no obvious cause - offer a
    # controlled reset before that happens (GH discussion on local Ollama
    # setups).
    lib_docker.maybe_stop_existing_stack([*compose_args, "--env-file", str(env_path)])

    info(f"Starting {' + '.join(services)} (docker compose {' '.join(compose_args)} up -d)...")
    cmd = ["docker", "compose", *compose_args, "--env-file", str(env_path), "up", "-d", *services]
    try:
        # capture_output (not DEVNULL): "is the Docker daemon running?" was
        # a guess masking whatever `docker compose up` actually said - on
        # at least one real Windows run the daemon WAS running and this
        # still fired, with the real cause hidden (GH issue #36).
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        warn("Could not start the containers - skipping the live test.")
        detail = (e.stderr or e.stdout or "").strip()
        if detail:
            print(detail)
        print("If Docker Desktop/the daemon is running and this doesn't explain it, check the")
        print("error above, then once resolved verify with: python3 doctor.py")
        return

    info("Waiting for the LiteLLM proxy to come up...")
    if not lib_llm_test.llm_wait_for_proxy("http://localhost:4000", 60):
        warn("LiteLLM proxy did not become reachable at http://localhost:4000 within 60s.")
        print(f"Check logs with: docker compose {' '.join(compose_args)} logs litellm")
        return

    master_key = lib_env.read_env_var(env_path, "LITELLM_MASTER_KEY")

    max_attempts, wait_between = 1, 0
    if provider == "local":
        # First run downloads the model in the background - can take a
        # while for larger models, so retry with a generous budget.
        max_attempts, wait_between = 20, 30
        print("(First run: Ollama is downloading the model - this can take several minutes.)")

    info(f"Sending a test request to scrum-po (model: {model_label})...")
    detail = ""
    for attempt in range(1, max_attempts + 1):
        ok, detail = lib_llm_test.llm_test_alias("http://localhost:4000", master_key, "scrum-po", 30)
        if ok:
            print(f"SUCCESS: {detail}")
            print()
            print("The configuration is working.")
            return
        if attempt < max_attempts:
            print(f"Not ready yet ({detail}) - retrying in {wait_between}s (attempt {attempt}/{max_attempts})...")
            time.sleep(wait_between)

    warn(f"The test request did not succeed: {detail}")
    print(f"Run python3 doctor.py for a more detailed diagnosis, or check: docker compose {' '.join(compose_args)} logs litellm")


# --- Cloud provider flow (Gemini / Anthropic / OpenAI) ---

def run_cloud_provider(provider: str, key_var: str, fetch_fn, provider_label: str) -> None:
    """Order deliberately asks about THIS provider (API key, model choice)
    first, then the surrounding project settings (git identity, state repo,
    interaction level, budget) - a user who just picked "Anthropic Claude"
    expects the next question to be about Anthropic, not their git email.
    See docs/SETUP.md's "Setup wizard flow" section."""
    env_path = Path(".env")
    if not env_path.is_file():
        shutil.copy(".env.example", env_path)
        info("Created .env from .env.example.")

    existing = lib_env.read_env_var(env_path, key_var)
    if not lib_env.is_placeholder(existing):
        print(f"Found an existing {key_var} in .env (ending in ...{existing[-4:]}).")
        reuse = input("Use it? [Y/n]: ").strip()
        if reuse.lower().startswith("n"):
            existing = ""
    else:
        existing = ""

    if existing:
        api_key = existing
    else:
        while True:
            api_key = getpass.getpass(f"Enter your {provider_label} API key: ")
            if api_key:
                break
            print("API key cannot be empty.")

    # Read back whatever this provider's own template file already has
    # configured (from a previous run), so re-running this script prefills
    # the current setup instead of always defaulting to the freshly
    # fetched list's first (newest) entry.
    template_path = Path(f"config/model-templates/litellm.cloud-{provider}.yaml")
    current_main = current_model_for_role(template_path, "scrum-po")
    current_cheap = current_model_for_role(template_path, "scrum-eval-cheap")

    info(f"Fetching current model list from {provider_label}...")
    models = fetch_fn(api_key)

    if not models:
        warn("Could not fetch a model list (bad key, network issue, or no matching models).")
        prompt_suffix = f" [{current_main}]" if current_main else ""
        main_model = input(f"Enter a model id to use for every role manually{prompt_suffix}: ").strip() or current_main
        if not main_model:
            die("No model id given.")
        cheap_model = main_model
    else:
        # Cap the menu at a readable size; the full list is still fetched/valid.
        models = models[:25]
        print()
        print(f"Current models available from {provider_label} (most recent first):")
        main_model = select_model("Model for all scrum-team roles", models, current=current_main)

        print()
        hint = detect_cheap_hint(models)
        print("Optionally pick a cheaper/faster model for the eval harness's 'scrum-eval-cheap' alias.")
        if hint:
            print(f"(Option {hint} looks like a lighter-weight model.)")
        default_cheap = current_cheap or main_model
        print(f"Press Enter to keep {default_cheap} (currently configured, or same as the main model).")
        cheap_model = select_model("Model for scrum-eval-cheap", models, current=default_cheap)

    lib_env.update_env_var(env_path, key_var, api_key)
    lib_env.ensure_master_key(env_path)

    write_litellm_yaml(provider, main_model, cheap_model, Path("litellm.yaml"))
    write_litellm_yaml(provider, main_model, cheap_model, Path(f"config/model-templates/litellm.cloud-{provider}.yaml"))

    print()
    print("--- Provider & model ---")
    print(f"Provider: {provider_label}")
    print(f"Main model (all roles):    {main_model}")
    print(f"Eval-harness cheap model:  {cheap_model}")
    print(f"Written to: .env, litellm.yaml, config/model-templates/litellm.cloud-{provider}.yaml")

    prompt_project_settings(env_path)

    run_configuration_test(provider, main_model, env_path)

    print()
    print("Next steps:")
    print("  python3 setup_project.py   # if you haven't already (Docker/GitHub CLI checks)")
    print("  python3 run.py")


# --- Local / Ollama flow ---

def run_local_provider(dev: bool = False) -> None:
    """Same ordering rationale as run_cloud_provider: model + GPU choice
    (what a user picking "Local / Ollama" actually came here for) first,
    then the surrounding project settings."""
    env_path = Path(".env")
    if not env_path.is_file():
        shutil.copy(".env.local.example", env_path)
        info("Created .env from .env.local.example.")

    # Ollama's model library has no stable public "list models" API like the
    # cloud providers do, so this is a curated pick-list rather than a live
    # fetch. Check https://ollama.com/library for the full current catalog.
    print()
    print("Ollama has no public API for 'currently available models' the way the")
    print("cloud providers do, so pick from a curated list (or enter any tag from")
    print("https://ollama.com/library manually). All roles + the eval harness share")
    print("this one model.")
    current_model = lib_env.read_env_var(env_path, "OLLAMA_MODEL")
    model = select_model(
        "Local model (tool-calling support recommended)", OLLAMA_CURATED_MODELS, current=current_model,
    )

    lib_env.update_env_var(env_path, "OLLAMA_MODEL", model)
    lib_env.ensure_master_key(env_path)

    print()
    print("--- GPU acceleration ---")
    gpu_detected = detect_nvidia_gpu()
    if gpu_detected:
        print("An NVIDIA GPU was detected on this machine (nvidia-smi ran successfully) -")
        print("enabling it is recommended: inference will be substantially faster than CPU-only.")
    else:
        print("No NVIDIA GPU was detected (nvidia-smi isn't available, or found no device) -")
        print("Ollama will run CPU-only regardless of this setting on this machine.")
    current_gpu = lib_env.read_env_var(env_path, "OLLAMA_GPU_ENABLED")
    default_enable = gpu_default_enable(gpu_detected, current_gpu)
    default_label = "Y/n" if default_enable else "y/N"
    gpu_answer = input(f"Enable NVIDIA GPU acceleration? [{default_label}]: ").strip().lower()
    gpu_enabled = gpu_answer.startswith("y") if gpu_answer else default_enable
    if gpu_enabled and not gpu_detected:
        warn("Enabling GPU support without a detected NVIDIA GPU - Ollama will likely fail to start.")
        print("See docs/SETUP.md's \"GPU Support\" section for prerequisites (drivers, the WSL2")
        print("backend on Windows, the NVIDIA Container Toolkit on Linux).")
    lib_env.update_env_var(env_path, "OLLAMA_GPU_ENABLED", "true" if gpu_enabled else "false")

    write_litellm_yaml("local", model, model, Path("config/model-templates/litellm.local-ollama.yaml"))

    print()
    print("--- Provider & model ---")
    print("Provider: Local / Ollama")
    print(f"Model (all roles + eval harness): {model}")
    print(f"GPU acceleration: {'enabled' if gpu_enabled else 'disabled'}")
    print("Written to: .env, config/model-templates/litellm.local-ollama.yaml")

    prompt_project_settings(env_path, is_local=True)

    run_configuration_test("local", model, env_path, dev=dev)

    print()
    print("Next steps:")
    gpu_flag = " -f docker-compose.gpu.yaml" if gpu_enabled else ""
    print(f"  docker compose -f docker-compose.local.yaml{gpu_flag} up")


def main(dev: bool = False) -> None:
    """dev=True (see setup_all.py, which asks about developer mode before
    running this step at all): the Local/Ollama flow's own live test
    rebuilds the ollama image fresh before starting it, instead of testing
    a stale image a later dev-mode rebuild would just replace anyway. Cloud
    providers never touch a locally-built image, so dev has no effect on
    that path - accepted here regardless so the caller doesn't need to
    know which path will be taken before the user picks one."""
    os.chdir(Path(__file__).resolve().parent)

    print("--- Horseless Carriage: LLM Provider & Model Setup ---")
    print()
    print("Select an LLM provider:")
    print("  1) Google Gemini    (cloud, commercial API key)")
    print("  2) Anthropic Claude (cloud, commercial API key)")
    print("  3) OpenAI           (cloud, commercial API key)")
    print("  4) Local / Ollama   (fully local, no commercial API, no keys)")
    choice = input("Choice [1-4]: ").strip()

    if choice == "1":
        run_cloud_provider("gemini", "GOOGLE_API_KEY", fetch_gemini_models, "Google Gemini")
    elif choice == "2":
        run_cloud_provider("anthropic", "ANTHROPIC_API_KEY", fetch_anthropic_models, "Anthropic Claude")
    elif choice == "3":
        run_cloud_provider("openai", "OPENAI_API_KEY", fetch_openai_models, "OpenAI")
    elif choice == "4":
        run_local_provider(dev=dev)
    else:
        die(f"Invalid choice: {choice}")


if __name__ == "__main__":
    try:
        main(dev="--dev" in sys.argv[1:] or "dev" in sys.argv[1:])
    except KeyboardInterrupt:
        # GH issue #117: run.py already turns a Ctrl+C during its own
        # foreground `docker compose up` into a clean message instead of a
        # raw traceback (see ISSUE-0032) - this wizard had no equivalent,
        # despite a user being arguably more likely to hit Ctrl+C here
        # (second-guessing a prompt, a mistyped key) than during run.py's
        # mostly-unattended container startup. Only wraps the standalone
        # entry point, not main() itself - setup_all.py calls main()
        # directly and deliberately wants Ctrl+C to abort its whole guided
        # flow, not just this one step.
        print()
        print("Cancelled.")
        sys.exit(0)
