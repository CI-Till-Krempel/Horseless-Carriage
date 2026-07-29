# agents/scrum_team/tests/test_notifications.py
import unittest
from unittest.mock import MagicMock, patch

from agents.scrum_team.tools.notifications import (
    Notifier,
    ConsoleNotifier,
    NOTIFIER_REGISTRY,
    get_configured_notifiers,
    record_blocking_interaction,
    resolve_blocking_interaction,
    list_blocking_interactions,
)
from agents.scrum_team.state import ScrumState


def _tool_context():
    tc = MagicMock()
    tc.state = ScrumState().model_dump()
    return tc


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestRecordBlockingInteraction(unittest.TestCase):
    """
    Acceptance Criteria (GH issue #53): "task list style list of blocking
    interactions" - a persisted, incrementally-ID'd record of every
    absolutely-necessary-human-feedback moment or critical tool error.
    """

    def test_records_entry_with_expected_shape(self, mock_save):
        tc = _tool_context()
        result = record_blocking_interaction("approval", "Needs a release approval", detail="see docs", tool_context=tc)

        self.assertEqual(result["status"], "ok")
        entry = result["interaction"]
        self.assertEqual(entry["id"], 1)
        self.assertEqual(entry["kind"], "approval")
        self.assertEqual(entry["summary"], "Needs a release approval")
        self.assertEqual(entry["detail"], "see docs")
        self.assertFalse(entry["resolved"])
        self.assertIsNone(entry["resolved_at"])
        self.assertIn("created_at", entry)
        self.assertEqual(tc.state["blocking_interactions"], [entry])

    def test_blank_summary_is_rejected(self, mock_save):
        tc = _tool_context()
        result = record_blocking_interaction("approval", "   ", tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertEqual(tc.state["blocking_interactions"], [])

    def test_ids_increment_across_calls(self, mock_save):
        tc = _tool_context()
        first = record_blocking_interaction("approval", "First", tool_context=tc)["interaction"]
        second = record_blocking_interaction("critical_error", "Second", tool_context=tc)["interaction"]
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)

    def test_persists_via_save_state_to_repo(self, mock_save):
        tc = _tool_context()
        record_blocking_interaction("approval", "Needs approval", tool_context=tc)
        mock_save.assert_called_once_with(tc)

    def test_fires_every_configured_notifier(self, mock_save):
        tc = _tool_context()
        fake_notifier = MagicMock(spec=Notifier)
        with patch("agents.scrum_team.tools.notifications.get_configured_notifiers", return_value=[fake_notifier]):
            result = record_blocking_interaction("approval", "Needs approval", tool_context=tc)
        fake_notifier.notify.assert_called_once_with(result["interaction"])

    def test_one_failing_notifier_does_not_break_recording(self, mock_save):
        tc = _tool_context()
        broken_notifier = MagicMock(spec=Notifier)
        broken_notifier.notify.side_effect = RuntimeError("boom")
        with patch("agents.scrum_team.tools.notifications.get_configured_notifiers", return_value=[broken_notifier]):
            result = record_blocking_interaction("approval", "Needs approval", tool_context=tc)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(tc.state["blocking_interactions"]), 1)


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestResolveBlockingInteraction(unittest.TestCase):
    def test_marks_matching_entry_resolved(self, mock_save):
        tc = _tool_context()
        entry_id = record_blocking_interaction("approval", "Needs approval", tool_context=tc)["interaction"]["id"]

        result = resolve_blocking_interaction(entry_id, tool_context=tc)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(tc.state["blocking_interactions"][0]["resolved"])
        self.assertIsNotNone(tc.state["blocking_interactions"][0]["resolved_at"])

    def test_already_resolved_is_rejected(self, mock_save):
        tc = _tool_context()
        entry_id = record_blocking_interaction("approval", "Needs approval", tool_context=tc)["interaction"]["id"]
        resolve_blocking_interaction(entry_id, tool_context=tc)

        result = resolve_blocking_interaction(entry_id, tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("already resolved", result["message"])

    def test_unknown_id_is_rejected(self, mock_save):
        tc = _tool_context()
        result = resolve_blocking_interaction(999, tool_context=tc)
        self.assertEqual(result["status"], "error")
        self.assertIn("No blocking interaction", result["message"])


@patch("agents.scrum_team.tools.scrum.save_state_to_repo", return_value={"status": "ok"})
class TestListBlockingInteractions(unittest.TestCase):
    def test_defaults_to_open_only(self, mock_save):
        tc = _tool_context()
        open_id = record_blocking_interaction("approval", "Still open", tool_context=tc)["interaction"]["id"]
        resolved_id = record_blocking_interaction("critical_error", "Now resolved", tool_context=tc)["interaction"]["id"]
        resolve_blocking_interaction(resolved_id, tool_context=tc)

        result = list_blocking_interactions(tool_context=tc)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["interactions"][0]["id"], open_id)

    def test_include_resolved_shows_everything(self, mock_save):
        tc = _tool_context()
        record_blocking_interaction("approval", "Still open", tool_context=tc)
        resolved_id = record_blocking_interaction("critical_error", "Now resolved", tool_context=tc)["interaction"]["id"]
        resolve_blocking_interaction(resolved_id, tool_context=tc)

        result = list_blocking_interactions(include_resolved=True, tool_context=tc)

        self.assertEqual(result["count"], 2)

    def test_empty_state_reports_zero(self, mock_save):
        tc = _tool_context()
        result = list_blocking_interactions(tool_context=tc)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["interactions"], [])


class TestConsoleNotifier(unittest.TestCase):
    def test_notify_prints_summary_and_detail(self):
        notifier = ConsoleNotifier()
        with patch("sys.stderr") as mock_stderr:
            notifier.notify({"kind": "approval", "summary": "Needs a release approval", "detail": "see docs"})
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("Needs a release approval", printed)
        self.assertIn("see docs", printed)
        self.assertIn("approval", printed)

    def test_notify_without_detail_does_not_crash(self):
        notifier = ConsoleNotifier()
        with patch("sys.stderr"):
            notifier.notify({"kind": "critical_error", "summary": "Budget exceeded"})


class TestGetConfiguredNotifiers(unittest.TestCase):
    def test_defaults_to_console_only(self):
        with patch.dict("os.environ", {}, clear=True):
            notifiers = get_configured_notifiers()
        self.assertEqual(len(notifiers), 1)
        self.assertIsInstance(notifiers[0], ConsoleNotifier)

    def test_unknown_plugin_name_is_skipped_with_a_warning(self):
        with patch.dict("os.environ", {"NOTIFICATION_PLUGINS": "console,nonexistent"}, clear=True):
            with patch("sys.stderr") as mock_stderr:
                notifiers = get_configured_notifiers()
        self.assertEqual(len(notifiers), 1)
        self.assertIsInstance(notifiers[0], ConsoleNotifier)
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("unknown notification plugin", printed)

    def test_registry_contains_console_by_default(self):
        self.assertIn("console", NOTIFIER_REGISTRY)
        self.assertTrue(issubclass(NOTIFIER_REGISTRY["console"], Notifier))


if __name__ == "__main__":
    unittest.main()
