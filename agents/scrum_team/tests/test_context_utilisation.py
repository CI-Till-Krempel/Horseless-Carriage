"""
GH issue #566 / US-0034: runtime context-window utilisation KPI - per LLM
call prompt tokens vs. the role's model's context window, aggregated per
sprint and role, surfaced in calculate_kpis(), the sprint report, the eval
report and the dashboard. Observability only - must never touch budget
enforcement state.
"""
import unittest
from unittest.mock import MagicMock, patch

import agents.scrum_team.agent as agent_module
import agents.scrum_team.tools.quality as quality
from agents.scrum_team import context_utilisation as cu
from agents.scrum_team.agent import track_context_utilisation_callback
from agents.scrum_team.state import ScrumState
from agents.scrum_team.tools.budget import create_sprint_report, render_fallback_sprint_report
from agents.scrum_team.tools.scrum import REPO_STATE_KEYS


def _record(ctx, sprint, role, prompt_tokens, window=100_000, source="litellm_proxy", threshold=80.0):
    return cu.record_call(ctx, sprint, role, f"scrum-{role.lower()}", prompt_tokens, window, source, threshold)


class TestRecordAndSummarize(unittest.TestCase):
    def test_mean_peak_and_high_water_per_role_and_team(self):
        ctx = {}
        ctx = _record(ctx, 1, "DevTeam", 10_000)   # 10%
        ctx = _record(ctx, 1, "DevTeam", 85_000)   # 85% -> high-water
        ctx = _record(ctx, 1, "QA", 30_000)        # 30%

        summary = cu.summarize(ctx, 1)

        self.assertTrue(summary["available"])
        dev = summary["agents"]["DevTeam"]
        self.assertEqual(dev["calls"], 2)
        self.assertEqual(dev["mean_percent"], 47.5)
        self.assertEqual(dev["peak_percent"], 85.0)
        self.assertEqual(dev["peak_prompt_tokens"], 85_000)
        self.assertEqual(dev["mean_prompt_tokens"], 47_500)
        self.assertEqual(dev["high_water_calls"], 1)
        self.assertEqual(summary["agents"]["QA"]["high_water_calls"], 0)
        team = summary["team"]
        self.assertEqual(team["calls"], 3)
        self.assertEqual(team["mean_percent"], round((10 + 85 + 30) / 3, 2))
        self.assertEqual(team["peak_percent"], 85.0)
        self.assertEqual(team["peak_role"], "DevTeam")
        self.assertEqual(team["high_water_calls"], 1)
        self.assertEqual(summary["flagged_roles"], ["DevTeam"])

    def test_sprints_are_kept_separate(self):
        ctx = _record({}, 1, "DevTeam", 90_000)
        ctx = _record(ctx, 2, "DevTeam", 10_000)
        self.assertEqual(cu.summarize(ctx, 1)["team"]["peak_percent"], 90.0)
        self.assertEqual(cu.summarize(ctx, 2)["team"]["peak_percent"], 10.0)
        self.assertEqual(cu.summarize(ctx, 2)["flagged_roles"], [])

    def test_no_data_is_reported_unavailable_not_zero(self):
        for ctx in (None, {}, {"sprints": {}}, _record({}, 1, "QA", 5_000)):
            summary = cu.summarize(ctx, 7)
            self.assertFalse(summary["available"])
            self.assertEqual(summary["team"], {})
            self.assertIn("note", summary)

    def test_record_call_does_not_mutate_input(self):
        original = _record({}, 1, "QA", 5_000)
        snapshot = repr(original)
        _record(original, 1, "QA", 50_000)
        self.assertEqual(repr(original), snapshot)

    def test_non_positive_tokens_or_window_is_a_noop(self):
        self.assertEqual(_record({}, 1, "QA", 0), {})
        self.assertEqual(_record({}, 1, "QA", 100, window=0), {})

    def test_old_sprints_are_pruned(self):
        ctx = {}
        for sprint in range(1, cu.MAX_SPRINTS_RETAINED + 4):
            ctx = _record(ctx, sprint, "QA", 1_000)
        self.assertEqual(len(ctx["sprints"]), cu.MAX_SPRINTS_RETAINED)
        self.assertNotIn("1", ctx["sprints"])
        self.assertIn(str(cu.MAX_SPRINTS_RETAINED + 3), ctx["sprints"])

    def test_fallback_window_is_flagged_as_estimated(self):
        ctx = _record({}, 1, "Architect", 1_000, source=cu.FALLBACK_SOURCE)
        self.assertEqual(cu.summarize(ctx, 1)["estimated_window_roles"], ["Architect"])

    def test_threshold_env_var_and_invalid_values(self):
        with patch.dict("os.environ", {"CONTEXT_UTILISATION_HIGH_WATER_PERCENT": "50"}):
            self.assertEqual(cu.high_water_percent(), 50.0)
            ctx = cu.record_call({}, 1, "QA", "m", 60, 100, "override")
            self.assertEqual(cu.summarize(ctx, 1)["team"]["high_water_calls"], 1)
        for bad in ("abc", "0", "-5", "150"):
            with patch.dict("os.environ", {"CONTEXT_UTILISATION_HIGH_WATER_PERCENT": bad}):
                self.assertEqual(cu.high_water_percent(), cu.DEFAULT_HIGH_WATER_PERCENT)

    def test_parse_overrides_skips_malformed_entries(self):
        self.assertEqual(
            cu.parse_context_window_overrides("scrum-dev=32768, scrum-po = 1048576,bogus,x=abc,y=-1"),
            {"scrum-dev": 32768, "scrum-po": 1048576},
        )

    def test_fallback_tokens_env(self):
        with patch.dict("os.environ", {"CONTEXT_WINDOW_FALLBACK_TOKENS": "32000"}):
            self.assertEqual(cu.fallback_context_window_tokens(), 32000)
        with patch.dict("os.environ", {"CONTEXT_WINDOW_FALLBACK_TOKENS": "nope"}):
            self.assertEqual(cu.fallback_context_window_tokens(), cu.DEFAULT_FALLBACK_CONTEXT_WINDOW_TOKENS)


