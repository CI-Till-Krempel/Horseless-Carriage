# agents/scrum_team/tests/conftest.py
"""
Safety net against tests leaking real writes into this repo's own working
tree.

agents/scrum_team/tools/base.py's _configured_repo_root(tool_context)
resolves where doc/state writes go, in this order: INTERNAL_STATE_REPO_PATH
env var, STATE_REPO_PATH env var, tool_context.state["repo"]["local_path"],
and - only if none of those are set - _project_root() (this actual
Horseless-Carriage checkout) as a last-resort fallback. A test whose
tool_context doesn't configure any of the first three silently falls
through to that fallback and writes real files here - this already
happened (specs/stories/EP-1-New-Epic.md, ST-1-New-Story.md,
.hc/state.json, etc., all since deleted and gitignored).

Every module that resolves a repo path imports its own bound reference via
`from .base import _configured_repo_root` (docs.py, requirements.py,
github.py, budget.py, scrum.py, quality.py) - each is a separate name
bound at import time, so patching base.py's copy alone would not affect
calls made through the others (this is exactly how an earlier attempt at
isolating agents.scrum_team.tools.requirements._configured_repo_root
still let save_state_to_repo - which resolves its own path via
agents.scrum_team.tools.scrum's copy - leak `.hc/state.json`). This
autouse fixture patches all of them for every test, so no test can leak
regardless of which module's copy it goes through.

The patch calls through to the REAL _configured_repo_root first and only
substitutes the isolated tmp_path when that real call actually fell
through to the _project_root() fallback - so a test that configures things
properly (env vars, like test_id_generation.py/test_state_persistence.py,
or tool_context.state["repo"]["local_path"]) still resolves exactly as it
would in real usage. A test's own explicit
patch("...<module>._configured_repo_root", ...) inside its body still
takes precedence for its scope, since it nests on top of this one and is
unwound back to it afterwards.
"""
from unittest.mock import patch

import pytest

from agents.scrum_team.tools.base import _configured_repo_root as _real_configured_repo_root
from agents.scrum_team.tools.base import _project_root

_REAL_PROJECT_ROOT = _project_root()

_MODULES_WITH_REPO_ROOT = [
    "agents.scrum_team.tools.base",
    "agents.scrum_team.tools.docs",
    "agents.scrum_team.tools.requirements",
    "agents.scrum_team.tools.github",
    "agents.scrum_team.tools.budget",
    "agents.scrum_team.tools.scrum",
    "agents.scrum_team.tools.quality",
]


@pytest.fixture(autouse=True)
def _isolated_repo_root(tmp_path):
    fake_root = tmp_path / "unconfigured-repo-root"
    fake_root.mkdir()

    def _redirect_unconfigured_fallback(tool_context=None):
        resolved = _real_configured_repo_root(tool_context)
        return fake_root if resolved == _REAL_PROJECT_ROOT else resolved

    patchers = [
        patch(f"{module}._configured_repo_root", side_effect=_redirect_unconfigured_fallback)
        for module in _MODULES_WITH_REPO_ROOT
    ]
    for p in patchers:
        p.start()
    try:
        yield fake_root
    finally:
        for p in patchers:
            p.stop()
