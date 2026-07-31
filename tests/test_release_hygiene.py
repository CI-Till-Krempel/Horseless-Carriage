"""
Regression tests for GH issue #123: a v0.1.0 tag should be built from a
reproducible dependency graph and never leak secrets into an image layer.

- agent.Dockerfile's `test` stage does `COPY . .`, which would copy a real,
  gitignored-but-present .env (live API keys), .git/, and sessions/ into an
  image layer if that stage is ever invoked directly (e.g. `docker build
  --target test ...`). .dockerignore must keep excluding them.
- requirements.txt used to leave every package but litellm unpinned, so a
  `--pull` rebuild months apart could silently resolve a different
  google-adk (a fast-moving library) with no way to reproduce a given
  release's exact dependency graph. Every entry must pin an exact version.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dockerignore_excludes_secrets_and_local_state():
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in text.splitlines()}
    for expected in (".env", ".git/", "sessions/", ".venv/", "eval-output/", "__pycache__/"):
        assert expected in lines, (
            f".dockerignore is missing `{expected}` - agent.Dockerfile's `test` "
            "stage does `COPY . .`, which would otherwise bake it into an "
            "image layer (GH issue #123)."
        )


def test_requirements_txt_pins_every_dependency_to_an_exact_version():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    unpinned = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.search(r"==\d", line):
            unpinned.append(line)
    assert not unpinned, (
        f"requirements.txt has unpinned dependencies: {unpinned} - a rebuild "
        "today vs. one months from now could resolve a different version "
        "with no way to reproduce a given release's dependency graph "
        "(GH issue #123)."
    )
