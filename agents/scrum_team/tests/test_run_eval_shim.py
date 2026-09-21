# agents/scrum_team/tests/test_run_eval_shim.py
import io
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import click
import google.adk.cli.cli_eval as cli_eval_module
from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_metrics import EvalStatus
from google.adk.evaluation.eval_result import EvalCaseResult, EvalMetricResultPerInvocation
from google.genai import types as genai_types

from eval.adk import run_eval_shim


class TestRun(unittest.TestCase):
    """
    Acceptance Criteria: `adk eval`'s own CLI (cli_tools_click.py's
    cli_eval) never fails the process based on per-case pass/fail counts -
    only real infra errors (bad agent module, missing eval-set file, etc.)
    raise a ClickException. A real CI run went green with "Tests passed: 0
    / Tests failed: 10" because of exactly this. run() must read the same
    "Tests failed: N" text `cli_eval` prints via click.echo and force a
    nonzero exit whenever any case failed, while still passing through
    real click errors and restoring click.echo afterward either way.
    """

    def _fake_main_printing(self, message):
        def fake_main(standalone_mode=False):
            click.echo(message)
            return None

        return fake_main

    def test_returns_zero_when_all_cases_pass(self):
        fake_main = self._fake_main_printing("my-eval-set:\n  Tests passed: 10\n  Tests failed: 0")
        with patch.object(run_eval_shim.main, "main", fake_main):
            self.assertEqual(run_eval_shim.run(), 0)

    def test_returns_nonzero_when_any_case_fails(self):
        fake_main = self._fake_main_printing("my-eval-set:\n  Tests passed: 0\n  Tests failed: 10")
        with patch.object(run_eval_shim.main, "main", fake_main):
            self.assertNotEqual(run_eval_shim.run(), 0)

    def test_sums_failures_across_multiple_eval_sets(self):
        def fake_main(standalone_mode=False):
            click.echo("set-a:\n  Tests passed: 3\n  Tests failed: 1")
            click.echo("set-b:\n  Tests passed: 5\n  Tests failed: 0")
            return None

        with patch.object(run_eval_shim.main, "main", fake_main):
            self.assertNotEqual(run_eval_shim.run(), 0)

    def test_propagates_a_real_click_exception_as_nonzero(self):
        def fake_main(standalone_mode=False):
            raise click.ClickException("agent module not found")

        stderr = io.StringIO()
        with patch.object(run_eval_shim.main, "main", fake_main), redirect_stderr(stderr):
            exit_code = run_eval_shim.run()
        self.assertNotEqual(exit_code, 0)
        self.assertIn("agent module not found", stderr.getvalue())

    def test_restores_click_echo_after_success(self):
        original_echo = click.echo
        fake_main = self._fake_main_printing("my-eval-set:\n  Tests passed: 1\n  Tests failed: 0")
        with patch.object(run_eval_shim.main, "main", fake_main):
            run_eval_shim.run()
        self.assertIs(click.echo, original_echo)

    def test_restores_click_echo_after_a_click_exception(self):
        original_echo = click.echo

        def fake_main(standalone_mode=False):
            raise click.ClickException("boom")

        with patch.object(run_eval_shim.main, "main", fake_main), redirect_stderr(io.StringIO()):
            run_eval_shim.run()
        self.assertIs(click.echo, original_echo)

    def test_does_not_swallow_a_nonzero_click_exit_code(self):
        """A real click.exceptions.Exit(code) (e.g. an arg-validation
        ctx.exit(2)) is returned directly by main.main(standalone_mode=False)
        - that must win even with zero "Tests failed" mentions at all."""

        def fake_main(standalone_mode=False):
            return 2

        with patch.object(run_eval_shim.main, "main", fake_main):
            self.assertEqual(run_eval_shim.run(), 2)


