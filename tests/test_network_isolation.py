"""
Regression test for GH issue #287: the `agent` container must have no route
to the internet except through the `net-proxy` sidecar's allowlist
(github.com/api.github.com, for git+gh tooling) - `agent-net` is marked
`internal: true` specifically so this is a Docker-level network guarantee,
not just an application-level convention a future change could silently
route around (e.g. by adding `agent` back onto `external-net`, or dropping
`internal: true`).
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

_COMPOSE_FILES = ("docker-compose.yaml", "docker-compose.local.yaml", "docker-compose.local-hostollama.yaml")


def _load(compose_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / compose_path).read_text(encoding="utf-8"))


class TestAgentNetworkIsolation:
    def test_agent_net_is_internal_in_every_compose_file(self):
        for compose_file in _COMPOSE_FILES:
            data = _load(compose_file)
            agent_net = data["networks"]["agent-net"]
            assert agent_net.get("internal") is True, (
                f"{compose_file}'s agent-net must be internal: true - that's the actual mechanism "
                "that gives agent no route to the internet at all (GH issue #287), not just an "
                "application-level convention."
            )

    def test_agent_service_is_only_on_agent_net(self):
        for compose_file in _COMPOSE_FILES:
            data = _load(compose_file)
            networks = set(data["services"]["agent"]["networks"])
            assert networks == {"agent-net"}, (
                f"{compose_file}'s agent service must be on agent-net only - being on external-net "
                "too would give it a direct route out, bypassing net-proxy's allowlist entirely "
                f"(GH issue #287). Found: {sorted(networks)}"
            )

    def test_litellm_is_reachable_by_agent_and_keeps_its_own_unrestricted_egress(self):
        """litellm needs both: agent-net so `agent` can reach it directly
        (never through net-proxy - it's not git/gh tooling), and
        external-net for its own, already-scoped calls to commercial LLM
        providers - a legitimate egress path distinct from agent's
        arbitrary shell/tool access."""
        for compose_file in _COMPOSE_FILES:
            data = _load(compose_file)
            networks = set(data["services"]["litellm"]["networks"])
            assert networks == {"external-net", "agent-net"}, (
                f"{compose_file}'s litellm service networks: {sorted(networks)}"
            )

    def test_net_proxy_service_exists_and_bridges_both_networks(self):
        for compose_file in _COMPOSE_FILES:
            data = _load(compose_file)
            net_proxy = data["services"]["net-proxy"]
            assert net_proxy["build"]["dockerfile"] == "net-proxy.Dockerfile"
            networks = set(net_proxy["networks"])
            assert networks == {"external-net", "agent-net"}, (
                f"{compose_file}'s net-proxy service must be the only bridge between the two "
                f"networks. Found: {sorted(networks)}"
            )

    def test_agent_has_proxy_env_vars_pointing_at_net_proxy(self):
        for compose_file in _COMPOSE_FILES:
            data = _load(compose_file)
            entries = data["services"]["agent"]["environment"]
            env = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in entries}
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                assert env.get(var) == "http://net-proxy:8888", f"{compose_file}: {var}={env.get(var)!r}"
            for var in ("NO_PROXY", "no_proxy"):
                assert "litellm" in env.get(var, ""), (
                    f"{compose_file}: {var} must exclude litellm, or agent's own LiteLLM calls "
                    "would be routed through net-proxy too (GH issue #287)"
                )


class TestNetProxyAllowlist:
    def test_allowlist_only_permits_github_hosts(self):
        """Deliberately narrow scope per GH issue #287's own decision -
        github.com/api.github.com only, nothing broader. A future addition
        should be a conscious edit here, not silent scope creep."""
        text = (REPO_ROOT / "config" / "net-proxy" / "allowlist.filter").read_text(encoding="utf-8")
        patterns = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        assert patterns == [
            r"^github\.com(:[0-9]+)?$",
            r"^api\.github\.com(:[0-9]+)?$",
        ]

    def test_tinyproxy_conf_references_the_allowlist_and_denies_by_default(self):
        text = (REPO_ROOT / "config" / "net-proxy" / "tinyproxy.conf").read_text(encoding="utf-8")
        assert "FilterDefaultDeny Yes" in text
        assert "allowlist.filter" in text
