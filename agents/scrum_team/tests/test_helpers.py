# agents/scrum_team/tests/test_helpers.py
import unittest
from unittest.mock import patch

from agents.scrum_team.helpers import (
    get_interaction_level,
    required_pre_implementation_approval,
    required_pre_release_approval,
    report_detail_level,
    get_env_with_deprecated_fallback,
    infer_blocker_category,
    should_escalate_blocker_to_user,
    ready_backlog_shortfall,
    closeout_grace_percent,
    closeout_remaining_work_fraction,
    looks_like_role_behavior_finding,
    recurring_technical_finding_sprint,
    current_sprint_phase,
    SPRINT_PHASES,
    SPRINT_PHASE_GUIDANCE,
)
from agents.scrum_team.state import ScrumState


class TestInteractionLevel(unittest.TestCase):
    """Acceptance Criteria: INTERACTION_LEVEL is configurable and drives which
    human-approval gates are required - see docs/INTERACTION-LEVELS.md."""

    def test_defaults_to_product_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_interaction_level(), "Product")

    def test_defaults_to_product_when_unrecognized(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Manager"}, clear=True):
            self.assertEqual(get_interaction_level(), "Product")

    def test_case_insensitive(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "ceo"}, clear=True):
            self.assertEqual(get_interaction_level(), "CEO")
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "eval"}, clear=True):
            self.assertEqual(get_interaction_level(), "EVAL")

    def test_required_pre_implementation_approval_by_level(self):
        self.assertEqual(required_pre_implementation_approval("Product"), "sprint")
        self.assertEqual(required_pre_implementation_approval("CEO"), "budget")
        self.assertIsNone(required_pre_implementation_approval("EVAL"))

    def test_required_pre_release_approval_by_level(self):
        self.assertEqual(required_pre_release_approval("Product"), "release")
        self.assertIsNone(required_pre_release_approval("CEO"))
        self.assertIsNone(required_pre_release_approval("EVAL"))

    def test_reads_from_environment_when_level_not_given(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            self.assertEqual(required_pre_implementation_approval(), "budget")
            self.assertIsNone(required_pre_release_approval())

    def test_report_detail_level_by_level(self):
        self.assertEqual(report_detail_level("Product"), "full")
        self.assertEqual(report_detail_level("CEO"), "executive")
        self.assertEqual(report_detail_level("EVAL"), "full")


class TestBlockerRouting(unittest.TestCase):
    """Acceptance Criteria: a BLOCKED story's category decides who's asked -
    Architect for technical, Product Owner for product (or the human User
    directly at the Product interaction level) - see raise_story_blocker/
    resolve_story_blocker (agents/scrum_team/tools/requirements.py)."""

    def test_infer_blocker_category_technical_roles(self):
        for role in ("DevTeam", "Architect", "QA"):
            with self.subTest(role=role):
                self.assertEqual(infer_blocker_category(role), "technical")

    def test_infer_blocker_category_defaults_to_product(self):
        self.assertEqual(infer_blocker_category("ProductOwner"), "product")
        self.assertEqual(infer_blocker_category("ScrumMaster"), "product")
        self.assertEqual(infer_blocker_category("SomeUnknownRole"), "product")

    def test_infer_blocker_category_technical_if_any_agent_is_technical(self):
        """A transfer-loop pair has two agents - if either one is a
        technical role, the pair's stuck question is technical."""
        self.assertEqual(infer_blocker_category("ProductOwner", "Architect"), "technical")
        self.assertEqual(infer_blocker_category("ProductOwner", "ScrumMaster"), "product")

    def test_should_escalate_blocker_to_user_only_product_category_at_product_level(self):
        self.assertTrue(should_escalate_blocker_to_user("product", "Product"))
        self.assertFalse(should_escalate_blocker_to_user("technical", "Product"))

    def test_should_escalate_blocker_to_user_false_at_other_levels(self):
        for level in ("CEO", "EVAL"):
            with self.subTest(level=level):
                self.assertFalse(should_escalate_blocker_to_user("product", level))

    def test_should_escalate_blocker_to_user_reads_from_environment_when_level_not_given(self):
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "Product"}, clear=True):
            self.assertTrue(should_escalate_blocker_to_user("product"))
        with patch.dict("os.environ", {"INTERACTION_LEVEL": "CEO"}, clear=True):
            self.assertFalse(should_escalate_blocker_to_user("product"))


