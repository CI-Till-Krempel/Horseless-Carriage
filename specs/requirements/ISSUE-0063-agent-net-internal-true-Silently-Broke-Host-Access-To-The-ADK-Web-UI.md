# Issue

- Issue ID: ISSUE-0063
- Title: `agent-net`'s `internal: true` Silently Broke Host Access To The ADK Web UI
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
A local dev session found the ADK web UI (`http://127.0.0.1:8000`) completely unreachable -
`Connection refused`, with no error anywhere in `agent`'s own logs (which showed Uvicorn correctly
`Running on http://0.0.0.0:8000` inside the container). `docker inspect agent`'s
`NetworkSettings.Ports` came back `{"8000/tcp":[]}` - empty - despite `docker-compose.yaml`'s own
`ports: "${AGENT_WEB_BIND_HOST:-127.0.0.1}:${AGENT_WEB_HOST_PORT:-8000}:8000"` entry and a correctly
recorded `HostConfig.PortBindings` for that same container. Recreating the container (even a full
stop/rm/up cycle, ruling out any stale state) did not help.

**Confirmed mechanism.** GH issue #287 put `agent` on `agent-net` only - `internal: true`
(`docker-compose.yaml`'s `networks:` section) - deliberately giving it no route to the internet at
all except through `net-proxy`'s allowlisted forward proxy. A side effect neither that issue nor any
later one caught: **a container with no attachment to any non-internal network can never have a port
published to the host**, because Docker's port-publishing needs a gateway path to forward host
traffic through, and an `internal: true` network has no gateway by definition. `litellm` - on *both*
`external-net` and `agent-net` (for its own direct, unrestricted commercial-provider calls) - showed
the port mapping working correctly (`NetworkSettings.Ports` populated, host could reach it), confirming
this is specific to being attached *only* to the internal network, not a general Docker Desktop fault -
a sanity-check `docker run -p 18080:80 nginx:alpine` with no custom networks also worked immediately.

This is a confirmed, reproducible guardrail/config gap (the egress restriction's own side effect was
never surfaced anywhere - no error, no log line, no comment warning this would happen) - not agent
behavior, not a one-off environment glitch.

## Acceptance Criteria
- The ADK web UI is reachable from the host (`AGENT_WEB_BIND_HOST`/`AGENT_WEB_HOST_PORT`, same env
  vars/defaults as before) without giving `agent` itself a second, egress-capable network interface -
  `agent` must remain `agent-net`-only.
- The existing egress allowlist is provably unaffected: an allowlisted host (`github.com`) still
  succeeds through the proxy, a non-allowlisted host (anything not on the list) still fails, from
  inside `agent`.
- Applies consistently across every compose file with this same `agent`/`net-proxy`/`agent-net`
  shape (`docker-compose.yaml`, `docker-compose.local.yaml`, `docker-compose.local-hostollama.yaml`).

## Notes
- Fix shape: `net-proxy` is already the one container straddling both networks (for egress) - it now
  also reverse-proxies the web UI's ingress (host:8000 -> net-proxy:8000 -> agent:8000 over
  `agent-net`), via a lightweight `socat TCP-LISTEN:8000,fork,reuseaddr TCP:agent:8000` added
  alongside its existing `tinyproxy` process (`config/net-proxy/entrypoint.sh`, new). `agent` itself
  gained no new network attachment and no new egress path - only `net-proxy`'s own already-trusted
  position changed shape (ingress as well as egress).
- An alternative (just adding `external-net` back to `agent`) was considered and rejected: it would
  fix the port, but also hand `agent` a second interface with a real route to the internet, letting
  any tool inside it bypass the `net-proxy` allowlist entirely - reopening exactly what GH issue #287
  closed. Confirmed by direct testing this fix does NOT reopen that gap (see Acceptance Criteria).
- No existing test harness in this repo validates `docker-compose*.yaml` content (the test suite is
  pure Python unit tests over agent logic) and PyYAML isn't a current dependency - verification here
  was live, manual, end-to-end (`curl` through the real published port, `docker exec` into `agent` to
  confirm both the allowlist still holds and `agent`'s own `NetworkSettings.Networks` still excludes
  `external-net`), not an automated regression test.

## Test Approach
- Manual/live verification (performed as part of this fix): `curl -L http://127.0.0.1:$AGENT_WEB_HOST_PORT`
  returns `200` (following the ADK web UI's own redirect); `docker inspect <agent>` shows no
  `external-net` entry in `NetworkSettings.Networks`; `docker exec <agent> curl https://github.com`
  succeeds and `docker exec <agent> curl https://example.com` fails, both through the proxy.
- If a future pass adds YAML-parsing test infrastructure to this repo, the natural regression test is
  static: assert `agent`'s compose service definition has no `ports:` key and no `external-net` network
  membership, and `net-proxy`'s does have the `ports:` mapping, across all three compose files.
