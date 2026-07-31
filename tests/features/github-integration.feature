Feature: GitHub integration (auth, protected-branch guard, access checks)
  agents/scrum_team/tools/github.py supports two authentication paths (a
  Personal Access Token or a GitHub App installation token), and git_push
  refuses to push straight to a protected branch (GitFlow's main/develop)
  no matter which auth path is active - the escape hatch (allow_protected)
  exists only for specific, deliberate mechanical callers, not for agents.
  doctor.py separately verifies real, live read/push access, not just that
  a credential is present in .env.

  Background:
    # github.py:35-52 (configure_github_repo) - GitFlow bootstrap: clones if
    # missing, then ensures both main and develop exist on origin.
    Given a GitHub repo is configured (GITHUB_REPO_URL / STATE_REPO_PATH)

  # github.py:159-172 (git_push) - protected_branches is
  # {_default_push_branch, _develop_branch_name} (both main and develop are
  # protected under GitFlow); the check runs BEFORE any git command at all.
  @automatable
  Scenario Outline: git_push refuses a direct push to a protected branch by default
    Given the configured default/develop branch is "<branch>"
    When git_push(branch="<branch>", commit_message="chore: update") is called
      with no allow_protected argument
    Then it returns status "error" mentioning "Refusing to push directly to '<branch>'"
    And no "git push" subprocess is ever invoked
    And the message points at start_feature_branch/gh_pr_create/create_release_pr instead

    Examples:
      | branch  |
      | main    |
      | develop |

  # github.py:163-164 - allow_protected=True is the deliberate escape hatch
  # used only by seed_repository's bootstrap commit and
  # _sync_and_commit_roadmap_on_exhaustion (agent.py) - never by an
  # agent-facing tool call an LLM can trigger on a whim.
  @automatable
  Scenario: allow_protected=True is the only way past the protected-branch guard
    Given the configured default branch is "main"
    When git_push(branch="main", allow_protected=True) is called
    Then the protected-branch check is bypassed and the push proceeds normally

  # github.py:142-192 (git_push) - a feature branch is never protected;
  # pushing to it always proceeds to the actual git commands.
  @automatable
  Scenario: Pushing to an ordinary feature branch is never blocked
    Given the configured default/develop branches are "main"/"develop"
    When git_push(branch="feature/US-0012-add-login") is called
    Then no protected-branch error is returned
    And "git checkout -B feature/US-0012-add-login", "git add -A",
      "git commit", and "git push -u origin feature/US-0012-add-login" all run

  # doctor.py:159-165 - which auth method is reported depends purely on
  # which credentials are present, checked in this exact order (a real
  # GITHUB_TOKEN wins even if GitHub App vars also happen to be set).
  @automatable
  Scenario Outline: doctor.py reports which GitHub auth method is configured
    Given ".env" has "<config>"
    When doctor.check(repo_root) runs
    Then the output states "GitHub Authentication: Using <method>."

    Examples:
      | config                                                       | method                |
      | GITHUB_TOKEN only                                            | Personal Access Token |
      | GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY + GITHUB_APP_INSTALLATION_ID | GitHub App     |

  # doctor.py:210-218 - with no auth configured at all, this is a WARNING
  # (not an ERROR): the agent might still work for read-only/local use.
  @automatable
  Scenario: No GitHub authentication configured at all is a WARNING, not a hard failure
    Given ".env" has neither GITHUB_TOKEN nor the GitHub App trio
    When doctor.check(repo_root) runs
    Then a WARNING item mentions "No GitHub authentication method fully configured"
    And the result has_errors is False on this account alone

  # doctor.py:167-184 and lib_github.check_repo_access - GH issue #60:
  # "without the ability to read from, write to and read/write pull
  # requests and issues, the setup is not complete" - actually verified
  # live, not just inferred from a credential's presence.
  @automatable
  Scenario: doctor.py verifies real, live read access to the configured repo
    Given GITHUB_REPO_URL and a resolvable token are both configured
    And lib_github.check_repo_access reports (False, "issues read failed (HTTP 403)")
    When doctor.check(repo_root) runs
    Then a WARNING item contains "issues read failed"

  # doctor.py:174-178 - a GitHub App installation token that can't even be
  # minted (bad app id/key/installation id, or missing PyJWT/requests) is
  # its own distinct warning, since check_repo_access can never even run.
  @automatable
  Scenario: A GitHub App token that fails to mint is reported distinctly from an access failure
    Given GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_APP_INSTALLATION_ID are set (no GITHUB_TOKEN)
    And lib_github.resolve_token returns (None, "app") (minting failed)
    When doctor.check(repo_root) runs
    Then a WARNING item mentions "Could not mint a GitHub App installation token"
    And check_repo_access is never called (there is no token to check with)

  # github.py:294-323 (mark_pr_ready_for_review / merge_story_pr) - QA
  # merges a story's feature-branch PR into develop only after
  # advance_story_stage(..., "Tested") succeeds; admin=False by default so a
  # story-level merge respects real branch protection like any normal PR.
  @manual-qa
  Scenario: A story's feature-branch PR merges into develop through real branch protection
    Given a story reached "Tested" via advance_story_stage
    When merge_story_pr() is called with admin=False (the default)
    Then `gh pr merge --merge` runs without --admin
    And GitHub's own required-checks/reviews rules are respected, not bypassed
