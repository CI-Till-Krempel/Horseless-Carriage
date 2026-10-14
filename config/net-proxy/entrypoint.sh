#!/bin/sh
set -e

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