class TestRenderReportSection(unittest.TestCase):
    def setUp(self):
        ctx = _record({}, 3, "DevTeam", 85_000)
        ctx = _record(ctx, 3, "QA", 20_000)
        self.summary = cu.summarize(ctx, 3)

    def test_full_detail_includes_per_agent_breakdown_and_advisory(self):
        text, omitted = cu.render_report_section(self.summary, "full")
        self.assertIn("### Context Utilisation", text)
        self.assertIn("mean 52.5% / peak 85.0%", text)
        self.assertIn("Context pressure: DevTeam", text)
        self.assertIn("#### Per-Agent Context Utilisation", text)
        self.assertIn("QA (scrum-qa, 100,000-token window, litellm_proxy)", text)
        self.assertEqual(omitted, [])

    def test_lower_detail_keeps_headline_but_omits_breakdown(self):
        text, omitted = cu.render_report_section(self.summary, "executive")
        self.assertIn("peak 85.0%", text)
        self.assertNotIn("Per-Agent Context Utilisation", text)
        self.assertEqual(omitted, ["Per-Agent Context Utilisation"])

    def test_unavailable(self):
        text, omitted = cu.render_report_section(cu.summarize(None, 1), "full")
        self.assertIn("Not available", text)
        self.assertEqual(omitted, [])

    def test_short_label(self):
        self.assertEqual(cu.short_label(self.summary), "ctx mean 52.5% / peak 85.0% (1 ≥80%)")
        self.assertEqual(cu.short_label(cu.summarize(None, 1)), "")


