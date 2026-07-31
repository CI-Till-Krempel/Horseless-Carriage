Feature: Conversation transcript capture and per-interaction-level report detail tiers
  Every agent's turn is appended to a shared, persisted multi-agent
  transcript (state.transcript) as it happens - not reconstructed after the
  fact - so the sprint report and any later audit can point at a real
  record of who said/did what. Transcript growth is bounded so a
  long-running sprint can't blow the token budget just by holding an
  ever-growing transcript in state. create_sprint_report then renders a
  condensed view of it whose depth depends on the active interaction level
  (see human-interaction-levels.feature for the full detail-tier matrix;
  this file focuses on transcript capture/summarization mechanics
  specifically).

  Background:
    # agent.py:897-932 (history_management_after_callback) - fires as an
    # AfterModelCallback for every sub-agent (COMMON_AGENT_CALLBACKS), not
    # just the Orchestrator.
    Given history_management_after_callback runs after every agent's model turn

  # agent.py:919-925 - every agent's turn (not just the Orchestrator's) is
  # appended to the SHARED transcript, tagged with agent_name, so the full
  # sprint conversation is auditable across all 6 roles.
  @automatable
  Scenario: Every agent's turn is appended to the shared transcript, tagged by role
    Given agent_name="DevTeam" produces a text response "Implemented the login form."
    When history_management_after_callback(callback_context, llm_response) runs
    Then state["transcript"] gains an entry
      {"agent_name": "DevTeam", "role": "model", "content": "Implemented the login form."}

  # agent.py:936-947 - ONLY the Orchestrator's turns are also appended to
  # the flat, resumable `messages` history used to reconstruct CLI/web
  # session resume - a specialist sub-agent's turn is in `transcript` but
  # never in `messages`.
  @automatable
  Scenario: Only the Orchestrator's turns are additionally kept in the flat resumable history
    Given agent_name="QA" produces a text response
    When history_management_after_callback runs
    Then state["transcript"] gains a "QA" entry
    But state["messages"] is NOT appended to (QA is not the Orchestrator)

  # agent.py:938-939 - a duplicate Orchestrator response (same call fired
  # twice with identical text) is not appended twice to `messages`.
  @automatable
  Scenario: An identical repeated Orchestrator response is not double-appended to messages
    Given state["messages"] already ends with {"role": "model", "content": "Done."}
    And agent_name="ScrumOrchestrator" produces the exact same text "Done." again
    When history_management_after_callback runs
    Then state["messages"] still ends with exactly one "Done." entry, not two

  # agent.py:752-779 (_get_transcript_max_entries / _trim_transcript) -
  # bounds transcript growth; the dropped prefix becomes ONE marker entry
  # noting the omitted count, never silently discarded with no trace.
  @automatable
  Scenario: Transcript growth is bounded, with a marker noting how many entries were omitted
    Given TRANSCRIPT_MAX_ENTRIES=200 and the transcript already has 250 entries
    When one more entry is appended and _trim_transcript runs
    Then the transcript is trimmed to 200 entries plus one marker entry at the front:
      {"agent_name": "system", "content": "[51 earlier transcript entries omitted for token budget]"}

  @automatable
  Scenario: A transcript exactly at the threshold is left untouched
    Given TRANSCRIPT_MAX_ENTRIES=200 and the transcript has exactly 200 entries
    When _trim_transcript runs
    Then the transcript is returned unchanged (no marker entry added)

  # budget.py:216-230 (_summarize_transcript) - one REPRESENTATIVE (most
  # recent) entry per agent, ordered by first appearance - not a plain
  # tail-N cut, which could let one chatty agent crowd out an earlier
  # agent's only contribution entirely. Ground truth: agents/scrum_team/
  # tests/test_budget.py::test_create_sprint_report_includes_transcript_excerpt.
  @automatable
  Scenario: The sprint report's transcript excerpt keeps one entry per agent, not a raw tail cut
    Given the transcript is:
      | agent_name  | content                        |
      | ProductOwner| Prioritized the backlog.       |
      | DevTeam     | Implemented the feature.       |
      | DevTeam     | Fixed a bug found in review.   |
    When create_sprint_report(summary, accomplishments) runs at "full" detail
    Then the report includes "Prioritized the backlog." (ProductOwner's only entry)
    And the report includes "Fixed a bug found in review." (DevTeam's MOST RECENT entry)
    And the report does NOT include "Implemented the feature." (DevTeam's superseded entry)

  # budget.py:429-460 - detail-tier-dependent transcript rendering: "full"
  # gets a location pointer PLUS the per-agent excerpt; "business" gets only
  # the location pointer (no excerpt dump); "executive" omits the whole
  # section (see budget.py:391-411, 460 omitted_sections handling).
  @automatable
  Scenario Outline: The transcript section's depth depends on the active interaction level
    Given INTERACTION_LEVEL is "<level>" and a non-empty transcript exists
    When create_sprint_report(summary, accomplishments) runs
    Then the report's "## Conversation Transcript" section is "<rendering>"

    Examples:
      | level       | rendering                                                          |
      | Product     | location pointer + "Most recent contribution per agent" excerpt   |
      | EVAL        | location pointer + "Most recent contribution per agent" excerpt   |
      | Stakeholder | location pointer only, no excerpt, no "## Conversation Transcript" header |
      | CEO         | omitted entirely, listed under "## Full Process Detail"           |

  # budget.py:436-449 - the location pointer names the exact repo-relative
  # path to the full transcript (the state file), not a vague "see logs".
  @automatable
  Scenario: The transcript location pointer names the exact state-file path
    Given a 12-entry transcript exists and repo_root is known
    When create_sprint_report runs at "full" or "business" detail
    Then the report states "Full transcript (12 entries) persisted at `.hc/state.json`"

  # budget.py:456-458 - no transcript recorded yet is handled explicitly,
  # not silently omitted with no explanation.
  @automatable
  Scenario: A missing transcript is reported explicitly, not silently skipped
    Given state["transcript"] is empty
    When create_sprint_report runs at "full" or "business" detail
    Then the report states "No transcript available yet for this sprint."
