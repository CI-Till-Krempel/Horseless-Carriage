Feature: Human interaction levels (Product / Stakeholder / CEO / EVAL)
  INTERACTION_LEVEL (agents/scrum_team/helpers.py) selects how much of a
  human is mechanically required in the loop: which record_human_approval
  type (if any) advance_story_stage(..., "Implemented") and create_release_pr
  each require, and how much detail create_sprint_report renders for the
  human reading it. It is read fresh from the environment wherever it
  matters - there is no session-state field for it (see the "Deviation"
  note at the end of this file).

  Note on scope: only the "Implemented" stage transition and create_release_pr
  are gated by INTERACTION_LEVEL (see _PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL /
  _PRE_RELEASE_APPROVAL_BY_LEVEL, agents/scrum_team/helpers.py:68-79). The
  "Ready" stage transition is gated on story-content completeness
  (_story_readiness_issues, requirements.py:48-71) regardless of interaction
  level - there is no interaction-level gate on "Ready" itself.

  Background:
    # agents/scrum_team/helpers.py:82-93 (get_interaction_level) - reads
    # INTERACTION_LEVEL case-insensitively, defaulting to "Product" (the
    # most-supervised level) on anything unset or unrecognized.
    Given INTERACTION_LEVEL is read fresh from the environment for every check

  # helpers.py:59 (INTERACTION_LEVELS) and :82-93 (get_interaction_level) -
  # an unset/unrecognized value never silently disables every gate.
  @automatable
  Scenario Outline: An unset or garbled INTERACTION_LEVEL falls back to the most-supervised level
    Given INTERACTION_LEVEL is set to "<raw_value>"
    When get_interaction_level() is called
    Then it returns "Product"

    Examples:
      | raw_value     |
      |               |
      | Typo-Level    |
      | product-owner |

  # helpers.py:68-73 (_PRE_IMPLEMENTATION_APPROVAL_BY_LEVEL) and
  # requirements.py:837-858 (advance_story_stage's "Implemented" gate) -
  # each level requires a different approval_type (or none) before a story
  # may reach Implemented.
  @automatable
  Scenario Outline: Marking a story Implemented requires the right fresh approval for the active level
    Given INTERACTION_LEVEL is "<level>"
    And a story has completed "Ready" and has a logged actual token count
    And no NEW record_human_approval call has been made this sprint
    When advance_story_stage(story_id, "Implemented") is called by "DevTeam"
    Then it is rejected with a message naming "<required_approval>" as the missing approval type
    But once record_human_approval("<required_approval>", ...) is called first, it succeeds

    Examples:
      | level       | required_approval |
      | Product     | sprint             |
      | Stakeholder | sprint             |
      | CEO         | budget             |

  # helpers.py:72 - EVAL requires no pre-implementation approval at all.
  @automatable
  Scenario: EVAL level requires no human approval before Implemented
    Given INTERACTION_LEVEL is "EVAL"
    And a story has completed "Ready" and has a logged actual token count
    And no record_human_approval call has ever been made
    When advance_story_stage(story_id, "Implemented") is called by "DevTeam"
    Then it succeeds (no approval-type check is applied)

  # helpers.py:74-79 (_PRE_RELEASE_APPROVAL_BY_LEVEL) and github.py:401-434
  # (create_release_pr) - Product/Stakeholder need "release"; CEO/EVAL need
  # none (the team releases on its own judgment).
  @automatable
  Scenario Outline: create_release_pr's approval requirement also depends on the active level
    Given INTERACTION_LEVEL is "<level>"
    And no NEW record_human_approval call has been made since the last release
    When create_release_pr(title, body) is called
    Then it is <outcome>

    Examples:
      | level       | outcome                                                          |
      | Product     | rejected, naming "release" as the missing approval type          |
      | Stakeholder | rejected, naming "release" as the missing approval type          |
      | CEO         | not gated at all - it proceeds without any approval check        |
      | EVAL        | not gated at all - it proceeds without any approval check        |

  # github.py:419-434 - a rejected create_release_pr call also records a
  # blocking interaction (see notifications.feature) so the pending approval
  # is visible to a human even in an unattended run.
  @automatable
  Scenario: A rejected release-approval gate also records a blocking interaction
    Given INTERACTION_LEVEL is "Product" and no fresh "release" approval exists
    When create_release_pr(title, body) is called
    Then a blocking_interactions entry of kind "approval" is recorded
    And its detail names the exact record_human_approval call needed to unblock it

  # helpers.py:123-133 (_REPORT_DETAIL_LEVEL_BY_LEVEL / report_detail_level)
  # and budget.py:371-472 (create_sprint_report's rendering) - the same
  # report-generation code branches on the level; nothing here is
  # prompt-guided.
  @automatable
  Scenario Outline: create_sprint_report renders a different level of detail per interaction level
    Given INTERACTION_LEVEL is "<level>"
    And retro actions, impediments, story estimates, and a transcript all exist for this sprint
    When create_sprint_report(summary, accomplishments) is called
    Then the report's rendered sections are exactly "<sections_rendered>"
    And omitted sections (if any) are listed under "## Full Process Detail" with a pointer to
      ".hc/state.json", never silently dropped

    Examples:
      | level       | sections_rendered                                                                    |
      | Product     | per-agent usage, retro actions, impediments, story estimates, transcript excerpts    |
      | EVAL        | per-agent usage, retro actions, impediments, story estimates, transcript excerpts    |
      | Stakeholder | retro actions, impediments, story estimates, transcript location pointer only        |
      | CEO         | budget/usage and sprint-length feedback only                                         |

  # Deviation from a literal reading of the task brief: there is NO session-
  # state field (e.g. no "INTERACTION_LEVEL" key in ScrumState) mirroring
  # this - get_interaction_level() (helpers.py:82-93) always reads
  # os.environ directly, by design ("read fresh... so it can't drift from
  # what's actually configured for the running process" - helpers.py:55-58).
  # An ADK EvalCase's session_input.state therefore cannot influence which
  # gate applies; only the process environment can. See eval/adk/README.md.
