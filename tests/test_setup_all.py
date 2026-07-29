import pytest

import setup_all


class TestConfirm:
    def test_empty_input_uses_default_yes(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "")
        assert setup_all.confirm("Proceed?", default_yes=True) is True

    def test_empty_input_uses_default_no(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "")
        assert setup_all.confirm("Proceed?", default_yes=False) is False

    def test_yes_variants(self, monkeypatch):
        for answer in ("y", "Y", "yes", "YES"):
            monkeypatch.setattr("builtins.input", lambda _p, a=answer: a)
            assert setup_all.confirm("Proceed?", default_yes=False) is True

    def test_no_variants(self, monkeypatch):
        for answer in ("n", "N", "no", "nope"):
            monkeypatch.setattr("builtins.input", lambda _p, a=answer: a)
            assert setup_all.confirm("Proceed?", default_yes=True) is False


class TestRunStep:
    def test_success_returns_true(self):
        assert setup_all.run_step("A step", lambda: None) is True

    def test_clean_sys_exit_zero_returns_true(self):
        def step():
            raise SystemExit(0)
        assert setup_all.run_step("A step", step) is True

    def test_sys_exit_none_code_returns_true(self):
        """sys.exit() with no args (bare) is exit code None, which the
        process treats as success (0) - must not be misread as a failure."""
        def step():
            raise SystemExit()
        assert setup_all.run_step("A step", step) is True

    def test_sys_exit_nonzero_returns_false(self, capsys):
        def step():
            raise SystemExit(1)
        assert setup_all.run_step("A step", step) is False
        assert "exited with an error" in capsys.readouterr().out

    def test_keyboard_interrupt_propagates(self):
        def step():
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            setup_all.run_step("A step", step)

    def test_unexpected_exception_is_caught_and_returns_false(self, capsys):
        def step():
            raise ValueError("boom")
        assert setup_all.run_step("A step", step) is False
        out = capsys.readouterr().out
        assert "failed unexpectedly" in out
        assert "boom" in out


class TestRunGuidedStep:
    def test_success_on_first_try_does_not_prompt(self, monkeypatch):
        def fail_if_called(_p):
            raise AssertionError("should not prompt when the step already succeeded")
        monkeypatch.setattr("builtins.input", fail_if_called)
        assert setup_all.run_guided_step("A step", lambda: None) is True

    def test_retries_until_success(self, monkeypatch):
        attempts = {"n": 0}

        def step():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise SystemExit(1)

        monkeypatch.setattr("builtins.input", lambda _p: "y")  # always retry
        assert setup_all.run_guided_step("A step", step) is True
        assert attempts["n"] == 3

    def test_declining_retry_gives_up(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        def always_fails():
            raise SystemExit(1)

        assert setup_all.run_guided_step("A step", always_fails) is False


class _FakeDoctorResult:
    def __init__(self, has_errors):
        self.has_errors = has_errors


class TestRunDoctorGate:
    def test_no_errors_returns_true_without_prompting(self, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_all.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=False))

        def fail_if_called(_p):
            raise AssertionError("should not prompt when there are no errors")
        monkeypatch.setattr("builtins.input", fail_if_called)

        assert setup_all.run_doctor_gate(tmp_path) is True

    def test_retries_until_clean(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def fake_check(*a, **k):
            calls["n"] += 1
            return _FakeDoctorResult(has_errors=calls["n"] < 3)

        monkeypatch.setattr(setup_all.doctor, "check", fake_check)
        monkeypatch.setattr("builtins.input", lambda _p: "y")

        assert setup_all.run_doctor_gate(tmp_path) is True
        assert calls["n"] == 3

    def test_declining_retry_gives_up(self, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_all.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=True))
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        assert setup_all.run_doctor_gate(tmp_path) is False


