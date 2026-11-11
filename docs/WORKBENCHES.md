[← Back to README](../README.md)

# Development Workbench Templates

A **Development Workbench** is the toolchain (JDK, Node, Python packages,
Android SDK, ...) the agent image needs to scaffold, develop, test, run, and
debug a *product's* actual tech stack — as opposed to anything
Horseless-Carriage itself needs to run. Every project gets exactly one,
tracked as `.hc/workbench-setup.sh` in that project's own state repo, built
into the agent image by `agent.Dockerfile`'s `workbench` build context.

Historically the Architect hand-wrote that script from scratch, every time,
for every project. This doc describes the **template mechanism** (EP-0010)
that lets the Architect instead pick a named, pre-built starting point —
and how to contribute a new one.

## Who this is for

Read this if you're adding a template for a tech stack that doesn't have one
yet (e.g. "Go + Gin" or "Rails"), or if you want to understand what
`propose_framework_change(template_name=...)` actually does before relying
on it. It assumes you've skimmed [Architecture](ARCHITECTURE.md) already.

**Scope note (GH-291):** this epic is GH-291's *Phase 1* — the Development
Workbench, for the technical roles only (`architect`, `dev_team`,
`qa_agent`, `quality_guardian`). Role-specific workbenches for the Product
Owner, UX, or Testing roles (also part of GH-291's broader "Role
Workbenches" concept) are explicitly out of scope here and are a later
phase, not covered by this doc. See also
[GH-456](https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/456)
("document the list of supported tech stacks... check regularly whether
tooling is set up") — this doc, plus the per-template `manifest.json`
descriptions below, *is* that list; GH-456 was moved into v0.3 precisely
because it falls out of this epic shipping.

## The template contract

A template is a directory under `config/development-workbench-templates/<name>/`
— the same convention `config/model-templates/` already uses for LLM
provider configs, applied to tech-stack tooling instead of model routing.
`<name>` must be a plain lowercase alphanumeric-and-hyphen string (e.g.
`python-flask`) — no `/`, no `..`, no path syntax; `template_name` is
ultimately agent-supplied, so `resolve_workbench_template`
(`agents/scrum_team/tools/workbench_templates.py`) validates it against
`^[a-z0-9]+(-[a-z0-9]+)*$` before ever touching the filesystem.

Three files, resolved by `resolve_workbench_template`/`get_workbench_template`:

| File | Required? | Contract |
|---|---|---|
| `workbench-setup.sh` | **Required** | The full toolchain install script. Copied verbatim into `.hc/workbench-setup.sh` (unless the caller supplies its own `workbench_setup_script` to customize/replace it). A directory missing this file is skipped by `list_workbench_templates` (not an error — lets a new template be staged incrementally) and `resolve_workbench_template` refuses to resolve it. |
| `mcp-config.json` | Optional | An MCP config fragment shaped exactly like this repo's own `.mcp.json`: `{"mcpServers": {...}}`. Merged into `.hc/workbench-mcp.json`. Missing or unreadable → treated as `{}`, never an error. |
| `manifest.json` | Optional | `{"description": str, "additional_allowlist_hosts": [str, ...]}` — a human-readable blurb (shown to the Architect via `list_workbench_templates`/`get_workbench_template`, and folded into the proposal PR body) and the package-repository hosts this stack's toolchain needs at **runtime**. Missing → empty description, empty allowlist. |

Two read-only, agent-callable tools let the Architect discover/preview a
template before committing to it:
- **`list_workbench_templates()`** — every subdirectory with a
  `workbench-setup.sh`, sorted by name, with its `description` and
  `additional_allowlist_hosts` from `manifest.json`.
- **`get_workbench_template(template_name)`** — the same bundle
  `resolve_workbench_template` would load (full script content, MCP config,
  allowlist hosts, description), for inspection before deciding what to
  customize. Never writes anything, never proposes anything.

Templates live in **Horseless Carriage's own repo** (read via
`_project_root()`), never the product/state repo — the same separation
`propose_steering_change` already relies on for `identity.md`. Nothing in
`workbench_templates.py` ever writes to this directory; it's a read-only
library baked into the agent image at build time
(`agent.Dockerfile`'s `COPY ./config/development-workbench-templates
./config/development-workbench-templates`) and additionally bind-mounted
read-only by the dev compose stack, so a new/edited template doesn't need
an image rebuild to try out.

### Wiring up scaffold/dev/test/exploratory-test/run/debug tooling

There's no separate field or schema for each of these — `workbench-setup.sh`
is a single, plain shell script (`sh`, not bash-specific, since the base
agent image is Debian's `python:3.11-slim`) that:
1. Installs whatever packages the stack needs on top of the base image
   (only what the base image doesn't already provide — e.g. `python-flask`
   adds nothing for Python itself, since the base image already ships
   Python 3.11 + pip).
2. Ends with a heredoc (`cat <<'EOF' ... EOF`) printing the actual
   scaffold/install/test/run/debug/exploratory-testing commands as
   human-readable instructions — this is how the Dev Team / QA roles
   discover what to actually type, since the script's job is *installing
   the toolchain*, not running the product's own build.

That's the whole contract: there's no required "debug command" field to
fill in, no schema to satisfy beyond "installs the toolchain, then explains
how to use it." See the worked example below for the exact shape.

## Worked example: `python-flask`

`config/development-workbench-templates/python-flask/` is a real, shipped
reference template (US-0022) — the clearest concrete illustration of the
contract above.

**`manifest.json`:**
```json
{
  "description": "Python + Flask: scaffold, install, test (pytest/pytest-flask), run, debug (debugpy), and exploratory-test (HTTPie/`flask shell`) a Flask application.",
  "additional_allowlist_hosts": [
    "pypi.org",
    "files.pythonhosted.org"
  ]
}
```

**`mcp-config.json`:**
```json
{
  "mcpServers": {
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    }
  }
}
```

**`workbench-setup.sh`** (abridged — see the real file for the full text):
```sh
#!/bin/sh
set -e

pip install --no-cache-dir \
    flask \
    pytest \
    pytest-flask \
    debugpy \
    httpie

cat <<'EOF'
python-flask workbench ready: flask, pytest, pytest-flask, debugpy, httpie installed.

Scaffold (minimal app, from the project root): ...
Install project-declared deps:   pip install -r requirements.txt
Test:                             pytest
Run:                              flask --app app run --debug --host=0.0.0.0
Debug (attach on port 5678):      python -m debugpy --listen 0.0.0.0:5678 --wait-for-client ...
Exploratory testing:              http GET http://127.0.0.1:5000/   # HTTPie
                                  flask --app app shell               # REPL
EOF
```

Each tooling concern from the contract maps onto a line in that heredoc:
scaffold (the `mkdir`/boilerplate instructions), install (`pip install -r
requirements.txt`), test (`pytest`), run (`flask run`), debug (`debugpy`
attach), exploratory-test (HTTPie against the running dev server, or
`flask shell` for an interactive REPL with full app context).

The other two shipped reference templates follow the same shape with
stack-appropriate substitutions — see
`config/development-workbench-templates/kotlin-multiplatform/workbench-setup.sh`
(Gradle test tasks, Android run/debug via `adb`/JDWP, and an explicit,
loudly-commented **iOS limitation**: this workbench runs in a Linux
container, so Kotlin/Native iOS *test* tasks run here, but actual iOS
build/run/debug needs a real macOS host with Xcode — never promised as
working inside the container) and
`config/development-workbench-templates/react-vite-firebase/workbench-setup.sh`
(Vitest, the Vite dev server, the Firebase emulator suite for
exploratory-testing Functions+Firestore without touching a real Firebase
project, and Node's `--inspect-brk` for debugging).

Each reference template also ships a `smoke-test.sh` that builds a
throwaway Docker image from that template's own `workbench-setup.sh` and
scaffolds/tests a minimal app inside it end to end — a reasonable pattern
to copy for a new template, though it's a manual/CI-only script (it needs
real internet access that the sandboxed `agent` test container's
`net-proxy` allowlist deliberately doesn't grant), not part of the template
contract itself.

## How `propose_framework_change` turns a template into a PR

`propose_framework_change` (`agents/scrum_team/tools/workflow.py`) is still
the **only** path from a template (or a fully hand-written script) to
`.hc/workbench-setup.sh` — there is no direct write; `docs.py`'s `write_file`/
`delete_file` refuse any `.hc/` path outright and point here instead.
Every workbench change, templated or not, is a human-reviewed draft PR,
exactly as before this mechanism existed.

Relevant parameters:
- **`template_name`** (optional): starts from the named template under
  `config/development-workbench-templates/<template_name>/`. Resolved via
  `resolve_workbench_template` — an unknown or invalid name is refused with
  the list of what *is* available.
- **`workbench_setup_script`** (optional when `template_name` is given): the
  full replacement content for `.hc/workbench-setup.sh` (like
  `propose_steering_change`'s `new_content` — not a diff). Omit it to use
  the template's script unchanged; pass the template's script back with
  edits (e.g. one extra `apt-get install` line) to customize it. Either way,
  only the resulting PR ever sees it — never a direct write.
- **`additional_mcp_config`** (optional): `{"mcpServers": {...}}`, merged on
  top of the template's own `mcp-config.json` (this dict's entries win on a
  key collision), written to `.hc/workbench-mcp.json` in the same commit.
  With no `template_name`, this is the *whole* MCP config. Note: this is a
  new, versioned, human-reviewed artifact — the mechanism that merges
  `.hc/workbench-mcp.json` into an actual running agent session is
  specified, not yet wired for every role; full per-role session wiring can
  land incrementally without changing this file's format or how it's
  proposed.
- **`additional_allowlist_hosts`** (optional): extra runtime hosts beyond
  whatever the template itself declares, unioned together (see below).
- **`rationale`** (required, ≥15 characters, concrete — not a placeholder):
  why this change is needed.

With no `template_name` at all, today's fully-custom-script path keeps
working completely unchanged — templates are additive, never a replacement
for the free-form option.

`propose_framework_change` always opens a **draft** PR against the
product/state repo's `develop` branch and leaves it unmerged for human
review, regardless of interaction level — identical to
`propose_steering_change`'s guarantee. It refuses a missing/too-short
rationale, an unknown/invalid `template_name`, neither
`workbench_setup_script` nor `template_name` given, or a no-op (the
effective script and MCP config already match what's committed).

## The network-allowlist step: human approval, not automatic

This is the one step in the whole flow that is **not** self-applying, and
it's worth being explicit about so a contributor doesn't assume otherwise.

`additional_allowlist_hosts` — whether declared by a template's own
`manifest.json`, passed explicitly to `propose_framework_change`, or both
(they're unioned) — describes hostnames the running agent container needs
to reach at **runtime** (e.g. `pypi.org` for pip, `repo.maven.apache.org`
for Gradle) that the agent's network sandbox (`agent-net` + `net-proxy`'s
allowlist) doesn't already permit. This is a structurally separate concern
from the *build-time* network access every Dockerfile line already has —
`apt-get`/`curl` inside `workbench-setup.sh` itself can already reach the
open internet while the image builds; `additional_allowlist_hosts` is
specifically about what the **running** container can reach afterward.

`propose_framework_change` **cannot apply an allowlist change itself**:
`config/net-proxy/allowlist.filter` lives in Horseless Carriage's own repo,
which this tool has no filesystem access to and no git credentials for
(only `GITHUB_REPO_URL`/`GITHUB_TOKEN` for the *product* repo) — a genuine
structural boundary, not a policy choice that could be loosened later.

When the effective (template + extra) `additional_allowlist_hosts` list is
non-empty, the opened PR body gets an explicit **"⚠️ HUMAN OPERATOR ACTION
REQUIRED (outside this repo)"** section, and a blocking interaction is also
recorded (`record_blocking_interaction`) so a human operator sees it as an
immediate action item rather than just another PR in a queue — the Tested
gate stays blocked on this story until the operator-side step genuinely
happens. Today (per `propose_framework_change`'s own docstring and this
PR-body text), that step is:

1. Add the listed host(s) (comma-separated) to `NET_PROXY_EXTRA_ALLOWLIST_HOSTS`
   in **Horseless Carriage's own** `.env` (see `.env.example`) — no need to
   edit the tracked `config/net-proxy/allowlist.filter` directly.
2. Run `docker compose up` (not just `restart`) for the change to take
   effect — `entrypoint.sh` merges `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` into
   the live allowlist idempotently on container start.

This is deliberately **today's** behavior, not ADR-0002's design: see
`specs/architecture/ADR-0002-Per-Project-Workbench-Network-Allowlist-Proposals.md`
(Status: Proposed, not yet implemented) for a tracked
`.hc/allowlist.filter` file in the product repo as a possible successor.
EP-0010 builds on top of today's PR-body-only/`.env`-based behavior rather
than implementing that ADR — if ADR-0002 ships later, this section should
be updated to match, but until then: **merging the workbench PR alone is
not enough** to actually grant the new network access. Don't assume an
`additional_allowlist_hosts` entry takes effect just because its PR merged.

## Adding a new template

To add support for a new stack (e.g. `go-gin`):

1. Create `config/development-workbench-templates/go-gin/`.
2. Write `workbench-setup.sh`: install whatever the base image
   (`python:3.11-slim`) doesn't already have, then end with a heredoc
   documenting scaffold/install/test/run/debug/exploratory-test commands,
   following the `python-flask` shape above.
3. Write `manifest.json`: a one-paragraph `description` (this is what
   `list_workbench_templates`/`get_workbench_template` show the Architect,
   and what ends up in the proposal PR body — write it for a human
   deciding whether this template fits their project) and
   `additional_allowlist_hosts` (the package-repository hosts this
   toolchain needs at runtime — at minimum, the registry it installs
   dependencies from).
4. Write `mcp-config.json` if an MCP server is relevant to this stack
   (optional — omit the file entirely if not).
5. If there's a stack-specific constraint that can't actually work inside
   this Linux container (the way `kotlin-multiplatform`'s iOS build/run/debug
   can't), document it loudly in both `manifest.json`'s description *and* a
   top-of-file comment in `workbench-setup.sh` — don't let the gap surface
   only when someone tries it and it silently fails.
6. Optionally add a `smoke-test.sh` following the reference templates'
   pattern (build a throwaway image from the script, scaffold+test a
   minimal app inside it) as a manual/CI check, not something
   `resolve_workbench_template` reads.
7. No code change is needed in `agents/scrum_team/tools/workbench_templates.py`
   itself — `list_workbench_templates` discovers any subdirectory with a
   `workbench-setup.sh` automatically.

## See also

- [Architecture](ARCHITECTURE.md) — overall system design, including the
  "enforce mandatory process mechanically" principle this mechanism follows
  (every workbench change, templated or not, is forced through a
  human-reviewed PR; there's no bypass path).
- [Configuration Reference](CONFIGURATION.md) — `.env`-driven configuration,
  including `NET_PROXY_EXTRA_ALLOWLIST_HOSTS` and the network sandbox this
  doc's allowlist section builds on.
- `specs/architecture/ADR-0002-Per-Project-Workbench-Network-Allowlist-Proposals.md`
  — the (currently unimplemented) tracked-allowlist-file design.
- `specs/stories/EP-0010-Pluggable-Development-Workbench-Templates.md`,
  `US-0021-Introduce-the-Development-Workbench-Template-Mechanism.md`,
  `US-0022-Ship-Three-Reference-Development-Workbenches.md` — the stories
  that introduced this mechanism and its three reference templates.
