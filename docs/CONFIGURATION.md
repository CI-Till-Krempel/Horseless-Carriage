[← Back to README](../README.md)

# Configuration Reference & Combination Matrix

A single place to see every `.env`-driven configuration item, what it
controls, and — more importantly — how items interact with *each other*.
Individually, every item here is already documented (in `.env.example`,
`docs/SETUP.md`, `docs/BUDGET.md`, `docs/INTERACTION-LEVELS.md`, etc.); this
page's job is the combinations: which pairings are valid, which are
harmlessly inert, and which silently produce a broken or confusing setup.

## Setup wizard flow

`setup_llm.py` asks about the provider/model you actually came here to
configure *before* asking about surrounding project settings:

1. Provider choice → API key (cloud) or curated model pick (local) → model
   selection → GPU acceleration (local only).
2. *Then* the project-wide settings: Git identity, state repository,
   human interaction level, sprint budgets.
3. Write `.env` + `litellm.yaml` → live end-to-end test.

The Git identity prompt defaults to the host machine's own `git config --global
user.name`/`user.email` (see `setup_llm._host_git_identity`) when nothing is already
configured in `.env`. This is safe because real commits the agent makes are
attributed per-role (`Architect`, `DevTeam`, ...) via `GIT_AUTHOR_NAME`/
`GIT_COMMITTER_NAME` overrides (see `agents/scrum_team/tools/base.py`), not
this value — `GIT_USER_NAME`/`GIT_USER_EMAIL` is only the global git-config
fallback for anything that doesn't get that per-role override.

## The provider axis

Exactly one of these is "active" at a time, selected by which
`litellm.yaml` is in place (cloud) or by `docker-compose.local*.yaml` (local):

| Provider | API key env var | Model config file | USD budget meaningful? |
|---|---|---|---|
| Google Gemini | `GOOGLE_API_KEY` | `litellm.yaml` (copy of `litellm.cloud-gemini.yaml`) | Yes |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `litellm.yaml` (copy of `litellm.cloud-anthropic.yaml`) | Yes |
| OpenAI | `OPENAI_API_KEY` | `litellm.yaml` (copy of `litellm.cloud-openai.yaml`) | Yes |
| Local / Ollama | none | `config/model-templates/litellm.local-ollama.yaml` | No — `LLM_LOCAL_PROVIDER=true` skips the USD check entirely (self-hosted inference has no real per-token price) |

**Inert-but-harmless combination**: switching providers leaves the *other*
providers' API keys sitting unused in `.env` — not an error, just dead
config. `doctor.py` reports the active provider based on which config file
was written most recently; on a virgin `git clone` (before `setup_llm.py`
has ever run), every tracked config file has essentially the same
checkout-time mtime, so this "active provider" line is undefined until the
wizard has actually run once — cosmetic only, doctor's real ERROR checks
(missing `.env`, missing `STATE_REPO_PATH`) still gate correctly regardless.

## The local-provider sub-axis: GPU / host-mode / CPU

For the Local/Ollama provider only, three mutually-exclusive execution modes:

| Mode | Compose files | When to use |
|---|---|---|
| CPU-only (default) | `docker-compose.local.yaml` | No NVIDIA GPU, or macOS (no GPU passthrough into Docker Desktop at all) |
| Dockerized GPU | `docker-compose.local.yaml -f docker-compose.gpu.yaml` | Linux/Windows with an NVIDIA GPU, drivers, and (Linux) the NVIDIA Container Toolkit / (Windows) the WSL2 backend |
| Native host Ollama | `docker-compose.local-hostollama.yaml` | macOS, to get Metal acceleration by running Ollama natively on the host instead of in Docker (Docker Desktop for Mac has no GPU passthrough) |

`OLLAMA_HOST_MODE=true` and `OLLAMA_GPU_ENABLED=true` are mutually
exclusive — host mode bypasses the dockerized `ollama` service (and its GPU
override) entirely, and `setup_llm.py` never offers GPU acceleration once
host mode is chosen. **Known-invalid combination** (manual `-f` stacking
only, not reachable through the wizard): merging `docker-compose.gpu.yaml`
onto `docker-compose.local-hostollama.yaml` by hand — the GPU file defines
only a partial `ollama` service fragment (resource reservations, no
image/build), and `docker-compose.local-hostollama.yaml` has no `ollama`
service at all by design, so Compose synthesizes an unbuildable service and
`up` fails outright. Only ever combine `docker-compose.gpu.yaml` with
plain `docker-compose.local.yaml`.

`OLLAMA_GPU_ENABLED=true` with no NVIDIA GPU actually present starts but
runs CPU-only — `doctor.py` scrapes the `ollama` container's own startup
logs and warns loudly if it reports `library=cpu` despite the flag being on.

## Human interaction level × everything else

`INTERACTION_LEVEL` (`Product` / `Stakeholder` / `CEO` / `EVAL`,
case-insensitive; unset or unrecognized falls back to the most restrictive,
`Product`) changes which approvals gate `advance_story_stage`,
`create_release_pr`, and how much detail `create_sprint_report` renders —
see [Interaction Levels](INTERACTION-LEVELS.md) for the full table. It's
independent of the provider axis (works identically with any of the four
providers) but interacts with one other setting: **`EVAL` is meant to be
set by the eval harness itself** (`run_eval.py` forces it for the isolated
eval run), not hand-configured for a real engagement — setting it manually
removes every human approval gate for that `.env`.

## Budget axis

| Var | Scope | Meaningful when |
|---|---|---|
| `SPRINT_TOKEN_BUDGET` | Per-sprint, resets automatically | Always (local and cloud) |
| `TOTAL_USD_BUDGET` (canonical) / `SPRINT_USD_BUDGET` (deprecated fallback) | Whole engagement, never resets | Cloud providers only — a no-op for Local/Ollama |
| `EVAL_SPRINT_TOKEN_BUDGET` / `EVAL_USD_BUDGET_PER_SPRINT` (+ deprecated `EVAL_SPRINT_USD_BUDGET`) | Eval-harness-only, separate from a real engagement's budget | Only when `run_eval.py` runs |
| `PROCESS_OVERHEAD_PERCENTAGE` | Applies to both budgets | Always |

`setup_llm.py`'s local-provider flow (`is_local=True`) skips the
`TOTAL_USD_BUDGET` question entirely rather than asking something that can
never be enforced — it still writes a harmless default so `.env` stays
consistent if the same file is later reconfigured for a cloud provider.

## GitHub auth axis

`GITHUB_TOKEN` (Personal Access Token) takes precedence over the
`GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/`GITHUB_APP_INSTALLATION_ID` trio
if both are somehow present — see [GitHub Integration](GITHUB-INTEGRATION.md).
The trio only works if all three are set together; a partial trio degrades
to "no GitHub auth configured" the same as none being set at all.
`config/github_config.yaml` is a **human-facing notes file only** (repo
branch-protection policy, etc.) — nothing in the codebase reads it; real
auth flows exclusively through the env vars above.

