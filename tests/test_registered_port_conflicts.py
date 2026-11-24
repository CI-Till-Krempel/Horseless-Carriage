"""Cross-project host-port conflict detection/repair (lib_docker.
registered_dev_port_claims / repair_registered_port_conflicts) and its
doctor.py wiring.

Before this, ports were only ever checked against what's bound *right now*
(new_project.py at install time, ensure_dev_ports_available at startup), so a
stopped project's ports looked free and a second registered project could be
assigned the same one - surfacing only as a Docker bind failure or a surprise
reassignment once both were started.

project_registry.REGISTRY_PATH is redirected to a tmp file by conftest's
autouse _isolated_project_registry fixture - nothing here touches the real
~/.horseless-carriage/projects.json.
"""

import doctor
import lib_docker
import lib_env
import project_registry

from test_doctor import _patch_proxy_unreachable, valid_repo  # noqa: F401 - valid_repo is a fixture


def _register_project(tmp_path, name, litellm_port=None, web_port=None):
    hc_path = tmp_path / name / "horseless-carriage"
    hc_path.mkdir(parents=True)
    lines = []
    if litellm_port is not None:
        lines.append(f"LITELLM_HOST_PORT='{litellm_port}'")
    if web_port is not None:
        lines.append(f"AGENT_WEB_HOST_PORT='{web_port}'")
    (hc_path / ".env").write_text("\n".join(lines) + "\n")
    project_registry.register(name, hc_path, hc_path.parent)
    return hc_path


class TestFindFreePortExclude:
    def test_skips_excluded_ports_even_if_free(self, monkeypatch):
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)
        assert lib_docker.find_free_port(4001, exclude={4001, 4002}) == 4003


class TestRegisteredPortClaims:
    def test_claims_read_from_each_registered_env_with_defaults(self, tmp_path):
        a = _register_project(tmp_path, "a", litellm_port=4001)
        claims = lib_docker.registered_dev_port_claims()
        assert {(c["name"], c["var"], c["port"]) for c in claims} == {
            ("a", "LITELLM_HOST_PORT", 4001), ("a", "AGENT_WEB_HOST_PORT", 8000),
        }
        assert all(c["hc_path"] == str(a.resolve()) for c in claims)

    def test_extra_hc_path_already_registered_is_not_double_counted(self, tmp_path):
        a = _register_project(tmp_path, "a", 4001, 8001)
        claims = lib_docker.registered_dev_port_claims(extra_hc_paths=[a])
        assert len(claims) == 2
        assert lib_docker.find_registered_port_conflicts(claims) == {}

    def test_stale_registry_entry_without_env_is_ignored(self, tmp_path):
        project_registry.register("gone", tmp_path / "gone" / "horseless-carriage", tmp_path / "gone")
        assert lib_docker.registered_dev_port_claims() == []

    def test_detects_a_port_shared_by_two_projects(self, tmp_path):
        _register_project(tmp_path, "a", 4001, 8001)
        _register_project(tmp_path, "b", 4001, 8002)
        conflicts = lib_docker.find_registered_port_conflicts(lib_docker.registered_dev_port_claims())
        assert list(conflicts) == [4001]
        assert [c["name"] for c in conflicts[4001]] == ["a", "b"]


class TestRepairRegisteredPortConflicts:
    def test_reassigns_the_later_project_and_persists_it(self, tmp_path, monkeypatch):
        a = _register_project(tmp_path, "a", 4001, 8001)
        b = _register_project(tmp_path, "b", 4001, 8002)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: False)
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        report = lib_docker.repair_registered_port_conflicts()

        assert report["unresolved"] == []
        assert [(name, var, old, new) for name, _, var, old, new in report["changes"]] == [
            ("b", "LITELLM_HOST_PORT", 4001, 4002),
        ]
        assert lib_env.read_env_var(a / ".env", "LITELLM_HOST_PORT") == "4001"
        assert lib_env.read_env_var(b / ".env", "LITELLM_HOST_PORT") == "4002"

    def test_never_picks_a_port_another_project_claims(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "a", 4001, 8001)
        _register_project(tmp_path, "b", 4001, 8002)
        _register_project(tmp_path, "c", 4002, 8003)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: False)
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        report = lib_docker.repair_registered_port_conflicts()

        assert [(name, new) for name, _, _, _, new in report["changes"]] == [("b", 4003)]

    def test_never_picks_a_port_that_is_bound(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "a", 4001, 8001)
        b = _register_project(tmp_path, "b", 4001, 8002)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: False)
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: port != 4002)

        lib_docker.repair_registered_port_conflicts()

        assert lib_env.read_env_var(b / ".env", "LITELLM_HOST_PORT") == "4003"

    def test_running_project_keeps_the_port_even_if_registered_later(self, tmp_path, monkeypatch):
        a = _register_project(tmp_path, "a", 4001, 8001)
        b = _register_project(tmp_path, "b", 4001, 8002)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: hc_path == str(b.resolve()))
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        lib_docker.repair_registered_port_conflicts()

        assert lib_env.read_env_var(b / ".env", "LITELLM_HOST_PORT") == "4001"
        assert lib_env.read_env_var(a / ".env", "LITELLM_HOST_PORT") == "4002"

    def test_two_running_claimants_are_reported_not_rewritten(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "a", 4001, 8001)
        b = _register_project(tmp_path, "b", 4001, 8002)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: True)

        report = lib_docker.repair_registered_port_conflicts()

        assert report["changes"] == []
        assert [(name, port) for name, _, _, port, _ in report["unresolved"]] == [("b", 4001)]
        assert lib_env.read_env_var(b / ".env", "LITELLM_HOST_PORT") == "4001"

    def test_dry_run_reports_without_docker_or_writes(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "a", 4001, 8001)
        b = _register_project(tmp_path, "b", 4001, 8002)

        def fail(*a, **k):
            raise AssertionError("dry_run must not query Docker")
        monkeypatch.setattr(lib_docker, "_dev_stack_running", fail)

        report = lib_docker.repair_registered_port_conflicts(dry_run=True)

        assert report["changes"] == []
        assert [name for name, *_ in report["unresolved"]] == ["b"]
        assert lib_env.read_env_var(b / ".env", "LITELLM_HOST_PORT") == "4001"

    def test_no_conflicts_makes_no_docker_calls(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "a", 4001, 8001)
        _register_project(tmp_path, "b", 4002, 8002)

        def fail(*a, **k):
            raise AssertionError("no conflicts - must not query Docker")
        monkeypatch.setattr(lib_docker, "_dev_stack_running", fail)

        assert lib_docker.repair_registered_port_conflicts() == {"changes": [], "unresolved": []}


