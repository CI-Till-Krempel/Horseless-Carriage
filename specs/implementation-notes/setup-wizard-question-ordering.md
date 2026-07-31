# setup_llm.py question ordering and git-identity default

`setup_llm.py` asks about the provider/model first, then project-wide settings (Git identity, state
repository, human interaction level, sprint budgets), then writes config and runs a live end-to-end
test.

Prior to the v0.1.0 readiness pass, the ordering was reversed: the git-identity/state-repo/
interaction-level/budget questions were asked *before* the provider-specific questions, so a user
picking "Anthropic Claude" or "Local / Ollama" had to get through unrelated project setup first. This
was reordered so provider selection comes first.

At the same time, the Git identity prompt's default changed from a generic `DevTeam`/
`devteam@company.com` placeholder to the host machine's own `git config --global user.name`/
`user.email` (`setup_llm._host_git_identity`) when nothing is already configured in `.env` - a more
plausible starting point for most users. This is safe because real commits the agent makes are
attributed per-role (`Architect`, `DevTeam`, ...) via `GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME`
overrides (see `agents/scrum_team/tools/base.py`), not this value - `GIT_USER_NAME`/`GIT_USER_EMAIL`
is only the global git-config fallback for anything that doesn't get that per-role override.