## `.env` file selection axis

| File you copy `.env` from | Compose file(s) it pairs with |
|---|---|
| `.env.example` | `docker-compose.yaml` (any cloud provider) |
| `.env.local.example` | `docker-compose.local.yaml` (± `docker-compose.gpu.yaml`) or `docker-compose.local-hostollama.yaml` |
| `.env.test` | `docker-compose.yaml`, via `run_tests.py` only — deliberately exercises the deprecated `SPRINT_USD_BUDGET`/`EVAL_SPRINT_USD_BUDGET` fallback names as a live regression check, not a template to copy by hand |

`setup_llm.py` picks the right starting file for you based on which
provider you choose (cloud → `.env.example`, local → `.env.local.example`),
so this axis is only something to know about if you're hand-editing `.env`
instead of using the wizard.

## Known-safe no-ops (not bugs, listed here so they don't look like one)

- Cloud API keys left in `.env` for a provider you're not currently using.
- `TOTAL_USD_BUDGET` set to any value while `LLM_LOCAL_PROVIDER=true`.
- `OLLAMA_MODEL`/`OLLAMA_KEEP_ALIVE`/`OLLAMA_GPU_ENABLED` set while using a cloud provider — never read by any cloud compose path.
- `WATCH_POLL_INTERVAL_SECONDS` — only read by the opt-in `watch_roadmap.py`, itself never invoked automatically.
