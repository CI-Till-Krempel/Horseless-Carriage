Feature: Team-performance eval harness (agents/scrum_team/scripts/run_eval.py)
  run_eval.py headlessly drives the Scrum team through a fixed number of
  sprints against eval/scenario/PRODUCT-VISION.md, with INTERACTION_LEVEL
  forced to "EVAL" (no human in the loop at all), a pre-flight check that
  the LiteLLM proxy is actually reachable before any spend happens, and a
  --dev-mode override for a human deliberately choosing to proceed anyway.

  Background:
    # run_eval.py:67-107 (_configure_env) - sets every env var the agent
    # stack reads BEFORE agents.scrum_team.agent is ever imported.
    Given _configure_env(args) has run, setting INTERACTION_LEVEL, budgets,
      GITHUB_REPO_URL/BRANCH, and EVAL_RUN_ID for this run

  # run_eval.py:93-99 (_configure_env) - see docs/INTERACTION-LEVELS.md:
  # EVAL requires no record_human_approval call at either mechanical gate.
  @automatable
  Scenario: run_eval.py always forces INTERACTION_LEVEL=EVAL
    When _configure_env(args) is called
    Then os.environ["INTERACTION_LEVEL"] == "EVAL"
    And neither advance_story_stage(..., "Implemented") nor create_release_pr
      will require any record_human_approval call during this run

  # run_eval.py:50-64 (_litellm_proxy_reachable) and :630-653 (main()'s
  # pre-flight block) - a human running this locally (not CI, no
  # GITHUB_ACTIONS env var) is refused outright unless the proxy is
  # reachable, UNLESS --dev-mode is explicitly passed.
  @automatable
  Scenario: Refuses to run locally without a reachable LiteLLM proxy, unless --dev-mode is passed
    Given GITHUB_ACTIONS is not set (a human's own machine, not CI)
    And the LiteLLM proxy at LITELLM_PROXY_API_BASE is not reachable
      (_litellm_proxy_reachable returns False)
    When run_eval.py's main() runs without --dev-mode
    Then it exits via parser.error(...) mentioning "Refusing to run without a reachable LiteLLM proxy"
    And no sprint is ever started

  @automatable
  Scenario: --dev-mode explicitly overrides the unreachable-proxy refusal
    Given GITHUB_ACTIONS is not set and the LiteLLM proxy is not reachable
    When run_eval.py's main() runs WITH --dev-mode
    Then it proceeds (with a loud warning that the USD budget guardrail will not be enforced)
    And only the local per-sprint token-count guardrail still applies

  # run_eval.py:630 (`if not os.environ.get("GITHUB_ACTIONS")`) - the whole
  # local-proxy-reachability gate above is a no-op under CI, since eval.yml
  # already waits for /health/readiness before invoking this script.
  @automatable
  Scenario: The proxy-reachability pre-check is a no-op under CI (GITHUB_ACTIONS set)
    Given GITHUB_ACTIONS is set (running inside GitHub Actions)
    And the LiteLLM proxy is not reachable
    When run_eval.py's main() runs without --dev-mode
    Then it does not exit early on this account
      (eval.yml is responsible for waiting for proxy readiness beforehand)

  # run_eval.py:600-607 - --token-budget defaults to EVAL_SPRINT_TOKEN_BUDGET
  # (a PER-SPRINT value, never scaled by --sprints - see the comment on
  # sprints 2-3 of run 0.1.0-run2 silently doing nothing because of the old,
  # scaled behavior).
  @automatable
  Scenario: --token-budget defaults from EVAL_SPRINT_TOKEN_BUDGET and is not scaled by --sprints
    Given EVAL_SPRINT_TOKEN_BUDGET="200000" and --sprints=5 and no --token-budget given
    When main()'s argument resolution runs
    Then args.token_budget == 200000 (not 200000 * 5)

  # run_eval.py:616-623 - --usd-budget defaults from
  # EVAL_USD_BUDGET_PER_SPRINT (or the deprecated EVAL_SPRINT_USD_BUDGET),
  # and IS scaled by --sprints - the USD ceiling is a whole-run cumulative
  # cap, unlike the per-sprint token budget above.
  @automatable
  Scenario: --usd-budget defaults from EVAL_USD_BUDGET_PER_SPRINT scaled by --sprints
    Given EVAL_USD_BUDGET_PER_SPRINT="0.50" and --sprints=5 and no --usd-budget given
    When main()'s argument resolution runs
    Then args.usd_budget == 2.50

  # run_eval.py:347-373, 399-402 (_run_one_sprint) - sprint_report and
  # token_usage are reset via state_delta at the START of each sprint's
  # first attempt, mirroring the reset_sprint_budget tool a human/ScrumMaster
  # calls in interactive usage - a report existing from a prior sprint must
  # not be misread as "this sprint is already done".
  @automatable
  Scenario: Each sprint's completion signal is reset before that sprint starts
    Given a previous sprint already produced a non-empty session.state["sprint_report"]
    When _run_one_sprint(...) starts the next sprint (attempt == 0)
    Then the very first message carries state_delta resetting
      sprint_report to "" and token_usage to {"total": 0, "agents": {}}

  # run_eval.py:259-293 (_merge_open_prs) - the harness's explicit stand-in
  # for human release review: narrowed to base_branch=args.branch (this
  # run's own "main"), so it never touches story-level feature->develop PRs
  # (those are merged by QA's own merge_story_pr call during the sprint).
  @manual-qa
  Scenario: The eval harness auto-merges only the sprint-level develop->main PR
    Given a sprint just finished and opened a develop->main PR via create_release_pr
    When _merge_open_prs(local_path, base_branch=args.branch, sprint_result) runs
    Then that PR is merged with `gh pr merge --merge --admin`
    And any still-open feature-branch->develop PRs are left untouched
