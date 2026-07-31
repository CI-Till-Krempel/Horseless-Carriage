Feature: Ctrl-C / stop handling across run.py's modes
  GH issue #74: on at least one real Windows run, Ctrl-C during the
  foreground `docker compose up` raised a raw, uncaught KeyboardInterrupt
  from inside subprocess.run()/subprocess.communicate()'s own wait - a
  crash-looking traceback for what run.py's own "Press Ctrl+C to stop"
  messages describe as the normal way to end a foreground run. run.py's
  main() (run.py:107-125) wraps every mode's _main() call in one
  try/except KeyboardInterrupt so every mode gets the same clean stop.

  Background:
    # run.py:107-125 - main() is the single wrapper; _main() does the real
    # per-mode work and can raise KeyboardInterrupt from any of its
    # subprocess.run() calls below.
    Given main(argv) wraps _main(argv) in try/except KeyboardInterrupt

  # run.py:112-125 - the fix itself: any KeyboardInterrupt raised anywhere
  # inside _main() (web, cli, or daemon mode) is caught once, centrally.
  @automatable
  Scenario: Ctrl-C anywhere inside _main() is caught centrally, not per-mode
    Given _main(argv) raises KeyboardInterrupt (simulating a real Ctrl-C
      during any mode's subprocess.run() call)
    When main(argv) is called
    Then it prints an empty line, then "Stopped."
    And it calls sys.exit(0) - not a re-raised KeyboardInterrupt, not a traceback

  # run.py:197-200 - web/daemon(foreground) mode's own message sets the
  # expectation Ctrl-C is being handled for exactly this purpose.
  @automatable
  Scenario: Foreground web mode explicitly tells the operator Ctrl-C is the way to stop
    Given mode="web", daemon=False
    When _main() reaches the foreground run
    Then "Running ADK web frontend in foreground. Press Ctrl+C to stop." is printed
    Before "docker compose ... up --build agent" is invoked

  # run.py:169 - cli mode's own equivalent message/expectation.
  @automatable
  Scenario: Interactive cli mode explicitly tells the operator Ctrl-C is the way to exit
    Given mode="cli"
    When _main() reaches the interactive run
    Then "Running agent in interactive CLI mode. Press Ctrl+C to exit." is printed

  # run.py:189-196 - daemon mode's own `up -d` call returns almost
  # immediately (detached); a Ctrl-C here would hit thread.join() (waiting
  # on the dashboard-opening background thread), still inside the same
  # main()-level try/except.
  @manual-qa
  Scenario: Ctrl-C during daemon mode's dashboard-wait is still a clean stop
    Given mode="web", daemon=True
    And "docker compose ... up -d --build agent" has already returned
    And the dashboard-opening background thread is still polling
    When the operator presses Ctrl-C during thread.join()
    Then main() still reports "Stopped." with exit code 0 (same central handler)

  # Contrast: setup_all.py deliberately does NOT swallow Ctrl-C the same
  # way - see guided-setup-wizard.feature's "Ctrl-C during a guided setup
  # step propagates instead of being treated as a failure" scenario
  # (setup_all.py:80-81). run.py's central catch and setup_all.py's
  # explicit re-raise are two different, deliberate designs for two
  # different situations (ending a long-running foreground process
  # cleanly, vs. not misreporting an interrupted guided-setup step as an
  # ordinary script failure) - not an inconsistency.
  @automatable
  Scenario: run.py's clean-stop behavior is distinct from setup_all.py's propagate-on-Ctrl-C behavior
    Given run.py's main() catches KeyboardInterrupt and exits 0 with "Stopped."
    And setup_all.py's run_step() re-raises KeyboardInterrupt unchanged
    Then these are two intentionally different behaviors for two different scripts,
      not a bug in either one
