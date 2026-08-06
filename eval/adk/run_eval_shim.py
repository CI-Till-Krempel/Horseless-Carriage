#!/usr/bin/env python3
"""
Thin wrapper around `adk`'s own CLI entry point (google.adk.cli:main) that
patches two runtime defaults `adk eval` hardcodes with no CLI flag or
config-file field to override them - both only matter for debugging this
eval set against a live model, never for production (agent.py's real
root_agent, run via `adk web`/`adk run`/run.py, is never routed through
this shim).

1. Sequential eval cases. google.adk.evaluation.base_eval_service.
   InferenceConfig/EvaluateConfig both default parallelism=4, and
   cli_tools_click.py always constructs them with no arguments - so 4
   scripted conversations ran concurrently, interleaving their tool-call
   logs so badly it was impossible to tell which log line belonged to
   which scenario, even with the "=== New session - prompt: ... ==="
   banner (agent.py's sprint_status_injection_callback prints that banner
   for all 4 up front, before any of their tool calls, since they
   genuinely start at the same time).

2. A much lower max_llm_calls. google.adk.agents.run_config.RunConfig
   defaults max_llm_calls=500, and every call site that matters here
   (Runner.run_async and friends) falls back to a bare `RunConfig()` when
   the caller (evaluation_generator.py, for this eval path) doesn't supply
   one - so a model stuck in an unproductive loop (see agent.py's
   TRANSFER_LOOP_THRESHOLD, which caps *consecutive* same-pair transfers
   but not an overall session) could burn 100+ turns before the sprint
   token budget finally cut it off. These are ~10 short scripted
   single-turn conversations expecting a handful of tool calls each -
   ADK_EVAL_MAX_LLM_CALLS (env var, default 20) is generous headroom above
   that, while cutting a genuine runaway off far earlier and far cheaper
   than waiting for the token budget. Exceeding it raises
   LlmCallsLimitExceededError, which local_eval_service.py already catches
   and logs as an inference failure for that one eval case (the same
   graceful per-case failure path as any other inference-time exception),
   not a crash of the whole `adk eval` run.

Invoked by run_adk_eval.py's adk_eval_command() in place of the bare `adk`
executable - same arguments (eval, AGENT_MODULE_PATH, EVAL_SET_PATH,
--config_file_path, --print_detailed_results), so this is a drop-in
replacement, not a different command shape.
"""
import os
import sys

from google.adk.agents.run_config import RunConfig
from google.adk.evaluation.base_eval_service import EvaluateConfig, InferenceConfig

DEFAULT_MAX_LLM_CALLS = 20


def _patch_default(cls, **defaults):
    original_init = cls.__init__

    def patched_init(self, **kwargs):
        for key, value in defaults.items():
            kwargs.setdefault(key, value)
        original_init(self, **kwargs)

    cls.__init__ = patched_init


_patch_default(InferenceConfig, parallelism=1)
_patch_default(EvaluateConfig, parallelism=1)
_patch_default(RunConfig, max_llm_calls=int(os.environ.get("ADK_EVAL_MAX_LLM_CALLS", DEFAULT_MAX_LLM_CALLS)))

from google.adk.cli import main  # noqa: E402  (must import after patching above)

if __name__ == "__main__":
    sys.exit(main())
