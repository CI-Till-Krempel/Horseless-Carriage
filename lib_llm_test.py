"""
Shared helpers for checking whether the configured LLM/LiteLLM proxy setup
actually works. Used by both setup_llm.py (test a config right after writing
it) and doctor.py (diagnose an existing one). Stdlib-only (urllib instead of
curl) so it works identically on macOS/Linux/Windows.
"""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

_PROVIDER_KEY_VARS = {
    "gemini": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def llm_active_config_path(repo_root: Path) -> Path:
    """Which litellm-config file setup_llm.py most recently wrote: the root
    litellm.yaml (cloud providers, docker-compose.yaml) or
    config/model-templates/litellm.local-ollama.yaml (Local/Ollama,
    docker-compose.local.yaml). setup_llm.py's local-provider flow only
    ever writes the latter (see write_litellm_yaml/run_local_provider) -
    never litellm.yaml - so a check that only ever looks at litellm.yaml
    always reports the last-configured *cloud* provider (or the repo's
    shipped default) for a Local/Ollama setup, even with GOOGLE_API_KEY
    never set (GH issue #36).

    Picked by comparing mtimes rather than e.g. a persisted "mode" env
    var, so switching providers via a later setup_llm.py run is picked up
    correctly even if an earlier run's leftover env vars (e.g.
    OLLAMA_MODEL) are still sitting in .env."""
    repo_root = Path(repo_root)
    cloud_path = repo_root / "litellm.yaml"
    local_path = repo_root / "config" / "model-templates" / "litellm.local-ollama.yaml"
    if local_path.is_file() and (not cloud_path.is_file() or local_path.stat().st_mtime > cloud_path.stat().st_mtime):
        return local_path
    return cloud_path


def llm_active_provider(yaml_path: Path) -> str:
    """Provider implied by a litellm.yaml-style file's first model entry:
    "gemini" | "anthropic" | "openai" | "local" | "unknown"."""
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        return "unknown"
    text = yaml_path.read_text(encoding="utf-8")
    m = re.search(r"^\s*model:\s*(gemini|anthropic|openai|ollama)/", text, re.MULTILINE)
    if not m:
        return "unknown"
    provider = m.group(1)
    return "local" if provider == "ollama" else provider


def llm_provider_key_var(provider: str) -> str:
    """.env var name holding the API key for a cloud provider, "" for "local"."""
    return _PROVIDER_KEY_VARS.get(provider, "")


def llm_wait_for_proxy(base_url: str, timeout_secs: int = 30) -> bool:
    """Polls the LiteLLM proxy's liveness endpoint (cheap - no model call)
    until it responds or the timeout elapses."""
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/health/liveliness")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def llm_test_alias(base_url: str, auth_key: str, alias: str, timeout_secs: int = 30) -> Tuple[bool, str]:
    """Sends a minimal (max_tokens=5) real chat-completion request through the
    proxy to the given model alias, to confirm the whole path - proxy config,
    provider API key / local model availability, network - actually works.
    Returns (ok, human_readable_detail)."""
    payload = json.dumps({
        "model": alias,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {auth_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            content = _extract_content(body)
            return True, f'model "{alias}" responded: "{content or "<empty response>"}"'
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode("utf-8", errors="replace")
        err_msg = _extract_error_message(body)
        if code in (401, 403):
            return False, f'HTTP {code} (auth failed) for "{alias}" - check the provider API key in .env, or LITELLM_MASTER_KEY. {err_msg}'
        if code == 404:
            return False, f'HTTP {code} (not found) for "{alias}" - the model id in litellm.yaml may be invalid/retired. {err_msg}'
        if code == 429:
            return False, f'HTTP {code} (rate limited/quota) for "{alias}". {err_msg}'
        return False, f'HTTP {code} for "{alias}". {err_msg}'
    except urllib.error.URLError:
        return False, f"could not reach {base_url} (proxy not running, or network issue)."
    except Exception as e:
        return False, f"unexpected error testing {base_url}: {e}"


# Providers whose $0 cost is correct, not a misconfiguration - a self-hosted
# model genuinely has no per-token price to look up.
_FREE_PROVIDER_PREFIXES = ("ollama/",)


def llm_check_model_pricing(base_url: str, auth_key: str, timeout_secs: int = 15) -> list:
    """
    Queries the proxy's /model/info endpoint (GH issue #298) and flags any
    configured alias, routed through a commercial provider, whose resolved
    input_cost_per_token AND output_cost_per_token both come back zero or
    missing - LiteLLM's bundled pricing table not covering that model id at
    all (e.g. a newly-released or retired/renamed model) silently produces
    exactly this shape, not an error, so a live chat completion
    (llm_test_alias) succeeding is not enough to catch it - the response
    looks completely normal, only the derived USD cost is wrong.

    Returns a list of human-readable warning strings, empty if every
    commercial-provider alias resolved to a real price (or there was
    nothing to check / the endpoint couldn't be reached - never raises,
    same fail-safe shape as llm_wait_for_proxy).
    """
    req = urllib.request.Request(
        f"{base_url}/model/info",
        headers={"Authorization": f"Bearer {auth_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []

    warnings = []
    for entry in data.get("data", []) or []:
        alias = entry.get("model_name", "<unknown>")
        model_id = (entry.get("litellm_params") or {}).get("model", "")
        if model_id.startswith(_FREE_PROVIDER_PREFIXES):
            continue
        info = entry.get("model_info") or {}
        input_cost = info.get("input_cost_per_token") or 0
        output_cost = info.get("output_cost_per_token") or 0
        if input_cost == 0 and output_cost == 0:
            warnings.append(
                f'"{alias}" ({model_id}) resolves to $0 cost per token - LiteLLM\'s bundled '
                "pricing table likely doesn't cover this model id (new release, or retired/"
                "renamed) - budget/spend figures for this role will be wrong regardless of real "
                "usage. Check the model id in litellm.yaml, or pick a different one via "
                "setup_llm.py."
            )
    return warnings


def _extract_content(body: str) -> Optional[str]:
    try:
        data = json.loads(body)
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _extract_error_message(body: str) -> str:
    try:
        data = json.loads(body)
        return data.get("error", {}).get("message", "")
    except Exception:
        return ""
