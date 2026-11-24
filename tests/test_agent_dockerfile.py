"""
Regression test for GH issue #95: with no locale configured, the
python:3.11-slim base image runs under the "C" locale, so Python decodes
the interactive CLI's stdin using 'surrogateescape' - any multi-byte UTF-8
character a user types or pastes (smart quotes, em-dashes, accents) is
silently mangled into a lone surrogate codepoint. That string survives
fine until the ADK session service tries to json-serialize it for
storage, where it crashes the whole `adk run` process with
UnicodeEncodeError: "surrogates not allowed".

Pinning LANG/LC_ALL to the glibc-builtin C.UTF-8 pseudo-locale (no
`locales` package/locale-gen needed) plus PYTHONIOENCODING/PYTHONUTF8
makes Python decode stdin as real UTF-8 from the start, so this class of
input no longer produces surrogates. This asserts the Dockerfile keeps
pinning them, so a future edit can't silently drop the fix.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _base_stage_text() -> str:
    text = (REPO_ROOT / "agent.Dockerfile").read_text(encoding="utf-8")
    # Only the base stage matters here - every other stage is FROM base (or
    # FROM base AS ...), so its ENV vars are inherited automatically.
    return text.split("# --- Test Stage ---")[0]


def test_agent_dockerfile_pins_utf8_locale():
    base_stage = _base_stage_text()
    for expected in ("LANG=C.UTF-8", "LC_ALL=C.UTF-8", "PYTHONIOENCODING=UTF-8", "PYTHONUTF8=1"):
        assert expected in base_stage, (
            f"agent.Dockerfile's base stage is missing `{expected}` - "
            "without it, stdin decoding can mangle non-ASCII CLI input into "
            "lone surrogates and crash `adk run` (GH issue #95)."
        )


def test_agent_dockerfile_disables_adk_telemetry_prompt():
    """SPEC-ISSUE-0095: google-adk's first-run "Enable telemetry? [Y/n]"
    input() prompt fires on every `adk` subcommand whenever stdin is a TTY
    (the agent service runs with `tty: true`) and ~/.adk/config.json has no
    recorded answer. Unanswered, it blocks `adk web` before it ever binds
    port 8000. The base stage must record an opt-out after ADK is installed,
    so every stage (final, test) inherits it."""
    base_stage = _base_stage_text()
    assert "RUN adk telemetry disable" in base_stage, (
        "agent.Dockerfile's base stage must run `adk telemetry disable` - "
        "without it, `adk web` hangs on an unanswered telemetry consent "
        "prompt under the agent container's TTY (SPEC-ISSUE-0095)."
    )
    assert base_stage.index("pip install --no-cache-dir -r requirements.txt") < base_stage.index(
        "RUN adk telemetry disable"
    ), "`adk telemetry disable` must run after google-adk is installed."
