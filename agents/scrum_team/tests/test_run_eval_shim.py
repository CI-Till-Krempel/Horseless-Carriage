# agents/scrum_team/tests/test_run_eval_shim.py
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import click

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


if __name__ == "__main__":
    unittest.main()
