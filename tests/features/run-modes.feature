Feature: run.py modes (web / cli / daemon / dev), doctor-gate blocking, clean Ctrl-C stop
  run.py is the single entrypoint that brings up the Docker Compose stack in
  one of several modes, refusing to even try if doctor.py's pre-flight gate
  reports an ERROR, and treats a foreground Ctrl-C as a normal, clean stop
  rather than an uncaught crash.

  Background:
    # run.py:41-57 (parse_args) - mode defaults to "web"; "cli"/"daemon"/"dev"
    # are recognized keywords, anything else is passed through as extra args.
    Given the repo root is the current working directory

  # run.py:41-57 (parse_args) - table mirrors
  # tests/test_run.py::TestParseArgs exactly.
  @automatable
  Scenario Outline: parse_args resolves mode/daemon/dev/extra-args from argv
    When parse_args(<argv>) is called
    Then it returns (mode="<mode>", daemon=<daemon>, dev=<dev>, extra=<extra>)

    Examples:
      | argv                              | mode   | daemon | dev   | extra              |
      | []                                | web    | False  | False | []                 |
      | ["cli", "hello", "world"]         | cli    | False  | False | ["hello","world"]  |
      | ["daemon"]                        | web    | True   | False | []                 |
      | ["cli", "daemon", "query"]        | cli    | True   | False | ["query"]          |
      | ["cli", "web"]                    | web    | False  | False | []                 |
      | ["dev"]                           | web    | False  | True  | []                 |
      | ["cli", "daemon", "dev", "query"] | cli    | True   | True  | ["query"]          |

  # run.py:136-146 - doctor.check(Path("."), skip_llm_probe=True) is called
  # before any container work; result.has_errors blocks the run outright.
  @automatable
  Scenario: doctor.py's ERROR items block run.py from starting anything
    Given doctor.check(".", skip_llm_probe=True) would report an ERROR
      (e.g. STATE_REPO_PATH missing)
    When "python3 run.py" is invoked
    Then it prints "Cannot start: fix the ERROR items above, then try again."
    And it exits with code 1 before any "docker compose ... up" is run

  # run.py:166-176 (mode == "cli" branch) - interactive CLI runs
  # `docker compose ... run --rm --build agent /bin/bash .../run_agent.sh`,
  # distinct from the "up" used by web/daemon modes below.
  @manual-qa
  Scenario: cli mode starts an interactive terminal session, not the web UI
    When "python3 run.py cli \"say hello\"" is invoked (doctor gate passes)
    Then only the LiteLLM dashboard auto-opens (open_dashboards(mode="cli"))
    And the ADK web UI is never polled or opened
    And the agent runs via "docker compose run --rm --build agent ... run_agent.sh"

  # run.py:189-196 (daemon branch under the else/non-cli path) - `up -d
  # --build agent`, then prints the `docker compose logs -f agent` hint and
  # returns control of the terminal.
  @manual-qa
  Scenario: daemon mode starts detached and prints the log-tailing hint
    When "python3 run.py daemon" is invoked (doctor gate passes)
    Then the agent container starts via "docker compose ... up -d --build agent"
    And "Agent container started in daemon mode." is printed
    And a "docker compose ... logs -f agent" hint is printed
    And control of the terminal returns immediately

  # run.py:154-158 and rebuild_images.rebuild(compose_args) - dev mode
  # rebuilds images fresh (and sets LOG_LEVEL=debug for this invocation)
  # before any "up"/"run" happens.
  @manual-qa
  Scenario: dev mode rebuilds images before starting and forces debug logging
    When "python3 run.py dev" is invoked (doctor gate passes)
    Then rebuild_images.rebuild(compose_args) runs before "docker compose ... up"
    And LOG_LEVEL is overridden to "debug" for this invocation only
    And "Developer mode: LOG_LEVEL overridden to 'debug' for this run" is printed

  # run.py:107-125 (main()/parse_args wrapper) - GH issue #74: a raw,
  # uncaught KeyboardInterrupt from inside subprocess.run() (the foreground
  # `docker compose up`) is now caught at the top level and turned into a
  # clean "Stopped." message with exit code 0, not a stack trace.
  @automatable
  Scenario: Ctrl-C in the foreground web/daemon run is a clean stop, not a crash
    Given "python3 run.py" is running the foreground "docker compose ... up --build agent"
    When the process receives KeyboardInterrupt (Ctrl-C)
    Then "Stopped." is printed
    And the process exits with code 0
    And no Python traceback is printed