class TestGetEnvWithDeprecatedFallback(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #81): renaming a budget env var
    (SPRINT_USD_BUDGET -> TOTAL_USD_BUDGET) must never silently drop an
    existing .env's configured value in favor of a hardcoded default - that
    could mean a *higher*, unintended ceiling and unexpected cloud costs.
    """

    def setUp(self):
        import agents.scrum_team.helpers as helpers_module
        self._helpers_module = helpers_module
        helpers_module._deprecated_env_vars_warned.clear()

    def test_prefers_new_name_when_set(self):
        with patch.dict("os.environ", {"NEW_NAME": "5.0", "OLD_NAME": "1.0"}, clear=True):
            self.assertEqual(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"), "5.0")

    def test_falls_back_to_old_name_when_new_unset(self):
        with patch.dict("os.environ", {"OLD_NAME": "1.0"}, clear=True):
            self.assertEqual(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"), "1.0")

    def test_returns_none_when_neither_set(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME"))

    def test_warns_once_per_process_when_falling_back(self):
        """The deprecation warning fires at most once for the same old_name,
        even across repeated calls - check_cost_budget_callback runs this on
        every single turn, so a per-call warning would spam stderr."""
        warnings = []
        with patch.dict("os.environ", {"OLD_NAME": "1.0"}, clear=True):
            with patch("builtins.print", side_effect=lambda *a, **k: warnings.append(a)):
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
        self.assertEqual(len(warnings), 1)

    def test_no_warning_when_new_name_is_used(self):
        warnings = []
        with patch.dict("os.environ", {"NEW_NAME": "5.0"}, clear=True):
            with patch("builtins.print", side_effect=lambda *a, **k: warnings.append(a)):
                get_env_with_deprecated_fallback("NEW_NAME", "OLD_NAME")
        self.assertEqual(len(warnings), 0)


@patch.dict("os.environ", {"TARGET_STORIES_PER_SPRINT": "3", "READY_BACKLOG_SPRINTS_TARGET": "2"})
class TestReadyBacklogShortfall(unittest.TestCase):
    """
    Acceptance Criteria (ISSUE-0046): backlog_scope_complete is an explicit,
    justified escape hatch from the target - never an automatic cap based on
    however many items merely happen to be in product_backlog, which would
    silently satisfy the gate for any real, open-ended backlog too (a real
    eval run's fabricated "Additional Buffer Story" entries showed what
    happens without an honest way to say scope is genuinely exhausted).
    """

    def test_reports_shortfall_against_the_full_target_regardless_of_backlog_size(self):
        # Only 2 real stories exist at all, both Ready - an open-ended
        # backlog that simply hasn't been detailed further yet must still
        # report the full shortfall (target 6, ready 2 -> short 4), not
        # silently succeed just because that's everything currently entered.
        backlog = [
            {"id": "US-0001", "type": "User Story", "stages_completed": ["Draft", "Ready"]},
            {"id": "US-0002", "type": "User Story", "stages_completed": ["Draft", "Ready"]},
        ]
        self.assertEqual(ready_backlog_shortfall(backlog), 4)

    def test_backlog_scope_complete_waives_the_target_entirely(self):
        backlog = [
            {"id": "US-0001", "type": "User Story", "stages_completed": ["Draft", "Ready"]},
            {"id": "US-0002", "type": "User Story", "stages_completed": ["Draft", "Ready"]},
        ]
        self.assertEqual(ready_backlog_shortfall(backlog, backlog_scope_complete=True), 0)

    def test_backlog_scope_complete_defaults_to_false(self):
        self.assertEqual(ready_backlog_shortfall([]), 6)


class TestCloseoutGraceScaling(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #220): closeout_grace_percent's ceiling
    must scale down as less of the SPRINT CLOSE SEQUENCE (retro -> KPIs ->
    sprint report -> release PR) remains outstanding, instead of always
    granting the full configured percentage - see closeout_grace_percent/
    closeout_remaining_work_fraction, agents/scrum_team/helpers.py.
    """

    def test_fresh_state_gets_the_full_remaining_fraction(self):
        state = ScrumState()
        self.assertEqual(closeout_remaining_work_fraction(state), 1.0)

    def test_retro_fresh_reduces_the_remaining_fraction(self):
        state = ScrumState()
        state.retro_actions = [{"action": "a", "owner": "b", "success_metric": "c", "status": "open"}]
        # retro_baseline defaults to 0; len(retro_actions)+len(impediment_log)=1 > 0 -> retro fresh.
        self.assertEqual(closeout_remaining_work_fraction(state), 0.75)

    def test_retro_and_kpi_fresh_reduces_further(self):
        state = ScrumState()
        state.retro_actions = [{"action": "a", "owner": "b", "success_metric": "c", "status": "open"}]
        state.kpi_update_count = 1  # kpi_baseline defaults to 0 -> kpi fresh
        self.assertEqual(closeout_remaining_work_fraction(state), 0.5)

    def test_pending_release_means_only_release_remains(self):
        # sprint_report_pending_release alone implies retro/KPIs/report all
        # already happened - create_sprint_report only ever sets it True
        # after both of those gates passed.
        state = ScrumState()
        state.sprint_report_pending_release = True
        self.assertEqual(closeout_remaining_work_fraction(state), 0.25)

    def test_stale_history_from_a_previous_sprint_does_not_count_as_fresh(self):
        # retro_baseline/kpi_baseline are bumped to the then-current counts
        # by create_sprint_report - a state carrying old history AND a
        # matching baseline (the normal post-report shape) must not be
        # mistaken for "already done this sprint".
        state = ScrumState()
        state.retro_actions = [{"action": "a", "owner": "b", "success_metric": "c", "status": "open"}]
        state.retro_baseline = 1  # already consumed - not fresh
        state.kpi_update_count = 3
        state.kpi_baseline = 3  # already consumed - not fresh
        self.assertEqual(closeout_remaining_work_fraction(state), 1.0)

    def test_closeout_grace_percent_without_state_is_unscaled(self):
        with patch.dict("os.environ", {"SPRINT_CLOSEOUT_GRACE_PERCENT": "20"}, clear=True):
            self.assertEqual(closeout_grace_percent(), 20.0)
            self.assertEqual(closeout_grace_percent(state=None), 20.0)

    def test_closeout_grace_percent_scales_with_state(self):
        state = ScrumState()
        state.sprint_report_pending_release = True  # remaining fraction 0.25
        with patch.dict("os.environ", {"SPRINT_CLOSEOUT_GRACE_PERCENT": "20"}, clear=True):
            self.assertEqual(closeout_grace_percent(state), 5.0)  # 20 * 0.25

    def test_closeout_grace_percent_never_scales_below_a_quarter_of_configured(self):
        # Even with everything done bar the release PR (the smallest
        # possible remaining fraction the model produces), the ceiling must
        # not collapse below 25% of the configured percentage - ISSUE-0046
        # already showed even the last step alone can burn real tokens on
        # wrong guesses.
        state = ScrumState()
        state.sprint_report_pending_release = True
        with patch.dict("os.environ", {"SPRINT_CLOSEOUT_GRACE_PERCENT": "4"}, clear=True):
            self.assertEqual(closeout_grace_percent(state), 1.0)  # 4 * max(0.25, 0.25)


class TestLooksLikeRoleBehaviorFinding(unittest.TestCase):
    """GH issue #354: heuristic signal for a "technical" finding that
    actually reads like a role-behavior/process-discipline gap."""

    def test_flags_textbook_steering_candidates_from_the_real_eval_run(self):
        for text in (
            "Maintain rigorous story sequencing and ensure all planned stories have acceptance checks recorded promptly",
            "Keep feature branches synchronized with develop to prevent integration delays",
            "Ensure all lingering process issues and backlog requirements are completed in strict sequence before final wrap-up",
        ):
            with self.subTest(text=text):
                self.assertTrue(looks_like_role_behavior_finding(text))

    def test_does_not_flag_a_genuine_code_task(self):
        self.assertFalse(looks_like_role_behavior_finding(
            "Ensure test runner and dependencies are fully configured upfront in conftest.py/pytest.ini for new projects"
        ))

    def test_flags_a_finding_naming_a_role(self):
        self.assertTrue(looks_like_role_behavior_finding("QA should run the full suite before marking Tested"))

    def test_non_string_input_is_not_flagged(self):
        self.assertFalse(looks_like_role_behavior_finding(None))

    def test_flags_process_gating_findings_the_original_phrase_list_missed(self):
        """GH issue #381: a real run (0.1.0-run47) filed these 3 of 5 retro
        findings as 'technical' despite reading as team-process reminders -
        none matched the original phrase list or a role name."""
        for text in (
            "Ensure automated tests are fully stable before starting sprint test execution phases",
            "Recreate clean PRs promptly when merge conflicts arise to avoid stale branch blocking",
            "Ensure all stories in sprint backlog reach accepted stage before sprint finalization",
        ):
            with self.subTest(text=text):
                self.assertTrue(looks_like_role_behavior_finding(text))

    def test_does_not_flag_a_one_off_technical_nitpick(self):
        """A concrete, one-time code-quality instruction - not a recurring
        process/behavior gap - should stay unflagged even though it's
        phrased as an imperative, same as test_does_not_flag_a_genuine_code_task."""
        self.assertFalse(looks_like_role_behavior_finding(
            "Ensure test assertions use dynamic IDs correctly from created objects"
        ))


class TestRecurringTechnicalFindingSprint(unittest.TestCase):
    """GH issue #354: detects a "technical" finding recurring from an
    earlier, still-unresolved sprint via keyword overlap."""

    def test_no_recurrence_against_an_empty_history(self):
        self.assertIsNone(recurring_technical_finding_sprint([], "Keep feature branches synchronized with develop", 2, "action"))

    def test_detects_recurrence_from_an_earlier_sprint(self):
        existing = [{"action": "Keep feature branches synchronized with develop to avoid drift", "category": "technical", "sprint_number": 1}]
        result = recurring_technical_finding_sprint(existing, "Feature branches must stay synchronized with develop", 3, "action")
        self.assertEqual(result, 1)

    def test_does_not_match_the_same_sprint(self):
        existing = [{"action": "Keep feature branches synchronized with develop to avoid drift", "category": "technical", "sprint_number": 2}]
        result = recurring_technical_finding_sprint(existing, "Feature branches must stay synchronized with develop", 2, "action")
        self.assertIsNone(result)

    def test_ignores_non_technical_entries(self):
        existing = [{"action": "Keep feature branches synchronized with develop to avoid drift", "category": "steering", "sprint_number": 1}]
        result = recurring_technical_finding_sprint(existing, "Feature branches must stay synchronized with develop", 3, "action")
        self.assertIsNone(result)

    def test_unrelated_findings_do_not_match(self):
        existing = [{"action": "Update the README with setup instructions", "category": "technical", "sprint_number": 1}]
        result = recurring_technical_finding_sprint(existing, "Feature branches must stay synchronized with develop", 3, "action")
        self.assertIsNone(result)


class TestCurrentSprintPhase(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #403): current_sprint_phase derives the
    team's current ritual phase - Conceptual Work -> Plan & Start Sprint ->
    Development -> Review, Retro & Release - purely from existing state
    signals, so it can be surfaced proactively (a system-context nudge plus
    a console-log prefix) instead of the team only finding out reactively,
    after a mechanically-enforced tool call gets rejected.
    """

    def _base_state(self, **overrides):
        state = {
            "sprint_number": 0,
            "sprint_backlog_pr_sprint": 0,
            "product_backlog": [],
            "sprint_backlog": [],
            "backlog_scope_complete": False,
            "sprint_report_pending_release": False,
            "budgets": {"total": 1000},
            "token_usage": {"total": 0},
        }
        state.update(overrides)
        return state

    def test_conceptual_work_when_no_sprint_and_backlog_not_ready_sufficient(self):
        state = self._base_state()
        self.assertEqual(current_sprint_phase(state), "Conceptual Work")

    def test_plan_and_start_sprint_once_backlog_is_ready_sufficient(self):
        state = self._base_state(backlog_scope_complete=True)
        self.assertEqual(current_sprint_phase(state), "Plan & Start Sprint")

    def test_plan_and_start_sprint_while_sprint_started_but_backlog_pr_not_yet_published(self):
        state = self._base_state(backlog_scope_complete=True, sprint_number=1, sprint_backlog_pr_sprint=0)
        self.assertEqual(current_sprint_phase(state), "Plan & Start Sprint")

    def test_development_once_backlog_published_and_stories_not_all_accepted(self):
        state = self._base_state(
            backlog_scope_complete=True,
            sprint_number=1,
            sprint_backlog_pr_sprint=1,
            sprint_backlog=[{"id": "ST-1", "stages_completed": ["Ready", "Implemented"]}],
        )
        self.assertEqual(current_sprint_phase(state), "Development")

    def test_review_retro_release_once_every_story_accepted(self):
        state = self._base_state(
            backlog_scope_complete=True,
            sprint_number=1,
            sprint_backlog_pr_sprint=1,
            sprint_backlog=[{"id": "ST-1", "stages_completed": ["Ready", "Implemented", "Reviewed", "Tested", "Accepted"]}],
        )
        self.assertEqual(current_sprint_phase(state), "Review, Retro & Release")

    def test_review_retro_release_once_budget_exhausted_even_with_unfinished_stories(self):
        state = self._base_state(
            backlog_scope_complete=True,
            sprint_number=1,
            sprint_backlog_pr_sprint=1,
            sprint_backlog=[{"id": "ST-1", "stages_completed": ["Ready"]}],
            budgets={"total": 1000},
            token_usage={"total": 1000},
        )
        self.assertEqual(current_sprint_phase(state), "Review, Retro & Release")

    def test_review_retro_release_while_report_done_but_release_still_pending(self):
        state = self._base_state(
            backlog_scope_complete=True,
            sprint_number=1,
            sprint_backlog_pr_sprint=1,
            sprint_report_pending_release=True,
        )
        self.assertEqual(current_sprint_phase(state), "Review, Retro & Release")

    def test_epics_are_never_counted_toward_all_accepted(self):
        """An Epic sitting in sprint_backlog alongside a not-yet-accepted
        story must not itself cause (or prevent) "every story accepted" -
        only real stories count."""
        state = self._base_state(
            backlog_scope_complete=True,
            sprint_number=1,
            sprint_backlog_pr_sprint=1,
            sprint_backlog=[
                {"id": "EP-1", "type": "Epic", "stages_completed": []},
                {"id": "ST-1", "stages_completed": ["Ready", "Implemented", "Reviewed", "Tested", "Accepted"]},
            ],
        )
        self.assertEqual(current_sprint_phase(state), "Review, Retro & Release")

    def test_every_phase_has_its_own_guidance_text(self):
        for phase in SPRINT_PHASES:
            with self.subTest(phase=phase):
                self.assertIn(phase, SPRINT_PHASE_GUIDANCE)
                self.assertTrue(SPRINT_PHASE_GUIDANCE[phase])


if __name__ == "__main__":
    unittest.main()
