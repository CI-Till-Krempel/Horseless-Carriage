import io
import time
import unittest

from agents.scrum_team import tui


class TestAvatarFor(unittest.TestCase):
    def test_known_role(self):
        icon, label = tui.avatar_for("DevTeam")
        self.assertEqual(label, "Dev Team")
        self.assertTrue(icon)

    def test_unknown_role_falls_back_to_default(self):
        self.assertEqual(tui.avatar_for("SomeFutureRole"), tui.DEFAULT_AVATAR)

    def test_none_role_falls_back_to_default(self):
        self.assertEqual(tui.avatar_for(None), tui.DEFAULT_AVATAR)


class TestSpeechBubble(unittest.TestCase):
    def test_short_message_single_line_box(self):
        bubble = tui.speech_bubble("DevTeam", "write_file(path)")
        lines = bubble.splitlines()
        self.assertEqual(len(lines), 4)  # top, body, bottom, tail
        self.assertTrue(lines[0].startswith("╭") and lines[0].endswith("╮"))
        self.assertTrue(lines[2].startswith("╰") and lines[2].endswith("╯"))
        self.assertIn("write_file(path)", lines[1])
        self.assertIn("Dev Team", lines[3])

    def test_box_width_matches_content(self):
        bubble = tui.speech_bubble("Architect", "short")
        top, body, bottom, _tail = bubble.splitlines()
        self.assertEqual(len(top), len(bottom))
        self.assertEqual(len(top), len(body))

    def test_long_message_wraps_to_multiple_lines(self):
        long_message = " ".join(["word"] * 40)
        bubble = tui.speech_bubble("ScrumMaster", long_message, width=20)
        lines = bubble.splitlines()
        # top + N body lines + bottom + tail
        self.assertGreater(len(lines), 4)
        for line in lines[1:-2]:
            self.assertTrue(line.startswith("│") and line.endswith("│"))

    def test_empty_message_does_not_crash(self):
        bubble = tui.speech_bubble("QualityGuardian", "")
        self.assertIn("no message", bubble)

    def test_unrecognized_role_uses_default_avatar(self):
        bubble = tui.speech_bubble("Mystery", "hello")
        self.assertIn("Agent", bubble)


class TestSpinner(unittest.TestCase):
    def test_noop_when_stream_is_not_a_tty(self):
        stream = io.StringIO()  # StringIO.isatty() is False
        spinner = tui.Spinner(stream=stream)
        spinner.start("Working")
        time.sleep(0.05)
        spinner.stop()
        self.assertEqual(stream.getvalue(), "")

    def test_stop_without_start_is_a_noop(self):
        stream = io.StringIO()
        spinner = tui.Spinner(stream=stream)
        spinner.stop()  # must not raise

    def test_reference_counted_nested_start_stop(self):
        stream = _FakeTTY()
        spinner = tui.Spinner(stream=stream, interval=0.01)
        spinner.start("outer")
        spinner.start("inner")
        time.sleep(0.05)
        self.assertIsNotNone(spinner._thread)
        spinner.stop()  # inner stop: still referenced by outer
        self.assertIsNotNone(spinner._thread)
        spinner.stop()  # outer stop: now actually stops
        self.assertIsNone(spinner._thread)

    def test_writes_frames_when_a_tty(self):
        stream = _FakeTTY()
        spinner = tui.Spinner(stream=stream, interval=0.01)
        spinner.start("Working")
        time.sleep(0.05)
        spinner.stop()
        self.assertIn("Working", stream.getvalue())


class TestStartStopThinking(unittest.TestCase):
    def test_does_not_raise_without_a_tty(self):
        tui.start_thinking("ProductOwner")
        tui.stop_thinking()

    def test_stop_thinking_without_start_does_not_raise(self):
        tui.stop_thinking()


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


if __name__ == "__main__":
    unittest.main()
