"""
Re-exports the real root_agent (agents/scrum_team/agent.py) so `adk eval`
(see this package's __init__.py docstring) can load it via
`adk eval eval/adk/agent/scrum_team ...`, with app_name resolving to
"scrum_team" (os.path.basename of the AGENT_MODULE_FILE_PATH argument) -
matching the `session_input.app_name` used throughout
eval/adk/scrum_team.evalset.json.
"""
import sys
from pathlib import Path

# Repo root is 4 levels up from this file:
# eval/adk/agent/scrum_team/agent.py -> eval/adk/agent/scrum_team -> eval/adk/agent -> eval/adk -> eval -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.scrum_team.agent import root_agent  # noqa: E402,F401
