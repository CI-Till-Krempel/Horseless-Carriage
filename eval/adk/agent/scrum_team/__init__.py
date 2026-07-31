"""
ADK CLI eval loader shim (see eval/adk/README.md "Why this shim exists").

`adk eval <AGENT_MODULE_FILE_PATH> ...` (google.adk.cli.cli_eval._get_agent_module)
loads `<AGENT_MODULE_FILE_PATH>/__init__.py` as a module literally named
"agent", then accesses `<that module>.agent.root_agent` - i.e. it requires
the package's own __init__.py to import its `agent` submodule (the common
ADK quickstart shape: `__init__.py` containing `from . import agent`).

The project's real entrypoint package, agents/scrum_team/ (see
agents/scrum_team/agent.py), does not have an __init__.py at all - it is
imported today only via agents/agent.py's `from agents.scrum_team.agent
import root_agent`, which is enough for `adk web`/`adk run` (they use
google.adk.cli.utils.agent_loader.AgentLoader, a more flexible loader that
does a real `importlib.import_module(...)`), but is NOT enough for `adk
eval`'s stricter loader - verified directly against the installed adk
package in this environment; see eval/adk/README.md's "Deviation" section
for the exact AttributeError this produces without this shim.

This package re-exports the real root_agent unchanged so `adk eval` has a
loadable module shaped the way its loader expects, without modifying
agents/__init__.py or agents/scrum_team/ at all (out of scope for this
pass - see the task constraints in eval/adk/README.md).
"""
from . import agent  # noqa: F401
