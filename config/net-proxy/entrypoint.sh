#!/bin/sh
set -e

ALLOWLIST_PATH="/etc/tinyproxy/allowlist.filter"
ALLOWLIST_DEFAULT_PATH="/etc/tinyproxy/allowlist.filter.default"

# Regenerate the live allowlist from the repo-tracked baseline on every
# container start, then append any locally-configured extra hosts from
# NET_PROXY_EXTRA_ALLOWLIST_HOSTS (a comma-separated hostname list from
# .env - gitignored, local-only - see SECURITY.md) - this is what lets a
# project add the hosts its own workbench needs (e.g. a propose_framework_
# change proposal) without ever hand-editing the tracked allowlist.filter,
# so `git pull`-ing a new Horseless Carriage version never conflicts with a
# local addition. Regenerating from ALLOWLIST_DEFAULT_PATH each start
# (rather than appending in place) keeps this idempotent across container
# restarts - skipped entirely if ALLOWLIST_PATH already exists, which means
# something else provided it (docker-compose.eval.yml bind-mounts its own
# curated allowlist-eval.filter there read-only for CI - that file is
# fully self-contained and must be left untouched, not merged with this).
if [ ! -e "$ALLOWLIST_PATH" ]; then
  cp "$ALLOWLIST_DEFAULT_PATH" "$ALLOWLIST_PATH"
  if [ -n "$NET_PROXY_EXTRA_ALLOWLIST_HOSTS" ]; then
    {
      echo ""
      echo "# Appended at container start from NET_PROXY_EXTRA_ALLOWLIST_HOSTS (.env, local-only)"
      echo "$NET_PROXY_EXTRA_ALLOWLIST_HOSTS" | tr ',' '\n' | while IFS= read -r host; do
        host=$(echo "$host" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [ -n "$host" ] && printf '^%s(:[0-9]+)?$\n' "$(echo "$host" | sed 's/\./\\./g')"
      done
    } >> "$ALLOWLIST_PATH"
  fi
fi

# GH issue #287 follow-up: `agent` sits on agent-net only (internal: true -
# no gateway, no route to anything outside Docker's own bridge), which was
# the whole point - but it also means Docker can never publish a port to
# the host for that container, since port-publishing needs a gateway path
# to forward through. A real local dev session hit exactly this: the ADK
# web UI (port 8000) was completely unreachable, with no error anywhere -
# `docker inspect` showed the port mapping configured, just never actually
# bound. net-proxy is already the one container straddling both networks
# (for egress); it reverse-proxies the web UI's ingress too now, instead of
# `agent` gaining a second, egress-capable network interface itself (which
# would let anything inside it bypass the allowlist below entirely).
#
# socat forks a new process per connection (not a shared/long-lived
# tunnel), so one client's connection never blocks another's, and a crashed
# `agent` just means new connections fail individually rather than killing
# this whole container.
socat TCP-LISTEN:8000,fork,reuseaddr TCP:agent:8000 &

exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf
