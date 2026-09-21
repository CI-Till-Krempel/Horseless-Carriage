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

3. A real process exit code on eval failure. `cli_eval` (cli_tools_click.py)
   prints "Tests passed: X / Tests failed: Y" and, for each case, "Overall
   Eval Status: FAILED" - but never calls `ctx.exit()`/raises on a nonzero
   failure count, so its click.Group returns/exits 0 regardless (only
   actual infra errors - missing agent module, bad eval-set file, etc. -
   raise a ClickException and exit nonzero). A real CI run once went green
   with "Tests passed: 0 / Tests failed: 10" - the exact silent-100%-
   failure this eval set exists to catch. Rather than patching `cli_eval`
   itself (its whole body runs inside one click command function, nothing
   to hook), this intercepts every click.echo() call and tallies any
   "Tests failed: N" text - message text is cli_tools_click.py's only
   externally observable signal of the actual result, since the command's
   own return value is always None.

4. Duplicate tracebacks for the same failure. When an eval case's
   inference raises (e.g. LlmCallsLimitExceededError), ADK logs the full
   stack trace at three separate catch sites as it propagates up:
   _node_runner.py's "Node execution failed with exception", then
   runners.py's "Root node %s failed.", then finally
   local_eval_service.py's "Inference failed for eval case `%s` ..." -
   the only one of the three that actually names which case failed. A real
   CI run showed each LlmCallsLimitExceededError logged ~90 near-identical
   lines this way (GH issue #195) - exactly the kind of noise point 3's
   sibling log-hygiene work already trimmed elsewhere. The two earlier,
   context-free re-logs are suppressed at the source via a logging.Filter
   on their specific loggers - real, distinct failures elsewhere are
   unaffected, and the final, most useful log line (which names the eval
   case) is untouched.

Invoked by run_adk_eval.py's adk_eval_command() in place of the bare `adk`
executable - same arguments (eval, AGENT_MODULE_PATH, EVAL_SET_PATH,
--config_file_path, --print_detailed_results), so this is a drop-in
replacement, not a different command shape.
"""
import logging
import os
import re
import sys

import click
from google.adk.agents.run_config import RunConfig
from google.adk.evaluation.base_eval_service import EvaluateConfig, InferenceConfig

DEFAULT_MAX_LLM_CALLS = 20
_TESTS_FAILED_PATTERN = re.compile(r"Tests failed:\s*(\d+)")

# logger name -> the exact (pre-format) message that catch site logs with
# exc_info, for the two intermediate re-logs of the same underlying
# failure - see module docstring point 4. Matched on the raw message
# rather than the exception itself: by the time local_eval_service.py logs
# its own summary, the exception has usually been re-wrapped (e.g.
# DynamicNodeFailError chained from the original LlmCallsLimitExceededError
# via __context__), so there's no single exception identity shared across
# all three log calls to key off - but each catch site's own message text
# is fixed and specific to that log call, so matching on (logger, message)
# reliably identifies exactly these two re-logs and nothing else.
_NOISY_RELOG_LOGGERS = {
    "google_adk.google.adk.workflow._node_runner": "Node execution failed with exception",
    "google_adk.google.adk.runners": "Root node %s failed.",
}


class _SuppressRedundantExceptionRelogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        expected_msg = _NOISY_RELOG_LOGGERS.get(record.name)
        return not (expected_msg is not None and record.msg == expected_msg and record.exc_info)


for _logger_name in _NOISY_RELOG_LOGGERS:
    logging.getLogger(_logger_name).addFilter(_SuppressRedundantExceptionRelogs())


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


def run() -> int:
    """Runs `adk eval` with standalone_mode=False (so click returns instead
    of calling sys.exit itself) and tallies every "Tests failed: N" message
    cli_eval prints, returning a nonzero exit code if any eval case failed -
    see module docstring point 3 for why this can't just trust click's own
    return value/exit code."""
    failure_counts = []
    original_echo = click.echo

    def _tallying_echo(message=None, *args, **kwargs):
        if message is not None:
            match = _TESTS_FAILED_PATTERN.search(str(message))
            if match:
                failure_counts.append(int(match.group(1)))
        return original_echo(message, *args, **kwargs)

    click.echo = _tallying_echo
    try:
        exit_code = main.main(standalone_mode=False)
    except click.ClickException as e:
        e.show()
        exit_code = e.exit_code
    except click.exceptions.Abort:
        original_echo("Aborted!", file=sys.stderr)
        exit_code = 1
    finally:
        click.echo = original_echo

    exit_code = exit_code or 0
    if exit_code == 0 and sum(failure_counts) > 0:
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
