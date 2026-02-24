"""
ADK Dev UI entrypoint.

ADK searches for `agents.agent.root_agent` when you run `adk web <agents_dir>`.
We re-export the actual root agent defined in `agents/scrum_team/agent.py`.
"""

from agents.scrum_team.agent import root_agent  # noqa: F401