class TestEnsureDevPortsAvoidsRegisteredClaims:
    def test_reassigns_a_port_claimed_by_a_stopped_registered_project(self, tmp_path, monkeypatch):
        _register_project(tmp_path, "other", 4000, 8000)
        me = tmp_path / "me"
        me.mkdir()
        (me / ".env").write_text("LITELLM_HOST_PORT='4000'\nAGENT_WEB_HOST_PORT='8005'\n")
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args, cwd=None: [])
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        changes = lib_docker.ensure_dev_ports_available(me, [])

        assert changes == [("LITELLM_HOST_PORT", 4000, 4001)]
        assert lib_env.read_env_var(me / ".env", "AGENT_WEB_HOST_PORT") == "8005"

    def test_own_registered_claim_does_not_count_as_a_conflict(self, tmp_path, monkeypatch):
        me = _register_project(tmp_path, "me", 4000, 8000)
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args, cwd=None: [])
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        assert lib_docker.ensure_dev_ports_available(me, []) == []


class TestDoctorPortConflictCheck:
    def test_doctor_repairs_a_conflict_with_this_checkout(self, valid_repo, tmp_path, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        other = _register_project(tmp_path, "other", 4000, 8000)
        monkeypatch.setattr(lib_docker, "_dev_stack_running", lambda hc_path: False)
        monkeypatch.setattr(lib_docker, "is_port_free", lambda port: True)

        doctor.check(valid_repo, skip_llm_probe=True)
        out = capsys.readouterr().out

        # The registered project comes first, so it keeps its ports; the
        # (unregistered) checkout doctor runs in gets new ones.
        assert "FIXED: port 4000" in out and "FIXED: port 8000" in out
        assert lib_env.read_env_var(other / ".env", "LITELLM_HOST_PORT") == "4000"
        assert lib_env.read_env_var(valid_repo / ".env", "LITELLM_HOST_PORT") == "4001"
        assert lib_env.read_env_var(valid_repo / ".env", "AGENT_WEB_HOST_PORT") == "8001"

    def test_doctor_dry_run_only_warns(self, valid_repo, tmp_path, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        _register_project(tmp_path, "other", 4000, 8000)

        result = doctor.check(valid_repo, skip_llm_probe=True, dry_run=True)

        assert any("assigned to more than one" in item.message for item in result.items)
        assert not lib_env.read_env_var(valid_repo / ".env", "LITELLM_HOST_PORT")  # never written


class TestDoctorUnavailableOpenjdkCheck:
    def _write_workbench(self, valid_repo, body):
        hc = valid_repo / "state_repo" / ".hc"
        hc.mkdir(parents=True, exist_ok=True)
        (hc / "workbench-setup.sh").write_text("#!/bin/bash\nset -e\n" + body)

    def test_openjdk_17_is_an_error_with_a_suggested_replacement(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        self._write_workbench(valid_repo, "apt-get install -y openjdk-17-jdk wget unzip\n")

        result = doctor.check(valid_repo, skip_llm_probe=True)

        messages = [i.message for i in result.items if i.severity == "error"]
        assert any("openjdk-17-jdk" in m and "openjdk-21-jdk" in m for m in messages), messages

    def test_openjdk_21_is_fine(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        self._write_workbench(valid_repo, "apt-get install -y openjdk-21-jdk-headless\n")

        result = doctor.check(valid_repo, skip_llm_probe=True)

        assert not any("openjdk" in i.message for i in result.items)

    def test_commented_out_mention_is_ignored(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        self._write_workbench(valid_repo, "# used to be openjdk-17-jdk\napt-get install -y default-jdk\n")

        result = doctor.check(valid_repo, skip_llm_probe=True)

        assert not any("openjdk" in i.message for i in result.items)