class TestResolveContextWindow(unittest.TestCase):
    def setUp(self):
        quality._context_window_cache.update({"fetched_at": None, "windows": {}})

    def tearDown(self):
        quality._context_window_cache.update({"fetched_at": None, "windows": {}})

    def test_override_wins(self):
        with patch.dict("os.environ", {"CONTEXT_WINDOW_OVERRIDES": "scrum-dev=32768"}), \
             patch.object(quality, "_fetch_model_context_windows",
                          return_value={"scrum-dev": {"real_model": "x", "max_input_tokens": 1_000_000}}):
            self.assertEqual(quality.resolve_context_window("scrum-dev"), (32768, "override"))

    def test_proxy_model_info_then_cached(self):
        with patch.dict("os.environ", {"CONTEXT_WINDOW_OVERRIDES": ""}), \
             patch.object(quality, "_fetch_model_context_windows",
                          return_value={"scrum-dev": {"real_model": "x", "max_input_tokens": 1_000_000}}) as fetch:
            self.assertEqual(quality.resolve_context_window("scrum-dev"), (1_000_000, "litellm_proxy"))
            quality.resolve_context_window("scrum-dev")
            self.assertEqual(fetch.call_count, 1)

    def test_litellm_model_map_then_fallback(self):
        with patch.dict("os.environ", {"CONTEXT_WINDOW_OVERRIDES": "", "CONTEXT_WINDOW_FALLBACK_TOKENS": ""}), \
             patch.object(quality, "_fetch_model_context_windows", return_value={}):
            with patch.object(quality.litellm, "get_model_info", return_value={"max_input_tokens": 200_000}):
                self.assertEqual(quality.resolve_context_window("gpt-x"), (200_000, "litellm_model_map"))
            with patch.object(quality.litellm, "get_model_info", side_effect=Exception("not mapped")):
                self.assertEqual(
                    quality.resolve_context_window("scrum-unknown"),
                    (cu.DEFAULT_FALLBACK_CONTEXT_WINDOW_TOKENS, cu.FALLBACK_SOURCE),
                )


class TestTrackContextUtilisationCallback(unittest.TestCase):
    def _context(self, agent_name="DevTeam", sprint_number=2):
        ctx = MagicMock()
        ctx.agent_name = agent_name
        ctx.state = ScrumState(sprint_number=sprint_number).model_dump()
        return ctx

    def _response(self, prompt_tokens, total=None):
        response = MagicMock()
        response.usage_metadata.prompt_token_count = prompt_tokens
        response.usage_metadata.total_token_count = total if total is not None else prompt_tokens + 10
        return response

    def test_records_prompt_tokens_against_resolved_window(self):
        ctx = self._context()
        with patch.object(agent_module, "resolve_context_window", return_value=(100_000, "override")) as resolve:
            result = track_context_utilisation_callback(ctx, self._response(40_000))
        self.assertIsNone(result)
        resolve.assert_called_once_with(quality._model_alias_for_role("DevTeam"))
        summary = cu.summarize(ctx.state["context_utilisation"], 2)
        self.assertEqual(summary["agents"]["DevTeam"]["peak_percent"], 40.0)
        self.assertEqual(summary["agents"]["DevTeam"]["window_source"], "override")

    def test_never_touches_budget_state(self):
        ctx = self._context()
        before_usage = dict(ctx.state["token_usage"])
        before_budgets = dict(ctx.state["budgets"])
        with patch.object(agent_module, "resolve_context_window", return_value=(100_000, "override")):
            track_context_utilisation_callback(ctx, self._response(95_000))
        self.assertEqual(ctx.state["token_usage"], before_usage)
        self.assertEqual(ctx.state["budgets"], before_budgets)

    def test_no_usage_metadata_or_non_numeric_is_a_noop(self):
        ctx = self._context()
        response = MagicMock()
        response.usage_metadata = None
        self.assertIsNone(track_context_utilisation_callback(ctx, response))
        self.assertEqual(ctx.state["context_utilisation"], {})
        # A bare MagicMock usage_metadata (as many other tests build it) must not crash or record.
        self.assertIsNone(track_context_utilisation_callback(ctx, MagicMock()))
        self.assertEqual(ctx.state["context_utilisation"], {})

    def test_errors_are_swallowed(self):
        ctx = self._context()
        with patch.object(agent_module, "resolve_context_window", side_effect=RuntimeError("boom")):
            self.assertIsNone(track_context_utilisation_callback(ctx, self._response(1_000)))

    def test_registered_on_every_agent_after_update_token_usage(self):
        for agent in (agent_module.root_agent, agent_module.product_owner, agent_module.scrum_master,
                      agent_module.dev_team, agent_module.qa_agent, agent_module.architect):
            callbacks = agent.after_model_callback
            self.assertIn(track_context_utilisation_callback, callbacks, agent.name)
            self.assertGreater(
                callbacks.index(track_context_utilisation_callback),
                callbacks.index(agent_module.update_token_usage_callback),
                agent.name,
            )


