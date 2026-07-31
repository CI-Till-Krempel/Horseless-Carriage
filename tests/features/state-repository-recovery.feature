Feature: State repository health-check and corruption recovery
  A corrupted .hc/state.json (torn write, hand-edit gone wrong) must never
  silently discard a session's history. Two layers cover this: an automatic,
  in-session fallback to the latest valid git checkpoint
  (load_state_from_repo), and an interactive, host-side remediation menu
  (check_state_repo.py) plus in-chat repair tools (GH issue #85) for when
  even git history has nothing recoverable.

  Background:
    # scrum.py:215-224 (_write_state_atomically) - every save_state_to_repo
    # call writes to a .tmp file then os.replace()s it, so a process killed
    # mid-write can never leave a half-written state.json on disk (GH issue
    # #59) - the precondition that makes git-history recovery meaningful at
    # all (each committed snapshot is always a complete, valid write).
    Given every save_state_to_repo() call is atomic (write-temp + os.replace)

  # scrum.py:257-285 (_recover_state_json_from_git) - walks
  # `git log --format=%H -- .hc/state.json` newest-first, returning the
  # first commit whose OWN snapshot still parses as valid JSON.
  @automatable
  Scenario: A corrupted state.json auto-recovers from the newest valid git checkpoint
    Given .hc/state.json's current working-tree content is not valid JSON
    And git history for .hc/state.json has a valid checkpoint from 2 commits ago
    When load_state_from_repo(tool_context) is called
    Then it returns status "ok" with recovered_from_git=True
    And the working-tree state.json is repaired with the recovered content
    And the session's in-memory state reflects the recovered checkpoint

  # scrum.py:265-270 - recovery walks PAST a corrupted HEAD commit to an
  # earlier good one; it is not HEAD-only.
  @automatable
  Scenario: Recovery walks past a corrupted HEAD commit to an earlier good one
    Given the most recent commit touching .hc/state.json is itself corrupted
    And an earlier commit's snapshot is valid JSON
    When _recover_state_json_from_git(repo_root) is called
    Then it returns the earlier, valid snapshot, not None

  # scrum.py:308-327 - "not found" (a brand-new state repo with no
  # state.json at all) is explicitly NOT treated as corruption.
  @automatable
  Scenario: A missing state.json (never ran yet) is not treated as corruption
    Given .hc/state.json does not exist at all
    When init_scrum_state(tool_context) is called
    Then state_json_corrupted is False
    And no blocking interaction of kind "state_corrupted" is recorded

  # scrum.py:100-118, 192-213 (init_scrum_state) - corruption that even
  # git-history recovery can't fix is flagged on the live session
  # (state_json_corrupted=True) and surfaced via a blocking interaction,
  # rather than the session silently starting blank with no explanation.
  @automatable
  Scenario: Unrecoverable corruption surfaces a blocking interaction, not silent blank state
    Given .hc/state.json is invalid JSON
    And no commit in git history for .hc/state.json parses as valid JSON either
    When init_scrum_state(tool_context) is called
    Then state["state_json_corrupted"] is True
    And a blocking interaction of kind "state_corrupted" is recorded
    And its detail names get_corrupted_state_raw_content/save_repaired_state,
      reset_state_from_git, and clear_corrupted_state as recovery options

  # scrum.py:373-409 (save_repaired_state) - refuses to run against
  # currently-healthy state.json (this repairs a genuine problem, it does
  # not overwrite good state), and validates the repaired payload against
  # ScrumState before accepting it.
  @automatable
  Scenario: save_repaired_state refuses to run when state.json is not actually corrupted
    Given .hc/state.json currently parses as valid JSON
    When save_repaired_state({"sprint_goal": "whatever"}, tool_context) is called
    Then it returns status "error" mentioning "currently parses fine"
    And the existing state.json is left untouched

  @automatable
  Scenario: save_repaired_state rejects a payload that does not validate as ScrumState
    Given .hc/state.json is currently corrupted (invalid JSON)
    When save_repaired_state({"budgets": "not-a-dict"}, tool_context) is called
    Then it returns status "error" mentioning "does not validate as a ScrumState"
    And no file is written

  # scrum.py:412-442 (reset_state_from_git) - the explicit, on-demand
  # in-chat tool version of the automatic recovery above; also refuses on
  # healthy state.
  @automatable
  Scenario: reset_state_from_git refuses on healthy state, succeeds on corrupted state with history
    Given .hc/state.json is currently corrupted (invalid JSON)
    And git history has a valid checkpoint
    When reset_state_from_git(tool_context) is called
    Then it returns status "ok" and the working tree + live session are restored
    But when called again immediately after (now healthy), it returns status "error"

  # scrum.py:445-467 (clear_corrupted_state) - the explicit discard option;
  # also refuses on healthy state.
  @automatable
  Scenario: clear_corrupted_state deletes state.json only while it is actually corrupted
    Given .hc/state.json is currently corrupted (invalid JSON)
    When clear_corrupted_state(tool_context) is called
    Then state.json is deleted and state["state_json_corrupted"] becomes False
    But calling clear_corrupted_state again with no state.json present returns status "error"

  # check_state_repo.py:62-90 (_offer_state_repair) - the host-side,
  # pre-container interactive menu (GH issue #85): reset from git / delete /
  # leave as-is, defaulting to "leave as-is" on empty input.
  @automatable
  Scenario Outline: check_state_repo.py's interactive repair menu (GH issue #85)
    Given .hc/state.json fails validate_state.py's validation
    And check_state_repo.run(repo_root, interactive=True, prompt=...) is invoked
    When the operator answers "<choice>" at "What would you like to do?"
    Then "<result>"

    Examples:
      | choice | result                                                                 |
      | 1      | restored from the last known-good git checkpoint; exit code 0         |
      | 2      | state.json deleted, team starts fresh; exit code 0                    |
      | 3      | left as-is; exit code 1                                               |
      | (blank)| defaults to "leave as-is" (same as choice 3); exit code 1              |

  # check_state_repo.py:166-172 - the non-interactive path (doctor.py, CI)
  # must never block on input() - it just fails with a clear message.
  @automatable
  Scenario: Non-interactive validation failure never prompts, just fails
    Given .hc/state.json fails validate_state.py's validation
    And check_state_repo.run(repo_root, interactive=False) is invoked
    Then it returns exit code 1
    And "What would you like to do?" is never printed
