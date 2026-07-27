[← Back to README](../README.md)

# Testing

`python3 run_tests.py` runs everything in one command - both suites below,
the host-script suite first (fails fast, no need to wait for Docker if it's
broken):

```bash
python3 run_tests.py
```

## Host-script tests (`tests/`)

Covers the setup/doctor tooling itself (`lib_env.py`, `lib_llm_test.py`,
`setup_llm.py`, `doctor.py`, `check_state_repo.py`, `run.py`) - `.env`
read/write correctness, LiteLLM model-YAML generation for all 4 providers,
each provider's live-model-list fetch/filtering logic (mocked HTTP), the
state-repository setup logic (clone/init/leave-alone, against a local git
remote - no network needed), and every guard-clause branch in
`doctor.py`/`check_state_repo.py`. Runs directly on the host, no Docker
required (that's the point - these scripts must work before any container
exists) and no real network calls (a local mock HTTP server stands in for
the LiteLLM proxy). Requires `pytest` and `PyYAML`
(`pip install pytest pyyaml`, or `pip install -r requirements.txt`) - the
only place in this project where a host-side pip install is needed. Run
just this suite with:

```bash
python3 -m pytest tests/ -v
```

## Agent test suite (`agents/scrum_team/tests`, via Docker Compose)

To run the complete agent test suite (both unit and integration tests) using Docker Compose:

```bash
python3 run_tests.py
```

This script executes `pytest` inside the agent container, providing full network access to the LiteLLM and Database services. It includes coverage reporting for the `agents/` package.

### Integration Testing

The integration test suite (`test_llm_integration.py`) verifies the end-to-end connection between the agents and the LiteLLM Proxy. It ensures that:
- **Key Generation**: Virtual keys are correctly created for different agent roles.
- **Budget Association**: These keys are correctly linked to the shared `scrum-sprint-budget`.
- **Proxy Routing**: LLM calls from agents are successfully routed through the LiteLLM Proxy.

For integration tests to function, the `run_tests.py` script utilizes `docker compose run`, which automatically starts the necessary dependency containers (`litellm` and `db`) if they are not already active.

## Manual QA test plans

Before cutting a release, a human walks through [qa/](../qa/) - see e.g.
[qa/0.1.0-testplan.md](../qa/0.1.0-testplan.md) - covering the interactive
flows, real Docker/GitHub behavior, and cross-platform checks the automated
suites above can't fully exercise on their own.
