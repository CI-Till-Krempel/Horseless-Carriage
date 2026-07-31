# Troubleshooting

A symptom → cause → fix reference for Horseless Carriage, grouped by area. For concepts and
day-to-day usage see [MANUAL.md](MANUAL.md); for the pre-flight checklist see
[PREFLIGHT.md](PREFLIGHT.md). If you don't find your symptom here, check the linked `docs/*.md`
page for the area, then [open a GitHub issue](#where-to-get-more-help) if it's still unclear.

Run `python3 doctor.py` first for almost anything that looks wrong — it collects every problem it
can find (Docker, `.env`, state repo, GitHub auth, live LLM connectivity) into one "Actionable
Items" punch list in a single pass, rather than making you fix-one/rerun/discover-the-next-one.
`run.py` itself refuses to start at all while any `ERROR`-level item remains.

## 1. Setup / first-run problems

- **`doctor.py` reports `ERROR: .env file not found.`** — copy `.env.example` to `.env` (or
  `.env.local.example` for a fully local/Ollama setup), or just run `python3 setup_project.py` /
  `python3 setup_llm.py`, which create and populate it for you. See
  [Setup](docs/SETUP.md#1-guided-llmproject-setup-setup_llmpy).
- **`ERROR: LITELLM_MASTER_KEY is not set in .env.`** — set any string value for
  `LITELLM_MASTER_KEY`; it's the proxy's own admin key, not a provider credential.
- **`ERROR: STATE_REPO_PATH is not set in .env.`** / **`ERROR: The directory specified by
  STATE_REPO_PATH does not exist: ...`** — `run.py` and `doctor.py` hard-fail without a state repo.
  Set `STATE_REPO_PATH` and create the directory (`mkdir -p <path>`), or let `setup_llm.py` do both
  interactively. See [State Repository](docs/STATE-REPOSITORY.md).
- **`WARNING: GIT_USER_NAME is not set...` / `GIT_USER_EMAIL is not set...`** — cosmetic fallback
  warnings only; real commits the agents make are still attributed per-role regardless (see
  [GitHub Integration](docs/GITHUB-INTEGRATION.md#agent-identity)). Set them in `.env` to silence
  the warning, or ignore it.
- **`WARNING: No GitHub authentication method fully configured in .env.`** — neither `GITHUB_TOKEN`
  nor the full `GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/`GITHUB_APP_INSTALLATION_ID` trio is set.
  This is a warning, not an error — the container still starts and other tools still work — but any
  GitHub-touching tool call will fail until you fix `.env` and restart. See
  [GitHub Integration](docs/GITHUB-INTEGRATION.md) to pick one of the two auth methods.
- **`WARNING: State repository has N stray TEMPLATE-*.md file(s)...`** — a `TEMPLATE-*.md` file
  ended up directly under your state repo's `specs/` directory. These belong only in this project's
  own `spec-templates/`, never copied into a target repo. Delete them; run
  `python3 check_state_repo.py` for the exact file list.
- **`gh CLI is not authenticated` warning** — only matters if you're using the Personal Account auth
  method (skip if you configured a GitHub App); run `gh auth login`.
- **Not sure which items above actually block you vs. which are just noise?** — run
  `python3 setup_all.py` instead of chasing them by hand: it re-runs `setup_llm.py` +
  `setup_project.py`, then loops on the `doctor.py` gate (fix → retry) until no `ERROR`-level item
  remains, then offers to start the agent.
- **`check_state_repo.py` reports `ERROR: state.json validation failed...`** — your `.hc/state.json`
  is corrupted or fails schema validation. Run it interactively (a real terminal, not CI) for a
  repair/reset/delete menu: reset to the last known-good checkpoint in git history, delete
  `state.json` and start fresh, or leave it as-is and let the Orchestrator attempt an LLM-assisted
  repair once you start a session. See
  [State Repository § Checkpointing and recovery](docs/STATE-REPOSITORY.md#checkpointing-and-recovery).
  The Orchestrator offers the same three choices in chat if this happens mid-session instead of
  during setup (plus the LLM-assisted option, which only it can do).
- **First message to the Orchestrator gets no reply / nothing happens until you type something** —
  expected behavior, not a bug: ADK itself has no mechanism to invoke an agent before a real user
  turn, in either the web UI or `run.py cli`. Send any message (even "Hi") — the reply is a rich,
  state-aware greeting, not a generic one. See [MANUAL.md §4](MANUAL.md#4-running-a-sprint).

## 2. Docker / container problems

- **`docker compose up` fails, or the agent comes up pointed at the wrong provider** — an existing
  Horseless Carriage stack (from switching between `docker-compose.yaml` and
  `docker-compose.local.yaml`, which share service names, or a container left over from an
  interrupted run) can conflict with a fresh `up`. `run.py` / `rebuild_images.py` detect a
  already-running stack and offer to stop + recreate it before proceeding
  (`lib_docker.maybe_stop_existing_stack`) — accept that prompt, or run
  `docker compose down` (add your active `-f` file(s)) yourself first.
- **Agent container using the wrong `litellm.yaml` / no matching API key found** — a Local/Ollama
  setup only ever writes `config/model-templates/litellm.local-ollama.yaml`, not the root
  `litellm.yaml` that `docker-compose.yaml` mounts. Make sure you're bringing the stack up with
  `docker compose -f docker-compose.local.yaml up` (not plain `docker compose up`) when running
  local — see [Setup § Running fully local](docs/SETUP.md#running-fully-local-no-commercial-llm).
- **`OLLAMA_GPU_ENABLED=true`, but Ollama is actually running on CPU** — `doctor.py` prints this
  warning (with a hard-to-miss `!` banner) whenever the `ollama` container is up and its own startup
  log reports `library=cpu` instead of `library=cuda` — a driver/WSL2 misconfiguration on the host
  otherwise fails silently, with Docker starting the container either way. Check the prerequisites
  in [Setup § GPU Support](docs/SETUP.md#gpu-support), then verify directly with
  `docker compose <your -f args> exec ollama nvidia-smi`.
- **Port already in use (`4000`, `8000`, or `5433`)** — those are the LiteLLM proxy, the ADK web UI,
  and the Postgres host-mapped port respectively (`docker-compose.yaml`). Something else on your
  machine (or a leftover Horseless Carriage stack — see above) is bound to it; stop that process or
  the leftover stack, then retry.
- **Windows: `docker compose up` never asks about file sharing, then the agent can't see your state
  repo** — whichever drive `STATE_REPO_PATH` is on must be shared with Docker Desktop (**Settings →
  Resources → File Sharing**). See [Setup § Setting up on Windows](docs/SETUP.md#setting-up-on-windows).
- **A Dockerfile/entrypoint change doesn't seem to take effect** — `run.py`'s own image build only
  rebuilds Docker layers its cache considers stale; it never re-pulls a mutable base tag
  (`python:3.11-slim`, `ollama/ollama:latest`) on its own. Run `python3 rebuild_images.py` (or
  `python3 run.py dev`, which does this automatically) to force a truly fresh rebuild. See
  [Running the Agent § Rebuilding Images](docs/RUNNING.md#rebuilding-images).

## 3. Budget / cost problems

- **Sprint stops mid-work with a budget message** — this is a deliberate guardrail, not a bug: the
  sprint automatically pauses for review once the token or USD budget is exhausted, or on a
  persistent provider rate limit. See [MANUAL.md §5](MANUAL.md#5-budgets) and
  [Budget Management](docs/BUDGET.md).
- **`🚫 [NO BUDGET-CAPPED KEY]` response from an agent** — that agent has no `scrum-sprint-budget`
  -attached LiteLLM virtual key yet; every specialist agent is hard-blocked in code
  (`check_cost_budget_callback`) from making any LLM call until one exists, so no agent can
  accidentally spend on an unscoped key. Create it (`create_litellm_virtual_key`) before delegating
  to that agent again. Only the Orchestrator is exempt, for its one bootstrap call.
- **Old virtual keys stopped working after `docker compose down -v`** — wiping the LiteLLM database
  invalidates every previously issued virtual key. Recovery (see
  [GITHUB-INTEGRATION.md § Recovering from a LiteLLM Database Wipe](docs/GITHUB-INTEGRATION.md#recovering-from-a-litellm-database-wipe)
  and [BUDGET.md](docs/BUDGET.md)):
  1. Temporarily set `LITELLM_PROXY_API_KEY` to your `LITELLM_MASTER_KEY` in `.env` so the
     Orchestrator can start on an unbounded key.
  2. Run it once — it detects the missing keys and re-creates fresh ones for every agent.
  3. Switch `.env` back to a real per-purpose key. Every *other* agent stays blocked
     (`check_cost_budget_callback`) for the whole window, so only the Orchestrator's bootstrap call
     ever runs unbounded, and only for as long as it takes to recreate everyone's keys.
- **USD budget never seems to move / always shows ~$0.00 on a local/Ollama setup** — expected: a
  self-hosted model has no real per-token price, so `docker-compose.local.yaml` sets
  `LLM_LOCAL_PROVIDER=true` and the USD check is skipped outright rather than "pass" with false
  confidence. The **token budget** (`SPRINT_TOKEN_BUDGET`) is your only real guardrail in this mode
  — see [BUDGET.md § 2](docs/BUDGET.md#2-usd-budget-litellm-layer).
- **429 / rate-limit errors mid-sprint** — the Scrum Master should catch this and trigger a review;
  if it doesn't, it usually means the model or per-agent quota is too aggressive for your provider
  tier. Lower it, or set `LOG_LEVEL=debug` temporarily to see request volume.
- **A sprint never even starts, citing a missing budget** — the Scrum Master's hard guardrail
  requires an explicit, non-zero `TOTAL_USD_BUDGET` before a sprint can start. An unset value
  already defaults to a safe budget; an *explicit* `0` is treated as "no budget" and blocks the
  sprint — check `.env` isn't setting either budget var to `0` on purpose. See
  [MANUAL.md §5](MANUAL.md#5-budgets).

## 4. Mid-session / agent-behavior problems

- **Orchestrator keeps re-describing the same plan instead of acting** — after 3 replies in a row
  with no tool call at all, a `⏸ [NO ACTION TAKEN - ...]` banner is mechanically prepended to its
  next reply (visible directly in the chat, not just in logs), and it also shows up in
  `list_blocking_interactions()` — a code-guaranteed signal, not dependent on the model noticing on
  its own. If you see this banner, tell it plainly to act now (or ask what specifically it's waiting
  on) rather than restating the same instructions again.
- **A reply looks like raw JSON, e.g. `{"type": "function", "function": "repo_status",
  "arguments": {}}`, instead of real text or a real action** — some models occasionally emit a text
  reply shaped exactly like a tool call instead of actually making one, so the intended action never
  runs. **Known issue, not yet fixed in this build** (`main`) — a fix exists on an unmerged branch
  and is tracked as GitHub issue #89 / `ISSUE-0039`, but is not part of this v0.1.0 release. If it
  happens: tell the Orchestrator plainly to actually call the tool rather than describe it (a
  regular follow-up message usually gets it to make the real call), and report the occurrence on
  that issue so it's counted toward prioritizing the fix.
- **Repo/budget config shows as unset, or the session goes silent on the very first message** —
  state (repo URL, budgets, interaction level) is loaded automatically before the Orchestrator's
  first turn each session, so this shouldn't recur; if it does, check that `TOTAL_USD_BUDGET` /
  `SPRINT_TOKEN_BUDGET` in `.env` aren't explicitly set to `0` (see the budget section above).
- **A sprint stalls with no visible signal at all in the terminal** — check
  `list_blocking_interactions()` and, if you have a notifier configured, its output — see
  [Notifications](docs/NOTIFICATIONS.md) for the blocking-interactions list and pluggable notifier
  interface.

## 5. GitHub integration problems

- **GitHub tools fail with an auth error** — verify which method you're using
  ([GitHub Integration](docs/GITHUB-INTEGRATION.md)): a Personal Access Token (`GITHUB_TOKEN`) needs
  `gh auth login` done and the token itself valid; a GitHub App needs all three of
  `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID` set together — a
  partial trio is treated as "not configured" by `doctor.py`.
- **`doctor.py`'s live GitHub check reports access as a warning, not "OK"** — given
  `GITHUB_REPO_URL` and a resolvable credential, `doctor.py` performs a live, read-only check
  against the real repo (reads it, lists its issues, lists its pull requests) and reports the
  repo-level `permissions.push` flag as a proxy for write access. A warning here means the
  credential is present but can't actually read/write what's needed — a fine-grained PAT or a
  GitHub App installation can restrict access independently of the token merely existing. It
  doesn't attempt an actual write, so a clean result is encouraging but not an absolute guarantee.
- **`Could not mint a GitHub App installation token to verify repo access`** — check
  `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` / `GITHUB_APP_INSTALLATION_ID`, and that `PyJWT` and
  `requests` are installed in the environment `doctor.py` runs in.
- **Push to `main` (or `develop`) refused** — both are protected integration branches; `git_push`
  refuses a direct push to either one outright, regardless of which agent calls it, by design. Every
  story is implemented on its own `feature/<story_id>-<slug>` branch and merged in through the
  normal story/sprint PR pipeline instead. See
  [GITHUB-INTEGRATION.md § Branching Model](docs/GITHUB-INTEGRATION.md#branching-model-gitflow).
- **A GitHub App installed on the wrong repo, or missing on a new one** — GitHub App installations
  are per-repo; installing an app on your org doesn't automatically cover every repo in it (relevant
  if you add a repo, e.g. the eval repo, after the App was first installed). Reinstall / add the
  repo under **Settings → Developer settings → GitHub Apps → (your app) → Install App**.

## Where to get more help

- **Security vulnerability?** Do not open a public issue — use GitHub's "Report a vulnerability"
  flow (repo **Security** tab → **Advisories** → **New draft security advisory**). See
  [SECURITY.md](SECURITY.md).
- **Anything else** (bug, question, feature request) — open a
  [GitHub issue](https://github.com/CI-Till-Krempel/Horseless-Carriage/issues) using the Bug Report
  or Feature Request template (`.github/ISSUE_TEMPLATE/`).
- Contributing a fix yourself — see `.github/PULL_REQUEST_TEMPLATE.md`'s checklist (docs updated,
  CI green, no secrets included).