class TestStateAndKpiIntegration(unittest.TestCase):
    def test_state_field_is_additive_and_persisted(self):
        self.assertEqual(ScrumState().context_utilisation, {})
        # An older state.json without the key still validates.
        self.assertEqual(ScrumState(**{"sprint_number": 3}).context_utilisation, {})
        self.assertIn("context_utilisation", REPO_STATE_KEYS)

    def test_calculate_context_utilisation_reads_current_sprint(self):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 4, "context_utilisation": _record({}, 4, "QA", 50_000)}
        summary = quality.calculate_context_utilisation(tool_context)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["team"]["peak_percent"], 50.0)
        self.assertFalse(quality.calculate_context_utilisation(None)["available"])

    @patch("agents.scrum_team.tools.quality.calculate_prompt_module_breakdown", return_value={})
    @patch("agents.scrum_team.tools.quality.calculate_prompt_context_usage", return_value={})
    @patch("agents.scrum_team.tools.quality._compute_say_do_ratio", return_value={"say_do_ratio": None, "note": "n"})
    @patch("agents.scrum_team.tools.quality._scan_security_vulnerabilities",
           return_value={"available": False, "vulnerability_scan_results": None, "note": "n"})
    @patch("agents.scrum_team.tools.quality._compute_code_complexity",
           return_value={"code_complexity": None, "available": False, "note": "n"})
    @patch("agents.scrum_team.tools.quality._execute_test_suite_coverage",
           return_value={"test_coverage": None, "coverage_available": False, "tests_available": False,
                         "tests_run": 0, "tests_failed": 0, "note": "n"})
    def test_calculate_kpis_includes_context_utilisation(self, *_mocks):
        tool_context = MagicMock()
        tool_context.state = {"sprint_number": 1, "context_utilisation": _record({}, 1, "DevTeam", 90_000)}
        kpis = quality.calculate_kpis(tool_context)
        self.assertEqual(kpis["context_utilisation"]["flagged_roles"], ["DevTeam"])

    @patch("os.getenv")
    @patch("agents.scrum_team.tools.docs.write_file")
    def test_create_sprint_report_renders_context_utilisation(self, mock_write_file, mock_getenv):
        mock_getenv.return_value = "15.0"
        tool_context = MagicMock()
        tool_context.state = ScrumState(sprint_number=2).model_dump()
        tool_context.state["retro_actions"] = [{"action": "test", "owner": "SM", "status": "open"}]
        tool_context.state["kpi_update_count"] = 1
        tool_context.state["context_utilisation"] = _record({}, 2, "DevTeam", 85_000)

        report = create_sprint_report("summary", ["accomplishment"], tool_context=tool_context)["report"]

        self.assertIn("### Context Utilisation", report)
        self.assertIn("peak 85.0%", report)
        self.assertIn("Context pressure: DevTeam", report)

    @patch("agents.scrum_team.tools.docs.write_file")
    def test_fallback_sprint_report_renders_context_utilisation(self, mock_write_file):
        tool_context = MagicMock()
        tool_context.state = ScrumState(sprint_number=2).model_dump()
        tool_context.state["context_utilisation"] = _record({}, 2, "QA", 30_000)

        result = render_fallback_sprint_report(tool_context=tool_context)

        self.assertIn("### Context Utilisation", result.get("report", ""))
        self.assertIn("peak 30.0%", result.get("report", ""))


if __name__ == "__main__":
    unittest.main()