class TestOfferToStart:
    """
    Acceptance Criteria (ISSUE-0028 dev-mode ordering fix): dev is decided
    upfront by main() (step 0), not re-asked here - offer_to_start only
    asks start?/cli?/daemon?, then folds the already-decided dev value
    into argv.
    """

    def test_declining_does_not_call_run_main(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        def fail_if_called(_argv):
            raise AssertionError("run.main must not be called if the user declined to start")
        monkeypatch.setattr(setup_all.run, "main", fail_if_called)

        setup_all.offer_to_start(dev=False)

    def test_accepting_defaults_hands_off_to_run_main_with_web_mode(self, monkeypatch):
        answers = iter(["y", "", ""])  # start? / cli? / daemon?
        monkeypatch.setattr("builtins.input", lambda _p: next(answers))

        captured = {}
        monkeypatch.setattr(setup_all.run, "main", lambda argv: captured.setdefault("argv", argv))

        setup_all.offer_to_start(dev=False)

        assert captured["argv"] == ["web"]

    def test_all_options_enabled_produces_full_argv(self, monkeypatch):
        answers = iter(["y", "y", "y"])  # start? / cli? / daemon?
        monkeypatch.setattr("builtins.input", lambda _p: next(answers))

        captured = {}
        monkeypatch.setattr(setup_all.run, "main", lambda argv: captured.setdefault("argv", argv))

        setup_all.offer_to_start(dev=True)

        assert captured["argv"] == ["cli", "daemon", "dev"]

    def test_dev_true_is_folded_into_argv_even_with_defaults(self, monkeypatch):
        answers = iter(["y", "", ""])  # start? / cli? / daemon?
        monkeypatch.setattr("builtins.input", lambda _p: next(answers))

        captured = {}
        monkeypatch.setattr(setup_all.run, "main", lambda argv: captured.setdefault("argv", argv))

        setup_all.offer_to_start(dev=True)

        assert captured["argv"] == ["web", "dev"]


class TestMain:
    """
    Acceptance Criteria: main() asks about developer mode first (step 0,
    before any guided step runs - see ISSUE-0028: dev mode must be settled
    before any container work, not after setup_llm.py's own Local/Ollama
    live test has already started a container it would go on to rebuild),
    then chains setup_llm -> check_state_repo -> setup_project -> the
    doctor gate -> offering to start, in that order, stopping (with a
    nonzero exit) if any guided step or the gate ultimately fails.
    """

    def _mock_all_steps_succeed(self, monkeypatch, dev_answer=""):
        monkeypatch.setattr("builtins.input", lambda _p: dev_answer)
        monkeypatch.setattr(setup_all.setup_llm, "main", lambda **k: None)
        monkeypatch.setattr(setup_all.check_state_repo, "main", lambda: None)
        monkeypatch.setattr(setup_all.setup_project, "main", lambda: None)
        monkeypatch.setattr(setup_all.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=False))
        monkeypatch.setattr(setup_all, "offer_to_start", lambda dev: None)

    def test_happy_path_reaches_offer_to_start(self, monkeypatch):
        self._mock_all_steps_succeed(monkeypatch)
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])

        offered = []
        monkeypatch.setattr(setup_all, "offer_to_start", lambda dev: offered.append(dev))

        setup_all.main()

        assert offered == [False]

    def test_dev_flag_is_passed_through_as_default(self, monkeypatch):
        self._mock_all_steps_succeed(monkeypatch)
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py", "--dev"])

        offered = []
        monkeypatch.setattr(setup_all, "offer_to_start", lambda dev: offered.append(dev))

        setup_all.main()

        assert offered == [True]

    def test_dev_mode_answer_is_threaded_into_setup_llm(self, monkeypatch):
        """The whole point of ISSUE-0028: setup_llm.py's own Local/Ollama
        live test needs to know about developer mode too, so it can
        rebuild the ollama image before starting it for that test."""
        self._mock_all_steps_succeed(monkeypatch, dev_answer="y")
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])

        captured = {}
        monkeypatch.setattr(setup_all.setup_llm, "main", lambda **k: captured.update(k))

        setup_all.main()

        assert captured == {"dev": True}

    def test_setup_llm_failure_stops_before_setup_project(self, monkeypatch):
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])
        monkeypatch.setattr("builtins.input", lambda _p: "n")  # decline dev mode, then the retry prompt

        def failing_setup_llm(**kwargs):
            raise SystemExit(1)
        monkeypatch.setattr(setup_all.setup_llm, "main", failing_setup_llm)

        def fail_if_called():
            raise AssertionError("setup_project.main must not run if setup_llm failed and the user declined retry")
        monkeypatch.setattr(setup_all.setup_project, "main", fail_if_called)

        with pytest.raises(SystemExit) as exc_info:
            setup_all.main()
        assert exc_info.value.code == 1

    def test_doctor_gate_failure_stops_before_offer_to_start(self, monkeypatch):
        self._mock_all_steps_succeed(monkeypatch)
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])
        monkeypatch.setattr(setup_all.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=True))
        monkeypatch.setattr("builtins.input", lambda _p: "n")  # decline the gate's retry prompt

        def fail_if_called(dev):
            raise AssertionError("offer_to_start must not run if the doctor gate never cleared")
        monkeypatch.setattr(setup_all, "offer_to_start", fail_if_called)

        with pytest.raises(SystemExit) as exc_info:
            setup_all.main()
        assert exc_info.value.code == 1

    def test_state_repo_check_runs_after_setup_llm_before_setup_project(self, monkeypatch):
        """Acceptance Criteria (GH issue #60): check_state_repo.py used to
        be a step nothing else in the guided flow ever ran for you - it
        must now run right after setup_llm.py (which creates/clones the
        state repo) and before setup_project.py."""
        self._mock_all_steps_succeed(monkeypatch)
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])
        order = []
        monkeypatch.setattr(setup_all.setup_llm, "main", lambda **k: order.append("setup_llm"))
        monkeypatch.setattr(setup_all.check_state_repo, "main", lambda: order.append("check_state_repo"))
        monkeypatch.setattr(setup_all.setup_project, "main", lambda: order.append("setup_project"))

        setup_all.main()

        assert order == ["setup_llm", "check_state_repo", "setup_project"]

    def test_state_repo_check_failure_stops_before_setup_project(self, monkeypatch):
        self._mock_all_steps_succeed(monkeypatch)
        monkeypatch.setattr(setup_all.sys, "argv", ["setup_all.py"])
        monkeypatch.setattr("builtins.input", lambda _p: "n")  # decline the retry prompt

        def failing_check_state_repo():
            raise SystemExit(1)
        monkeypatch.setattr(setup_all.check_state_repo, "main", failing_check_state_repo)

        def fail_if_called():
            raise AssertionError("setup_project.main must not run if check_state_repo failed and the user declined retry")
        monkeypatch.setattr(setup_all.setup_project, "main", fail_if_called)

        with pytest.raises(SystemExit) as exc_info:
            setup_all.main()
        assert exc_info.value.code == 1
