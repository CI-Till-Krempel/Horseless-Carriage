# GH issue #287: forward-proxy sidecar the `agent` container is routed
# through for all outbound HTTPS - enforces the github.com/api.github.com
# allowlist at the network layer, since the agent container itself sits on
# an internal-only Compose network with no direct route to the internet.
# Alpine + tinyproxy (not a third-party prebuilt image) so the allowlist
# config is the only thing this repo doesn't fully control the provenance of.
#
# GH issue #287 follow-up: also reverse-proxies the ADK web UI's ingress
# (agent's port 8000) - see entrypoint.sh for why this has to live here
# rather than as a second port/network on `agent` itself. socat is a tiny,
# single-purpose addition (plain TCP forwarding, no HTTP-level logic) kept
# in this already-minimal, repo-controlled image rather than reaching for a
# third-party reverse-proxy image.
FROM alpine:3.20

RUN apk add --no-cache tinyproxy socat

COPY config/net-proxy/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY config/net-proxy/allowlist.filter /etc/tinyproxy/allowlist.filter
COPY config/net-proxy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8888 8000

CMD ["/entrypoint.sh"]
