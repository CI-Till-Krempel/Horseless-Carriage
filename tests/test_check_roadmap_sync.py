import json
import subprocess

import check_roadmap_sync


FAKE_ROADMAP = """\
# Product Roadmap

## How to use
- Some instructions.

### v0.1 — Trust & Integrity Fixes (Released)
Stories:
- [x] [EP-0001] Something done already

### v0.3 — Pluggable Development Workbenches (planned)
Stories:
- [ ] [EP-0010] Pluggable Development Workbench Templates
  - [ ] [US-0021] Introduce the mechanism
- [ ] [GH-456] Document supported tech stacks
- [ ] [GH-513] Context window optimizations
- [ ] [GH-515] ADK prompt-cache warning

### v0.5 — Dashboard & Onboarding UX (planned)
Stories:
- [ ] [GH-278] Sticky header

### Research backlog (exploratory, not a committed release)
Stories:
- [ ] [GH-96] Spike: Research custom frontend

### Backlog (unplanned, no GitHub issue yet)
- [ ] [EP-0008] Concurrency-Safe State
  - Related to [GH-286] (v0.4) but not the same scope

---

## Index of story references
- v0.3: EP-0010, US-0021, GH-456, GH-513, GH-515
- v0.5: GH-278
- Research backlog: GH-96
"""


class TestParseRoadmapVersions:
    def test_maps_gh_ids_to_their_version_section(self):
        versions = check_roadmap_sync.parse_roadmap_versions(FAKE_ROADMAP)
        assert versions["GH-456"] == "v0.3"
        assert versions["GH-513"] == "v0.3"
        assert versions["GH-515"] == "v0.3"
        assert versions["GH-278"] == "v0.5"

    def test_does_not_pick_up_research_backlog_or_unplanned_sections(self):
        """Research backlog / Backlog aren't real GitHub milestones (only
        v0.1.0...v0.8.0 exist) - GH-NNN references there shouldn't produce
        a (false) expected-milestone mapping."""
        versions = check_roadmap_sync.parse_roadmap_versions(FAKE_ROADMAP)
        assert "GH-96" not in versions

    def test_ignores_gh_id_mentioned_only_in_prose_not_as_its_own_checklist_entry(self):
        """"- Related to [GH-286] (v0.4) ..." is a sub-bullet note on
        EP-0008's own entry, not GH-286's own checklist line - and it's
        inside the non-versioned Backlog section anyway."""
        versions = check_roadmap_sync.parse_roadmap_versions(FAKE_ROADMAP)
        assert "GH-286" not in versions

    def test_ignores_bare_ids_in_the_index_summary_section(self):
        """The "## Index of story references" list uses bare `GH-456`
        (no brackets, no checkbox) - parse_roadmap_versions reads the
        per-version section bodies, not this summary, so it shouldn't
        matter whether the summary agrees or not."""
        roadmap_without_index = FAKE_ROADMAP.split("## Index of story references")[0]
        versions_full = check_roadmap_sync.parse_roadmap_versions(FAKE_ROADMAP)
        versions_without_index = check_roadmap_sync.parse_roadmap_versions(roadmap_without_index)
        assert versions_full == versions_without_index

    def test_empty_text_returns_empty_mapping(self):
        assert check_roadmap_sync.parse_roadmap_versions("") == {}

    def test_real_roadmap_file_has_gh_513_and_gh_515_under_v0_3(self):
        """Regression guard for the exact drift GH-272 cites as already
        having happened (GH-513/GH-515 carried the v0.3.0 GitHub milestone
        before anyone added them here) - confirms today's real
        specs/ROADMAP.md is actually in the fixed state and that this
        parser reads it the same way a human would."""
        text = check_roadmap_sync.DEFAULT_ROADMAP_PATH.read_text(encoding="utf-8")
        versions = check_roadmap_sync.parse_roadmap_versions(text)
        assert versions.get("GH-513") == "v0.3"
        assert versions.get("GH-515") == "v0.3"
        assert versions.get("GH-456") == "v0.3"


