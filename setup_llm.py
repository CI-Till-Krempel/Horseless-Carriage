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
     harness's "scrum-eval-cheap" alias.
  4. Writing the result into .env and into the active litellm.yaml (or, for
     the local/Ollama provider, into config/model-templates/litellm.local-ollama.yaml).
  5. Setting the Git user name/email used for commits the agent makes on
     your behalf, a human interaction level, and sprint token/USD budgets +
     maximum process overhead percentage (see docs/INTERACTION-LEVELS.md and
     .env.example's "Git Configuration" / "Sprint Budget & Resource
     Configuration").
  6. Starting the db + litellm (+ ollama) containers and sending one real,
     minimal test request through the proxy to confirm the new
     configuration actually works end-to-end.

This script only touches LLM/provider configuration. Run setup_project.py
separately (before or after this) for the Docker/GitHub CLI checks.

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

import lib_env
import lib_llm_test

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


def prompt_project_settings(env_path: Path) -> None:
    """Git identity + human interaction level + sprint budget/overhead
    prompts. Same vars as .env.example / .env.local.example's "Git
    Configuration", "Human Interaction Level", and "Sprint Budget & Resource
    Configuration" sections - see docs/INTERACTION-LEVELS.md."""
    print()
    print("--- Git identity ---")
    print("Used for commits the agent makes on your behalf.")
    current_git_name = lib_env.read_env_var(env_path, "GIT_USER_NAME")
    current_git_email = lib_env.read_env_var(env_path, "GIT_USER_EMAIL")

    git_name = prompt_text("Git user name", current_git_name or "DevTeam")
    lib_env.update_env_var(env_path, "GIT_USER_NAME", git_name)

    git_email = prompt_text(
        "Git user email", current_git_email or "devteam@company.com",
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "Please enter a valid email address.",
    )
    lib_env.update_env_var(env_path, "GIT_USER_EMAIL", git_email)

    print()
    print("--- Human interaction level ---")
    print("How much of a human needs to be in the loop before the team may")
    print("implement stories / release an increment (see docs/INTERACTION-LEVELS.md):")
    print("  1) Product     - most supervised (default)")
    print("  2) Stakeholder")
    print("  3) CEO")
    print("  4) EVAL        - fully automated, no human gate (used by the eval harness)")
    level_choice = input("Choice [1]: ").strip() or "1"
    interaction_level = {"1": "Product", "2": "Stakeholder", "3": "CEO", "4": "EVAL"}.get(level_choice)
    if interaction_level is None:
        die(f"Invalid choice: {level_choice}")
    lib_env.update_env_var(env_path, "INTERACTION_LEVEL", interaction_level)

    print()
    print("--- Sprint budget & resource configuration ---")
    current_token_budget = lib_env.read_env_var(env_path, "SPRINT_TOKEN_BUDGET")
    current_usd_budget = lib_env.read_env_var(env_path, "SPRINT_USD_BUDGET")
    current_overhead = lib_env.read_env_var(env_path, "PROCESS_OVERHEAD_PERCENTAGE")

    token_budget = prompt_number("Sprint token budget", current_token_budget or "1000000", r"^[0-9]+$")
    lib_env.update_env_var(env_path, "SPRINT_TOKEN_BUDGET", token_budget)

    usd_budget = prompt_number("Sprint USD budget", current_usd_budget or "0.50", r"^[0-9]+(\.[0-9]+)?$")
    lib_env.update_env_var(env_path, "SPRINT_USD_BUDGET", usd_budget)

    overhead = prompt_number("Maximum process overhead percentage", current_overhead or "20", r"^[0-9]+(\.[0-9]+)?$")
    lib_env.update_env_var(env_path, "PROCESS_OVERHEAD_PERCENTAGE", overhead)

    print()
    print(
        f"Set GIT_USER_NAME={git_name}, GIT_USER_EMAIL={git_email}, "
        f"INTERACTION_LEVEL={interaction_level}, SPRINT_TOKEN_BUDGET={token_budget}, "
        f"SPRINT_USD_BUDGET={usd_budget}, PROCESS_OVERHEAD_PERCENTAGE={overhead}"
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

def select_model(label: str, options: list) -> str:
    for i, m in enumerate(options, 1):
        print(f"  {i:2d}) {m}")
    custom_idx = len(options) + 1
    print(f"  {custom_idx:2d}) Enter a model id manually")
    choice = input(f"{label} [1]: ").strip() or "1"
    if choice == str(custom_idx):
        return input("Enter model id: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        return options[int(choice) - 1]
    die(f"Invalid selection: {choice}")


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


def run_configuration_test(provider: str, model_label: str, env_path: Path) -> None:
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

    services = ["db", "litellm"] + ([extra_service] if extra_service else [])
    info(f"Starting {' + '.join(services)} (docker compose {' '.join(compose_args)} up -d)...")
    cmd = ["docker", "compose", *compose_args, "--env-file", str(env_path), "up", "-d", *services]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        warn("Could not start the containers - is the Docker daemon running? Skipping the live test.")
        print("Once Docker is running, verify with: python3 doctor.py")
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
    env_path = Path(".env")
    if not env_path.is_file():
        shutil.copy(".env.example", env_path)
        info("Created .env from .env.example.")

    prompt_project_settings(env_path)

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

    info(f"Fetching current model list from {provider_label}...")
    models = fetch_fn(api_key)

    if not models:
        warn("Could not fetch a model list (bad key, network issue, or no matching models).")
        main_model = input("Enter a model id to use for every role manually: ").strip()
        if not main_model:
            die("No model id given.")
        cheap_model = main_model
    else:
        # Cap the menu at a readable size; the full list is still fetched/valid.
        models = models[:25]
        print()
        print(f"Current models available from {provider_label} (most recent first):")
        main_model = select_model("Model for all scrum-team roles", models)

        print()
        hint = detect_cheap_hint(models)
        print("Optionally pick a cheaper/faster model for the eval harness's 'scrum-eval-cheap' alias.")
        if hint:
            print(f"(Option {hint} looks like a lighter-weight model.)")
        print(f"Press Enter to just reuse {main_model} everywhere.")
        for i, m in enumerate(models, 1):
            print(f"  {i:2d}) {m}")
        custom_idx = len(models) + 1
        print(f"  {custom_idx:2d}) Enter a model id manually")
        cheap_choice = input(f"Choice [Enter = reuse {main_model}]: ").strip()
        if not cheap_choice:
            cheap_model = main_model
        elif cheap_choice == str(custom_idx):
            cheap_model = input("Enter model id: ").strip()
        elif cheap_choice.isdigit() and 1 <= int(cheap_choice) <= len(models):
            cheap_model = models[int(cheap_choice) - 1]
        else:
            die(f"Invalid selection: {cheap_choice}")

    lib_env.update_env_var(env_path, key_var, api_key)
    lib_env.ensure_master_key(env_path)

    write_litellm_yaml(provider, main_model, cheap_model, Path("litellm.yaml"))
    write_litellm_yaml(provider, main_model, cheap_model, Path(f"config/model-templates/litellm.cloud-{provider}.yaml"))

    print()
    print("--- Done ---")
    print(f"Provider: {provider_label}")
    print(f"Main model (all roles):    {main_model}")
    print(f"Eval-harness cheap model:  {cheap_model}")
    print(f"Written to: .env, litellm.yaml, config/model-templates/litellm.cloud-{provider}.yaml")

    run_configuration_test(provider, main_model, env_path)

    print()
    print("Next steps:")
    print("  python3 setup_project.py   # if you haven't already (Docker/GitHub CLI checks)")
    print("  python3 run.py")


# --- Local / Ollama flow ---

def run_local_provider() -> None:
    env_path = Path(".env")
    if not env_path.is_file():
        shutil.copy(".env.local.example", env_path)
        info("Created .env from .env.local.example.")

    prompt_project_settings(env_path)

    # Ollama's model library has no stable public "list models" API like the
    # cloud providers do, so this is a curated pick-list rather than a live
    # fetch. Check https://ollama.com/library for the full current catalog.
    print()
    print("Ollama has no public API for 'currently available models' the way the")
    print("cloud providers do, so pick from a curated list (or enter any tag from")
    print("https://ollama.com/library manually). All roles + the eval harness share")
    print("this one model.")
    model = select_model("Local model (tool-calling support recommended)", OLLAMA_CURATED_MODELS)

    lib_env.update_env_var(env_path, "OLLAMA_MODEL", model)
    lib_env.ensure_master_key(env_path)

    write_litellm_yaml("local", model, model, Path("config/model-templates/litellm.local-ollama.yaml"))

    print()
    print("--- Done ---")
    print("Provider: Local / Ollama")
    print(f"Model (all roles + eval harness): {model}")
    print("Written to: .env, config/model-templates/litellm.local-ollama.yaml")

    run_configuration_test("local", model, env_path)

    print()
    print("Next steps:")
    print("  docker compose -f docker-compose.local.yaml up")


def main() -> None:
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
        run_local_provider()
    else:
        die(f"Invalid choice: {choice}")


if __name__ == "__main__":
    main()
