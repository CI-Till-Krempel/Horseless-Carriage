# GH issue #287: forward-proxy sidecar the `agent` container is routed
# through for all outbound HTTPS - enforces the github.com/api.github.com
# allowlist at the network layer, since the agent container itself sits on
# an internal-only Compose network with no direct route to the internet.
# Alpine + tinyproxy (not a third-party prebuilt image) so the allowlist
# config is the only thing this repo doesn't fully control the provenance of.
FROM alpine:3.20

RUN apk add --no-cache tinyproxy

COPY config/net-proxy/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY config/net-proxy/allowlist.filter /etc/tinyproxy/allowlist.filter

EXPOSE 8888

CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
