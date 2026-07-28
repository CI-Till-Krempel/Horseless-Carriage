import rebuild_images


class TestParseArgs:
    def test_default_is_no_no_cache(self):
        assert rebuild_images.parse_args([]) is False

    def test_no_cache_flag(self):
        assert rebuild_images.parse_args(["--no-cache"]) is True


class TestImagesToRebuild:
    """
    Acceptance Criteria: `ollama` is only defined as a buildable service in
    docker-compose.local.yaml (see docker-compose.yaml vs.
    docker-compose.local.yaml) - rebuilding it for a cloud-provider setup
    would just fail with "no build info found".
    """

    def test_cloud_setup_only_rebuilds_agent(self):
        assert rebuild_images.images_to_rebuild([]) == ["agent"]

    def test_local_setup_also_rebuilds_ollama(self):
        assert rebuild_images.images_to_rebuild(["-f", "docker-compose.local.yaml"]) == ["agent", "ollama"]


class TestMain:
    def test_builds_expected_docker_command_for_cloud_setup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rebuild_images.shutil, "which", lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(rebuild_images.run, "compose_file_args", lambda _root: [])
        monkeypatch.setattr(rebuild_images.os, "chdir", lambda _path: None)
        monkeypatch.setattr(rebuild_images.sys, "argv", ["rebuild_images.py"])

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return rebuild_images.subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(rebuild_images.subprocess, "run", fake_run)

        rebuild_images.main()

        assert captured["cmd"] == ["docker", "compose", "build", "--pull", "agent"]

    def test_builds_expected_docker_command_for_local_setup_with_no_cache(self, monkeypatch):
        monkeypatch.setattr(rebuild_images.shutil, "which", lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(
            rebuild_images.run, "compose_file_args", lambda _root: ["-f", "docker-compose.local.yaml"]
        )
        monkeypatch.setattr(rebuild_images.os, "chdir", lambda _path: None)
        monkeypatch.setattr(rebuild_images.sys, "argv", ["rebuild_images.py", "--no-cache"])

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return rebuild_images.subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(rebuild_images.subprocess, "run", fake_run)

        rebuild_images.main()

        assert captured["cmd"] == [
            "docker", "compose", "-f", "docker-compose.local.yaml",
            "build", "--pull", "--no-cache", "agent", "ollama",
        ]

    def test_missing_docker_exits_with_error(self, monkeypatch, capsys):
        monkeypatch.setattr(rebuild_images.shutil, "which", lambda _name: None)
        monkeypatch.setattr(rebuild_images.os, "chdir", lambda _path: None)
        monkeypatch.setattr(rebuild_images.sys, "argv", ["rebuild_images.py"])

        try:
            rebuild_images.main()
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code == 1
        assert "docker" in capsys.readouterr().out.lower()

    def test_nonzero_build_exit_code_propagates(self, monkeypatch):
        monkeypatch.setattr(rebuild_images.shutil, "which", lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(rebuild_images.run, "compose_file_args", lambda _root: [])
        monkeypatch.setattr(rebuild_images.os, "chdir", lambda _path: None)
        monkeypatch.setattr(rebuild_images.sys, "argv", ["rebuild_images.py"])
        monkeypatch.setattr(
            rebuild_images.subprocess, "run",
            lambda cmd, **k: rebuild_images.subprocess.CompletedProcess(cmd, 1),
        )

        try:
            rebuild_images.main()
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code == 1
