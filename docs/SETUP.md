[← Back to README](../README.md)

# Setup

> **Windows users:** install the prerequisites in [Setting up on Windows](#setting-up-on-windows)
> below first, then follow steps 1-2 the same way, using `python` instead of `python3`.

## Guided, all-in-one setup: `setup_all.py`

```bash
python3 setup_all.py
```

Asks about developer mode first (before any container work happens - a Local/Ollama setup's own live
test starts a container developer mode would rebuild, so this can't wait until the end), then runs
steps 1-3 below in order, then offers to start the agent (`run.py`) - this is the fastest path for a
first-time or new-machine setup. Each step is still a fully standalone script (see the sections below)
if you'd rather run just one of them, or control the pace yourself; `setup_all.py` just chains them
and adds a fix→retry loop around each one.

## 1. Guided LLM/project setup: `setup_llm.py`

```bash
python3 setup_llm.py
```

This interactively walks you through everything specific to *this* run of
the team:

1. **Provider**: Google Gemini, Anthropic Claude, OpenAI, or fully local via
   Ollama.
2. **API key** (cloud providers only) - reuses an existing key from `.env` if
   present, otherwise prompts for one.
3. **Model**: fetches the provider's *current* model list via its own API
   (not a hardcoded/stale list) and lets you pick a main model for all
   scrum-team roles, plus an optional cheaper/faster model for the automated
   eval harness's `scrum-eval-cheap` alias. The local/Ollama provider instead
   offers a curated pick-list (Ollama has no such API) or a manual tag.
4. **Git identity** (user name/email used as a fallback git identity;
   defaults to your host machine's own `git config --global` name/email if
   set, otherwise a generic placeholder - real commits the agent makes are
   attributed per-role regardless, see [GitHub Integration](GITHUB-INTEGRATION.md))
   and the **state repository** itself: creates the directory if missing,
   then either clones `GITHUB_REPO_URL` into it (if empty and a URL is
   given) or initializes a fresh local git repo there - see
   [State Repository](STATE-REPOSITORY.md) for what this directory is and
   why it needs to exist before the agent can run.
5. **Human interaction level** (`Product` / `Stakeholder` / `CEO` / `EVAL` -
   see below) and **sprint budgets** (token budget, USD budget, max process
   overhead percentage - see [Budget Management](BUDGET.md)).
6. Writes all of the above into `.env` and the active `litellm.yaml` (or,
   for the local provider, `config/model-templates/litellm.local-ollama.yaml`).
7. **Live test**: starts the `db` + `litellm` (+ `ollama`) containers and
   sends one real, minimal request through the proxy to confirm the new
   configuration actually works end-to-end, before you invest time running
   the full team against it.

Re-run it any time to switch provider/model, repoint the state repository, or
adjust budgets - it reuses whatever's already in `.env` as the default for
each prompt.

## 2. Project/infrastructure setup: `setup_project.py`

```bash
python3 setup_project.py
```

This will:
1. Check for Docker and Docker Compose.
2. Guide you through GitHub CLI setup.
3. Create a `.env` file from the template (if it doesn't exist - `setup_llm.py`
   above will already have created and populated it in most cases).
4. Start the database and LiteLLM containers.

After running setup, please edit the `.env` file to add your specific API keys and configuration.

Before running the agent, it is recommended to run the doctor script to validate your setup:

```bash
python3 doctor.py
```

See [Running the Agent § Diagnostics & Maintenance](RUNNING.md#diagnostics--maintenance)
for what `doctor.py` checks.

## Setting up on Windows

All of this project's host-side tooling (`setup_all.py`, `setup_llm.py`, `setup_project.py`,
`doctor.py`, `run.py`, `run_tests.py`, `check_state_repo.py`, `rebuild_images.py`,
`watch_roadmap.py`) is plain stdlib Python - no bash, no WSL2 required. Docker Desktop may still use
WSL2 internally as its own backend, but that's automatic and not something you need to set up
yourself.

**Prerequisites:**

1. **Python 3.9+** - install from [python.org](https://www.python.org/downloads/) (check "Add
   python.exe to PATH" during setup) or via `winget install Python.Python.3.12`. Windows installs
   register the `python` command (and the `py` launcher) - **not** `python3` - so use `python
   setup_llm.py` etc. instead of `python3 ...` everywhere in these docs.
2. **Docker Desktop** - [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
   Requires virtualization enabled; Docker Desktop will offer to set up the WSL2 backend for you on
   first run (Hyper-V is the alternative backend). After installing, open a *new* terminal and verify
   with `docker --version` and `docker compose version`.
3. **Git for Windows** - [git-scm.com/download/win](https://git-scm.com/download/win/) - `setup_llm.py`
   uses it directly to set up the [state repository](STATE-REPOSITORY.md) (clone or `git init`); the
   agent's own commits happen using the separate git already installed inside the `agent` container.
4. **GitHub CLI (`gh`)** - [cli.github.com](https://cli.github.com/), or `winget install GitHub.cli`,
   then `gh auth login`.

**Running the scripts** - identical from PowerShell, cmd.exe, or Git Bash (pick whichever terminal
you're comfortable with):

```powershell
python setup_llm.py
python setup_project.py
python doctor.py
python run.py
```

**A few Windows-specific things to know:**

- **`STATE_REPO_PATH`**: a normal Windows path works in `.env`, e.g.
  `STATE_REPO_PATH=C:\Users\you\horseless-carriage-state` or `STATE_REPO_PATH=C:/Users/you/horseless-carriage-state`
  (Python's `pathlib` accepts either). Whichever drive it's on must be shared with Docker Desktop
  (Settings -> Resources -> File Sharing) for the `docker-compose.yaml` bind mount to work.
- **Copying `.env` templates manually** (only needed if you skip `setup_llm.py`/`setup_project.py`,
  which already do this for you): PowerShell's `copy` works like Unix `cp` - `copy .env.example .env`;
  the same command works in cmd.exe too.
- **Firewall prompts**: the first `docker compose up` may trigger a Windows Defender Firewall prompt
  for the ports LiteLLM (`4000`) and the ADK web UI (`8000`) listen on. Allow access on your private
  network. The local/Ollama setup's `ollama` container is not published to the host at all (litellm
  reaches it over Compose's internal network), so it triggers no such prompt - if you have a native
  Ollama install listening on its default `11434`, it's untouched and won't conflict.
- **Long path limits**: this only affects tooling running directly on the Windows host, never inside
  the Linux `agent` container (which has no such limit). If you ever hit it anyway, enable long paths
  with `git config --system core.longpaths true`.

## Running fully local (no commercial LLM)

To run the whole team against a self-hosted [Ollama](https://ollama.com) model instead of Gemini/OpenAI/Anthropic — no provider API keys, no external network calls once the model is pulled:

```bash
cp .env.local.example .env        # Windows: copy .env.local.example .env
docker compose -f docker-compose.local.yaml up
```

This starts the same `db` + `litellm` + `agent` services as `docker-compose.yaml`, plus an `ollama` container that pulls its model on first start (see `ollama-entrypoint.sh`). Every `scrum-*` role alias is routed to that one model — see `config/model-templates/litellm.local-ollama.yaml`. The default model is `llama3.1:8b` (tool-calling support, ~16GB RAM or any modern GPU); to fit different hardware, change the `model:` lines in that file and the matching `OLLAMA_MODEL` in `.env`/`docker-compose.local.yaml` together (see comments in both files for smaller/larger alternatives). Don't run this alongside `docker-compose.yaml` — the two `litellm` service definitions target different config files.

### Performance Tuning

- **Keep the model loaded**: `OLLAMA_KEEP_ALIVE` (`.env`, default `-1`) controls how long an idle
  model stays in memory before Ollama unloads it - Ollama's own default is `5m`, so without this the
  container pays the full model-load time again after every 5 minutes of idle time. Since this
  container serves one model to this one team continuously, `-1` (never unload) is the right default
  here; only lower it if you're sharing this Ollama instance with other, bursty workloads too where
  freeing the memory between uses is actually desired.
- **CPU/RAM**: `docker-compose.local.yaml` deliberately sets no `deploy.resources.limits` on the
  `ollama` service, so it isn't artificially capped by this repo's own configuration - by default a
  container can already use as much CPU/RAM as Docker Desktop's own VM is allocated. If Ollama still
  seems starved, check **Docker Desktop -> Settings -> Resources -> Advanced** on the host and
  increase the CPU/memory allocated to the Docker VM itself; that allocation, not anything in this
  repo, is the actual ceiling.
- See also [GPU Support](#gpu-support) below for hardware-accelerated inference.
- **Picked up a Dockerfile/entrypoint change and it doesn't seem to apply?** `python3 rebuild_images.py`
  force-rebuilds the `ollama` (and `agent`) image, pulling fresh base images - `run.py`'s own
  `--build` only rebuilds layers Docker's cache considers stale, which won't catch everything.

For a more detailed guide, see the [PREFLIGHT.md](../PREFLIGHT.md) checklist.

## GPU Support

The fully-local Ollama stack (above) runs CPU-only by default. `setup_llm.py`'s Local/Ollama flow
asks about this directly - it detects a usable NVIDIA GPU on this machine (via `nvidia-smi`),
recommends enabling it if one is found, and writes `OLLAMA_GPU_ENABLED` into `.env` accordingly;
`run.py` then includes the GPU override file automatically whenever that's set, so day-to-day you
don't need to think about the flag at all. Re-running `setup_llm.py` re-detects the GPU but defaults
to whatever you already had configured, not the fresh detection result, so it won't silently flip
a deliberate choice back.

To enable (or check) it manually - e.g. if you're skipping `setup_llm.py`, or want to run one-off
commands with it - merge in the GPU override file instead of hand-editing `docker-compose.local.yaml`:

```bash
docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml up
```

**Prerequisites** (the override file alone does nothing without these):
- An NVIDIA GPU with up-to-date drivers.
- **Windows (Docker Desktop)**: enable the WSL2 backend (Settings -> General -> "Use the WSL 2 based
  engine") and install an NVIDIA driver on the Windows host itself (not inside WSL) that supports
  [CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html). Docker Desktop 4.x+ then
  exposes the GPU to containers automatically - no separate `nvidia-container-toolkit` install is
  needed on Windows specifically.
- **Linux**: install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  and restart Docker.

**Verify it actually worked** - don't assume the override file alone is sufficient; a driver/WSL2
misconfiguration on the host will silently leave Ollama running on CPU with no error from Docker:

```bash
docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml exec ollama nvidia-smi
```

This should list your GPU. You can also check what Ollama itself detected at startup - look for
`library=cuda` (GPU found) vs. `library=cpu` (fell back) in its own log:

```bash
docker compose -f docker-compose.local.yaml -f docker-compose.gpu.yaml logs ollama | grep "inference compute"
```

You don't need to run either of these by hand, though: `doctor.py` (and therefore `run.py`'s
pre-flight gate) already does this check for you whenever `OLLAMA_GPU_ENABLED=true` and the
`ollama` container is up, and prints a hard-to-miss warning banner if it finds `library=cpu`.

### macOS: native Ollama instead (GH issue #93)

Everything above is NVIDIA-only - Docker Desktop for Mac has no GPU passthrough at all, so a
dockerized Ollama always runs CPU-only there, even on Apple Silicon (see
[this writeup](https://chariotsolutions.com/blog/post/apple-silicon-gpus-docker-and-ollama-pick-two/)).
The workaround is to run Ollama natively on the host instead of in a container, so it can use the
GPU (Metal) directly - the rest of the stack (`db`, `litellm`, `agent`) still runs in Docker exactly
as before, `litellm` just talks to your host's Ollama over `host.docker.internal` instead of to a
container.

`setup_llm.py`'s Local/Ollama flow detects macOS and offers this by default; it writes
`OLLAMA_HOST_MODE=true` to `.env` and points `config/model-templates/litellm.local-ollama.yaml`'s
`api_base` at your host instead of the dockerized service. To set it up manually:

```bash
# 1. Install and start Ollama natively on the host:
#    https://ollama.com/download
ollama serve

# 2. Pull the model configured in .env's OLLAMA_MODEL (default llama3.1:8b):
ollama pull llama3.1:8b

# 3. Use the host-Ollama compose file INSTEAD OF docker-compose.local.yaml
#    (a separate file, not a merged override - Compose merges rather than
#    replaces `depends_on` across `-f` files, so an override alone can't
#    remove litellm's dependency on the dockerized `ollama` service):
docker compose -f docker-compose.local-hostollama.yaml up
```

This works the same way on Linux/Windows too, if you'd rather run Ollama natively there for any
other reason - `docker-compose.local-hostollama.yaml` adds the `host.docker.internal` DNS mapping
Linux's Docker Engine needs (Docker Desktop on Mac/Windows already resolves it automatically).

Mutually exclusive with the NVIDIA GPU override above: host mode bypasses the dockerized `ollama`
service entirely, so there's nothing there for `docker-compose.gpu.yaml` to accelerate.
`doctor.py` checks host-mode reachability directly (a plain HTTP request to
`http://localhost:11434`) instead of inspecting a container, since there is no container to inspect.

## Human Interaction Levels

How much of a human is actually in the loop is configurable via `INTERACTION_LEVEL` (`.env`, set
interactively by `setup_llm.py`) - see [docs/INTERACTION-LEVELS.md](INTERACTION-LEVELS.md) for the
full breakdown. Four levels:

| Level | The human's role |
|---|---|
| `Product` (default) | Product Owner stand-in: task-level priorities, developer questions. |
| `Stakeholder` | Business stakeholder: business needs, release order, feature approval, review feedback. |
| `CEO` | Approves only the sprint budget; reads the sprint report as a management summary. |
| `EVAL` | No human at all - fixed-length automated evaluation runs (see [Evaluation](EVALUATION.md)). |

This isn't just documentation - it changes which `record_human_approval` gate is mechanically
required before the team may implement stories (`advance_story_stage(..., "Implemented")`) or
release an increment (`create_release_pr`), and how much detail `create_sprint_report` actually
renders (full technical detail at Product/EVAL, business-framed at Stakeholder, budget-and-headlines
only at CEO); see the linked doc for the exact mapping.

## Notes

- If `LITELLM_PROXY_API_BASE` is set, the agents assume "proxy mode" and use LiteLLM via the proxy endpoint.
- Keep your `.env` local and never commit real API keys.
- See [Configuration Reference](CONFIGURATION.md) for how every `.env` item combines with the
  others (provider × GPU/host-mode, budget applicability, interaction level, etc.) - useful when
  hand-editing `.env` instead of going through the wizard, or when something looks misconfigured.
