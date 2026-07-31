"""Terminal presentation helpers for AGENT_MODE=cli.

ADK's own `adk run` REPL only renders events that carry `.text` - a pure
tool-call/tool-response event has none, so tool activity is otherwise
invisible in a foreground CLI session (see agent.py's log_tool_invocation_callback,
which this module renders for). This module also provides a busy indicator
for the gap between sending a request and getting a reply, since a real
model call can take several seconds with no other output in between.

Everything here is cosmetic and safe to no-op: nothing in this module ever
raises out to a caller, and the busy indicator only activates against a real
terminal (never in `adk web` mode, piped output, or tests).
"""

import sys
import textwrap
import threading
from typing import Optional, TextIO

ROLE_AVATARS = {
    "ScrumOrchestrator": ("\U0001f40e", "Orchestrator"),
    "ProductOwner": ("\U0001f4cb", "Product Owner"),
    "ScrumMaster": ("\U0001f9ed", "Scrum Master"),
    "DevTeam": ("\U0001f6e0", "Dev Team"),
    "QualityGuardian": ("\U0001f50d", "QA"),
    "Architect": ("\U0001f3db", "Architect"),
}
DEFAULT_AVATAR = ("\U0001f916", "Agent")


def avatar_for(role: Optional[str]) -> tuple:
    """(icon, label) for a role name; a generic robot icon for anything
    unrecognized (e.g. a future role, or a malformed/missing agent_name) -
    never raises, never returns an empty label."""
    return ROLE_AVATARS.get(role or "", DEFAULT_AVATAR)


def speech_bubble(role: Optional[str], message: str, width: int = 76) -> str:
    """A boxed, cowsay-inspired callout: a rounded box around `message`, with
    a small tail pointing down to the role's avatar/label. Pure string
    formatting - callers decide whether/where to print it."""
    message = message if message else "(no message)"
    lines = textwrap.wrap(message, width=width) or [message[:width]]
    inner = max(len(line) for line in lines)
    icon, label = avatar_for(role)

    top = "╭" + "─" * (inner + 2) + "╮"
    bottom = "╰" + "─" * (inner + 2) + "╯"
    body = "\n".join(f"│ {line.ljust(inner)} │" for line in lines)
    tail = f"   {icon} {label}"
    return f"{top}\n{body}\n{bottom}\n{tail}"


class Spinner:
    """A minimal, reference-counted terminal busy indicator.

    Reference-counted so overlapping start()/stop() calls (e.g. a nested
    sub-agent call while a parent call is still "in flight") don't stomp on
    each other - only the first start() actually spins, only the matching
    final stop() actually stops it. A stray stop() with no matching start()
    is a no-op, never an error.

    No-ops entirely when `stream` isn't a real terminal, so this is always
    safe to call from `adk web` mode, CI, or piped output.
    """

    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, stream: Optional[TextIO] = None, interval: float = 0.08):
        self._stream = stream or sys.stderr
        self._interval = interval
        self._lock = threading.Lock()
        self._depth = 0
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._label = "Working"

    def _is_tty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    def start(self, label: str = "Working") -> None:
        with self._lock:
            self._depth += 1
            self._label = label
            if self._depth > 1 or not self._is_tty():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop_event, label), daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._depth == 0:
                return
            self._depth -= 1
            if self._depth > 0:
                return
            stop_event, thread = self._stop_event, self._thread
            self._stop_event, self._thread = None, None
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=1.0)

    def _run(self, stop_event: threading.Event, label: str) -> None:
        i = 0
        try:
            while not stop_event.is_set():
                frame = self._FRAMES[i % len(self._FRAMES)]
                try:
                    self._stream.write(f"\r{frame} {label}...  ")
                    self._stream.flush()
                except Exception:
                    return
                i += 1
                stop_event.wait(self._interval)
        finally:
            try:
                self._stream.write("\r" + " " * (len(label) + 12) + "\r")
                self._stream.flush()
            except Exception:
                pass


_thinking_spinner = Spinner()


def start_thinking(role: Optional[str]) -> None:
    icon, label = avatar_for(role)
    try:
        _thinking_spinner.start(f"{icon} {label} is thinking")
    except Exception:
        pass


def stop_thinking() -> None:
    try:
        _thinking_spinner.stop()
    except Exception:
        pass