class TestSuppressRedundantExceptionRelogs(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #195): a real eval run logged the same
    LlmCallsLimitExceededError's full stack trace three times in a row as
    it propagated up - _node_runner.py's "Node execution failed with
    exception", runners.py's "Root node %s failed.", then
    local_eval_service.py's "Inference failed for eval case `%s` ..." -
    ~90 near-identical lines for one root cause. The two earlier, context-
    free re-logs must be suppressed; the final one (which actually names
    the failing eval case) must always get through, and unrelated log
    records must never be touched.
    """

    def _emit(self, logger_name, msg, exc_info=False, args=()):
        logger = logging.getLogger(logger_name)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            if exc_info:
                try:
                    raise ValueError("boom")
                except ValueError:
                    logger.error(msg, *args, exc_info=True)
            else:
                logger.error(msg, *args)
        finally:
            logger.removeHandler(handler)
        return stream.getvalue()

    def test_suppresses_the_node_runner_relog(self):
        output = self._emit(
            "google_adk.google.adk.workflow._node_runner",
            "Node execution failed with exception",
            exc_info=True,
        )
        self.assertEqual(output, "")

    def test_suppresses_the_runners_relog(self):
        output = self._emit(
            "google_adk.google.adk.runners",
            "Root node %s failed.",
            exc_info=True,
            args=("ScrumOrchestrator",),
        )
        self.assertEqual(output, "")

    def test_does_not_suppress_the_local_eval_service_summary(self):
        output = self._emit(
            "google_adk.google.adk.evaluation.local_eval_service",
            "Inference failed for eval case `%s` with error %s.",
            exc_info=True,
            args=("some_case_id", "boom"),
        )
        self.assertIn("Inference failed for eval case", output)

    def test_does_not_suppress_a_matching_message_from_an_unrelated_logger(self):
        """Keying on (logger, message) together, not message alone - a
        coincidentally identical message from a different module must not
        be swallowed."""
        output = self._emit(
            "some.other.module",
            "Node execution failed with exception",
            exc_info=True,
        )
        self.assertIn("Node execution failed with exception", output)

    def test_does_not_suppress_the_same_logger_with_no_exc_info(self):
        """Only the exact exception-dump call is targeted - a plain,
        non-exception log line from the same logger must pass through."""
        output = self._emit(
            "google_adk.google.adk.workflow._node_runner",
            "Node execution failed with exception",
            exc_info=False,
        )
        self.assertIn("Node execution failed with exception", output)


def _make_invocation(tool_calls):
    return Invocation(
        invocation_id="inv-1",
        user_content=genai_types.Content(role="user", parts=[genai_types.Part(text="prompt")]),
        final_response=genai_types.Content(role="model", parts=[genai_types.Part(text="response")]),
        intermediate_data=IntermediateData(tool_uses=list(tool_calls)),
    )


def _make_eval_case_result(status, actual_invocation, expected_invocation):
    per_invocation_result = EvalMetricResultPerInvocation(
        actual_invocation=actual_invocation,
        expected_invocation=expected_invocation,
        eval_metric_results=[],
    )
    return EvalCaseResult(
        eval_set_file="dummy.evalset.json",
        eval_set_id="hc-scrum-team-gate-enforcement-v1",
        eval_id="git_push_refuses_protected_develop",
        final_eval_status=status,
        eval_metric_results=[],
        overall_eval_metric_results=[],
        eval_metric_result_per_invocation=[per_invocation_result],
        session_id="dummy-session",
    )


class TestCompactToolCallDiff(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #196): adk eval's own tabulate table wraps
    each invocation's args/call-IDs across 15-20 lines, interleaving
    fragments from different tool calls into adjacent cells - genuinely
    hard to eyeball whether a failure is a real trajectory mismatch.
    pretty_print_eval_result must additionally print one compact,
    single-line-per-side diff for every failed case, built directly from
    the same FunctionCall objects the table itself uses - not parsed from
    the rendered table text - while never touching the table for a passed
    case.
    """

    def test_format_tool_call_is_compact_and_single_line(self):
        call = genai_types.FunctionCall(name="git_push", args={"branch": "develop"})
        self.assertEqual(run_eval_shim.format_tool_call(call), "git_push(branch='develop')")

    def test_no_diff_is_printed_for_a_passed_case(self):
        invocation = _make_invocation([genai_types.FunctionCall(name="git_push", args={"branch": "develop"})])
        eval_result = _make_eval_case_result(EvalStatus.PASSED, invocation, invocation)

        out = io.StringIO()
        with redirect_stdout(out):
            cli_eval_module.pretty_print_eval_result(eval_result)

        self.assertNotIn("Compact tool-call diff", out.getvalue())

    def test_diff_is_printed_alongside_the_original_table_for_a_failed_case(self):
        expected_invocation = _make_invocation([genai_types.FunctionCall(name="git_push", args={"branch": "develop"})])
        actual_invocation = _make_invocation(
            [genai_types.FunctionCall(name="transfer_to_agent", args={"agent_name": "DevTeam"})]
        )
        eval_result = _make_eval_case_result(EvalStatus.FAILED, actual_invocation, expected_invocation)

        out = io.StringIO()
        with redirect_stdout(out):
            cli_eval_module.pretty_print_eval_result(eval_result)
        output = out.getvalue()

        # The original table output is untouched.
        self.assertIn("Eval Id: git_push_refuses_protected_develop", output)
        # The new compact diff is present and actually readable on one line
        # per side, unlike the wrapped table.
        self.assertIn("Compact tool-call diff", output)
        self.assertIn("expected: [\"git_push(branch='develop')\"]", output)
        self.assertIn("actual:   [\"transfer_to_agent(agent_name='DevTeam')\"]", output)


if __name__ == "__main__":
    unittest.main()
