import banner


class TestVersion:
    def test_reads_version_file(self):
        assert banner.version() != "unknown"

    def test_missing_file_returns_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(banner.Path, "__file__", str(tmp_path / "banner.py"), raising=False)
        monkeypatch.setattr(banner, "__file__", str(tmp_path / "banner.py"))
        assert banner.version() == "unknown"


class TestPrintBanner:
    def test_noop_when_not_a_tty(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        banner.print_banner()
        assert capsys.readouterr().out == ""

    def test_prints_when_a_tty(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        banner.print_banner()
        out = capsys.readouterr().out
        assert "====" in out
        assert banner.version() in out
