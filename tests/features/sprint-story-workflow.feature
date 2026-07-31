Feature: Sprint story workflow (5-stage pipeline: Ready -> Implemented -> Reviewed -> Tested -> Accepted)
  Deviation note: STORY_STAGES (agents/scrum_team/helpers.py:157) defines
  exactly 5 mandatory stages - "Draft" is only the free-form default value
  of a backlog item's "status" field before it ever enters the pipeline
  (requirements.py:598, `item.get("status", "Draft")`), not itself a stage
  advance_story_stage enforces ordering/ownership for. A prior draft of this
  task brief described this as a "6-stage pipeline"; that does not match the
  actual code (see docs/ARCHITECTURE.md "Story workflow", STORY_STAGES).

  advance_story_stage(title_or_id, stage) (agents/scrum_team/tools/
  requirements.py:751-976) is the single mechanism that may complete a
  stage: it enforces strict ordering (no skipping), stage ownership (only
  the owning role), one-story-at-a-time (backlog priority order), and
  (for "Ready"/Done-synonyms written via other tools) rejects
  placeholder/empty content - all in code, not only via a prompt asking
  nicely (see docs/ARCHITECTURE.md's "Design Principle" section).

  Background:
    # helpers.py:157-165 (STORY_STAGES / STAGE_OWNERS)
    Given the 5-stage pipeline and its owners:
      | stage       | owner        |
      | Ready       | ProductOwner |
      | Implemented | DevTeam      |
      | Reviewed    | Architect    |
      | Tested      | QA           |
      | Accepted    | ProductOwner |

  # requirements.py:800-809 - target_idx's preceding stages must already be
  # in stages_completed; skipping straight to a later stage is rejected.
  @automatable
  Scenario: Skipping a stage is rejected
    Given a story "US-0042" has completed only "Ready"
    When advance_story_stage("US-0042", "Reviewed") is called by "Architect"
    Then it returns status "error" mentioning "hasn't completed ['Implemented'] yet"
    And the story's stages_completed is unchanged

  # requirements.py:776-785 - the calling agent_name must equal
  # STAGE_OWNERS[stage]; any other role attempting the same stage is refused.
  @automatable
  Scenario Outline: Only the owning role may complete a given stage
    Given a story "US-0042" is otherwise eligible for "<stage>"
    When advance_story_stage("US-0042", "<stage>") is called by "<wrong_role>"
    Then it returns status "error" mentioning "can only be completed by <owner>"

    Examples:
      | stage       | owner        | wrong_role |
      | Ready       | ProductOwner | DevTeam    |
      | Implemented | DevTeam      | QA         |
      | Reviewed    | Architect    | DevTeam    |
      | Tested      | QA           | Architect  |
      | Accepted    | ProductOwner | QA         |

  # requirements.py:680-694, 817-826 (_preceding_story) - product_backlog
  # order is priority order; a story can't advance past Ready until the
  # immediately-preceding story (Epics skipped) has reached Accepted.
  @automatable
  Scenario: A story cannot advance until the higher-priority story ahead of it is Accepted
    Given product_backlog order is ["US-0001" (not yet Accepted), "US-0002"]
    And "US-0002" has otherwise completed every stage up to "Implemented"
    When advance_story_stage("US-0002", "Reviewed") is called by "Architect"
    Then it returns status "error" mentioning "US-0001' must reach Accepted first"

  # requirements.py:48-71 (_story_readiness_issues) via _update_story_markdown
  # (:598-617) - writing a story with status "Ready" (or a Done-synonym) with
  # placeholder/blank content is rejected at the content layer, distinct from
  # advance_story_stage's own ordering/ownership checks above.
  @automatable
  Scenario Outline: Placeholder or missing story content is rejected when marking Ready/Done
    Given a story item has "<field>" set to "<bad_value>"
    When upsert_story(item) is called (item.status == "Ready")
    Then the story-markdown write fails, and the top-level result status is "error"
    And the message cites "Fails Definition of Ready/Done" and names the specific issue

    Examples:
      | field         | bad_value                                             |
      | title         | US-0007                                               |
      | user_story    | As a , I want , so that .                             |
      | user_story    | As a <role>, I want <capability>, so that <benefit>.  |
      | acceptance_criteria | (empty list)                                     |

  # helpers.py:175-192 (blocks_direct_status_set) and requirements.py:274-284 /
  # scrum.py:599-608 (upsert_backlog_item / plan_sprint_backlog_item) - no
  # tool other than advance_story_stage may set status directly to a stage
  # name or a legacy Done-synonym.
  @automatable
  Scenario Outline: No tool other than advance_story_stage may set status to a pipeline stage directly
    When <tool>(..., status="<status>") is called
    Then it returns status "error" mentioning "must go through advance_story_stage"

    Examples:
      | tool                    | status    |
      | upsert_story             | Accepted  |
      | upsert_epic               | Ready     |
      | plan_sprint_backlog_item  | Done      |
      | plan_sprint_backlog_item  | completed |

  # requirements.py:868-880 - marking "Implemented" also requires a real
  # source-file write (write_file on a non-specs/non-template path) since the
  # last story was Implemented, unless the item is flagged {"spike": true}.
  @automatable
  Scenario: Marking Implemented without any real source file written is rejected
    Given a story has completed "Ready" and no source file has been written
      via write_file since the previous story reached Implemented
    And the story is not flagged as a spike
    When advance_story_stage(story_id, "Implemented") is called by "DevTeam"
      (with any required human approval already satisfied)
    Then it returns status "error" mentioning "no real source file has been written"

  # requirements.py:881-890 - "Implemented" also requires log_story_tokens to
  # have been called for this story (actual token spend logged, not just estimated).
  @automatable
  Scenario: Marking Implemented without logged actual tokens is rejected
    Given a story has a source-file write recorded but log_story_tokens was never called for it
    When advance_story_stage(story_id, "Implemented") is called by "DevTeam"
    Then it returns status "error" mentioning "actual tokens spent haven't been logged yet"

  # requirements.py:891-914 - Reviewed/Tested each require a fresh
  # gh_pr_review/gh_pr_comment call from the owning role (pr_review_calls),
  # not just the role's own say-so that a review happened.
  @automatable
  Scenario Outline: Reviewed/Tested require a real recorded PR review/comment from the owning role
    Given no gh_pr_review/gh_pr_comment call from "<owner>" has been recorded
      since the last story reached "<stage>"
    When advance_story_stage(story_id, "<stage>") is called by "<owner>"
    Then it returns status "error" mentioning "no gh_pr_review/gh_pr_comment call from <owner>"

    Examples:
      | stage    | owner     |
      | Reviewed | Architect |
      | Tested   | QA        |

  # requirements.py:915-928 - Tested additionally requires check_build() to
  # have been called and to have actually passed.
  @automatable
  Scenario: Tested is rejected if check_build() hasn't run, or its last result failed
    Given a QA review comment has been recorded for this story
    And the last check_build() result has passing=False (or check_build was never called)
    When advance_story_stage(story_id, "Tested") is called by "QA"
    Then it returns status "error" mentioning "check_build()" and either
      "hasn't been called yet" or "the last check_build() result failed"

  # requirements.py:751-975 - a successful transition updates BOTH the
  # story's own state and specs/ROADMAP.md's checkboxes atomically in the
  # same call; if the roadmap/story-file sync fails, the top-level status
  # must reflect that failure honestly, per docs/ARCHITECTURE.md's "A tool's
  # own success/failure must reflect the whole operation" principle.
  @automatable
  Scenario: A successful stage transition syncs specs/ROADMAP.md automatically, in the same call
    Given "US-0042" is eligible to advance to "Reviewed" and Architect has a fresh PR review recorded
    When advance_story_stage("US-0042", "Reviewed") is called by "Architect"
    Then it returns status "ok" with stages_completed including "Reviewed"
    And specs/ROADMAP.md's "US-0042" block now shows "REVIEWED" checked
    And no separate "update the roadmap" step was required