class TestParseRoadmapNonVersionIds:
    def test_research_backlog_and_unplanned_backlog_ids_are_exempt(self):
        exempt = check_roadmap_sync.parse_roadmap_non_version_ids(FAKE_ROADMAP)
        assert "GH-96" in exempt

    def test_real_version_section_ids_are_not_exempt(self):
        exempt = check_roadmap_sync.parse_roadmap_non_version_ids(FAKE_ROADMAP)
        assert "GH-456" not in exempt
        assert "GH-513" not in exempt


class TestVersionToMilestoneTitle:
    def test_two_part_version_gets_patch_zero(self):
        assert check_roadmap_sync.version_to_milestone_title("v0.3") == "v0.3.0"

    def test_one_part_version_gets_minor_and_patch_zero(self):
        assert check_roadmap_sync.version_to_milestone_title("v1") == "v1.0.0"

    def test_three_part_version_is_unchanged(self):
        assert check_roadmap_sync.version_to_milestone_title("v0.3.1") == "v0.3.1"


class TestFetchOpenIssuesWithMilestones:
    def test_parses_gh_json_output(self, monkeypatch):
        payload = [
            {"number": 513, "title": "Context window optimizations", "milestone": {"title": "v0.3.0"}},
            {"number": 999, "title": "No milestone yet", "milestone": None},
        ]

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

        monkeypatch.setattr(check_roadmap_sync.subprocess, "run", fake_run)
        issues = check_roadmap_sync.fetch_open_issues_with_milestones()
        assert issues == [
            {"number": 513, "title": "Context window optimizations", "milestone": "v0.3.0"},
            {"number": 999, "title": "No milestone yet", "milestone": None},
        ]

    def test_passes_repo_flag_through_when_given(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        monkeypatch.setattr(check_roadmap_sync.subprocess, "run", fake_run)
        check_roadmap_sync.fetch_open_issues_with_milestones(repo="CI-Till-Krempel/Horseless-Carriage")
        assert "--repo" in captured["cmd"]
        assert "CI-Till-Krempel/Horseless-Carriage" in captured["cmd"]

    def test_gh_not_found_returns_none(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(check_roadmap_sync.subprocess, "run", fake_run)
        assert check_roadmap_sync.fetch_open_issues_with_milestones() is None

    def test_nonzero_exit_returns_none(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not authenticated")

        monkeypatch.setattr(check_roadmap_sync.subprocess, "run", fake_run)
        assert check_roadmap_sync.fetch_open_issues_with_milestones() is None

    def test_invalid_json_returns_none(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="{not valid json", stderr="")

        monkeypatch.setattr(check_roadmap_sync.subprocess, "run", fake_run)
        assert check_roadmap_sync.fetch_open_issues_with_milestones() is None


class TestComputeDrift:
    def test_matching_milestone_produces_no_drift(self):
        roadmap_versions = {"GH-513": "v0.3"}
        issues = [{"number": 513, "title": "x", "milestone": "v0.3.0"}]
        assert check_roadmap_sync.compute_drift(roadmap_versions, issues) == []

    def test_github_milestone_but_not_in_roadmap_flags_missing_from_roadmap(self):
        roadmap_versions = {}
        issues = [{"number": 513, "title": "Context window", "milestone": "v0.3.0"}]
        drift = check_roadmap_sync.compute_drift(roadmap_versions, issues)
        assert len(drift) == 1
        assert drift[0].kind == "missing_from_roadmap"
        assert drift[0].gh_id == "GH-513"

    def test_roadmap_lists_it_but_github_has_no_milestone_flags_roadmap_only(self):
        roadmap_versions = {"GH-513": "v0.3"}
        issues = [{"number": 513, "title": "Context window", "milestone": None}]
        drift = check_roadmap_sync.compute_drift(roadmap_versions, issues)
        assert len(drift) == 1
        assert drift[0].kind == "roadmap_only"

    def test_disagreeing_milestones_flags_mismatch(self):
        """The GH-272 issue body's own cited example: #456 was milestoned
        v0.3.0 on GitHub but the roadmap listed it under v0.5."""
        roadmap_versions = {"GH-456": "v0.5"}
        issues = [{"number": 456, "title": "Document supported tech stacks", "milestone": "v0.3.0"}]
        drift = check_roadmap_sync.compute_drift(roadmap_versions, issues)
        assert len(drift) == 1
        assert drift[0].kind == "milestone_mismatch"

    def test_issue_with_no_milestone_and_not_in_roadmap_is_not_drift(self):
        roadmap_versions = {}
        issues = [{"number": 42, "title": "Totally unplanned", "milestone": None}]
        assert check_roadmap_sync.compute_drift(roadmap_versions, issues) == []

    def test_closed_issues_never_appear_so_cannot_produce_drift(self):
        """compute_drift only ever iterates the issues list it's given -
        fetch_open_issues_with_milestones only fetches OPEN issues, so an
        already-closed, already-shipped [GH-NNN] still referenced under an
        old Done roadmap section simply never reaches compute_drift at all."""
        roadmap_versions = {"GH-1": "v0.1"}
        assert check_roadmap_sync.compute_drift(roadmap_versions, []) == []

    def test_multiple_issues_report_each_mismatch_independently(self):
        roadmap_versions = {"GH-513": "v0.3", "GH-515": "v0.3"}
        issues = [
            {"number": 513, "title": "a", "milestone": "v0.3.0"},
            {"number": 515, "title": "b", "milestone": "v0.4.0"},
        ]
        drift = check_roadmap_sync.compute_drift(roadmap_versions, issues)
        assert len(drift) == 1
        assert drift[0].gh_id == "GH-515"

    def test_exempt_ids_never_produce_drift_regardless_of_milestone(self):
        """Regression for a real false-positive this script's first draft
        produced: GH-96/97/178 carry the real 'v0.8.0' GitHub milestone but
        are listed under ROADMAP.md's "Research backlog" section, not a
        real version section - there's no single milestone value to call
        "expected" for a research-backlog item, so it must never be
        flagged no matter what GitHub says (missing, present, or anything
        else)."""
        roadmap_versions = {}  # not under any real version section
        exempt_ids = {"GH-96"}
        issues = [{"number": 96, "title": "Spike", "milestone": "v0.8.0"}]
        assert check_roadmap_sync.compute_drift(roadmap_versions, issues, exempt_ids) == []


class TestMain:
    def test_returns_zero_when_gh_unavailable(self, monkeypatch, tmp_path, capsys):
        roadmap_file = tmp_path / "ROADMAP.md"
        roadmap_file.write_text(FAKE_ROADMAP, encoding="utf-8")
        monkeypatch.setattr(check_roadmap_sync, "fetch_open_issues_with_milestones", lambda repo=None: None)
        exit_code = check_roadmap_sync.main(["--roadmap", str(roadmap_file)])
        assert exit_code == 0
        assert "could not fetch" in capsys.readouterr().out

    def test_returns_zero_when_missing_roadmap_file(self, tmp_path):
        missing = tmp_path / "does-not-exist.md"
        assert check_roadmap_sync.main(["--roadmap", str(missing)]) == 0

    def test_returns_zero_and_reports_ok_when_no_drift(self, monkeypatch, tmp_path, capsys):
        roadmap_file = tmp_path / "ROADMAP.md"
        roadmap_file.write_text(FAKE_ROADMAP, encoding="utf-8")
        fake_issues = [{"number": 513, "title": "x", "milestone": "v0.3.0"}]
        monkeypatch.setattr(check_roadmap_sync, "fetch_open_issues_with_milestones", lambda repo=None: fake_issues)
        exit_code = check_roadmap_sync.main(["--roadmap", str(roadmap_file)])
        assert exit_code == 0
        assert "OK" in capsys.readouterr().out

    def test_returns_one_and_prints_drift_when_mismatched(self, monkeypatch, tmp_path, capsys):
        roadmap_file = tmp_path / "ROADMAP.md"
        roadmap_file.write_text(FAKE_ROADMAP, encoding="utf-8")
        # FAKE_ROADMAP lists GH-278 under v0.5 (expects milestone "v0.5.0");
        # give it a disagreeing real-GitHub milestone to force a mismatch.
        fake_issues = [{"number": 278, "title": "Sticky header", "milestone": "v0.3.0"}]
        monkeypatch.setattr(check_roadmap_sync, "fetch_open_issues_with_milestones", lambda repo=None: fake_issues)
        exit_code = check_roadmap_sync.main(["--roadmap", str(roadmap_file)])
        assert exit_code == 1
        out = capsys.readouterr().out
        assert "milestone_mismatch" in out
        assert "GH-278" in out
