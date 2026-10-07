# agents/scrum_team/tools/quality.py
import ast
import json
import os
import re
import requests
import litellm
from pathlib import Path
from ..state import ScrumState
from ..prompts import ROLE_NAMES, ROLE_PROMPT_TEXT
from typing import Dict, Any, List
from .base import _configured_repo_root, _run, _coerce_dict_arg

# A real eval run had QualityGuardian retry update_sprint_report ~15 times in
# a row with kpis="calculate_kpis"/"calculate_kpis()" - the NAME of the tool
# above, as a plain string, apparently expecting update_sprint_report to call
# it - before burning through the eval's whole LLM-call budget. Recognized
# below so that shape self-heals instead of looping.
_CALCULATE_KPIS_CALL_ALIASES = {"calculate_kpis", "calculate_kpis()"}

# Maps each role to the role-key agent.py's own get_model_name(role_key)
# uses to resolve its LiteLLM proxy model alias (SCRUM_<ROLE_KEY>_MODEL env
# var, default "scrum-<role_key>") - see agent.py's LlmAgent construction
# for each role's own get_model_name(...) call site. Duplicated here
# (rather than importing get_model_name from agent.py) to avoid a circular
# import: agent.py imports from tools/__init__.py, which imports this
# module - agent.py cannot be imported back from here.
_ROLE_MODEL_KEYS = {
    "ScrumOrchestrator": "orchestrator",
    "ProductOwner": "po",
    "ScrumMaster": "sm",
    "DevTeam": "dev",
    "QA": "qa",
    "Architect": "arch",
}


def _model_alias_for_role(role: str) -> str:
    role_key = _ROLE_MODEL_KEYS[role]
    return os.getenv(f"SCRUM_{role_key.upper()}_MODEL", f"scrum-{role_key}")


def _fetch_model_context_windows() -> Dict[str, Dict[str, Any]]:
    """
    Queries the LiteLLM proxy's own `/model/info` endpoint once for every
    configured model alias's real underlying model and the context window
    litellm already computed for it (`max_input_tokens`) - the alias
    itself (e.g. "scrum-po") isn't something `litellm.get_model_info`
    recognizes on its own (confirmed empirically: it raises "model isn't
    mapped yet" for a bare alias), and this project's litellm.yaml/config
    is only ever mounted into the `litellm` proxy container, never the
    `agent` one - so the proxy is the only thing that actually knows the
    alias -> real-model mapping at runtime.

    Returns {} on ANY failure (no master key configured, proxy
    unreachable, non-2xx response, malformed JSON) - this is best-effort
    observability data for calculate_prompt_context_usage below, never
    something that should fail a KPI calculation outright.
    """
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    if not master_key:
        return {}
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://litellm:4000")
    try:
        resp = requests.get(
            f"{proxy_base}/model/info",
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        entries = resp.json().get("data", []) or []
    except Exception:
        return {}

    windows: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        alias = entry.get("model_name")
        if not alias:
            continue
        real_model = (entry.get("litellm_params") or {}).get("model")
        model_info = entry.get("model_info") or {}
        max_input_tokens = model_info.get("max_input_tokens") or model_info.get("max_tokens")
        if real_model and max_input_tokens:
            windows[alias] = {"real_model": real_model, "max_input_tokens": max_input_tokens}
    return windows


def calculate_prompt_context_usage() -> Dict[str, Dict[str, Any]]:
    """
    For every role, measures how many tokens its concatenated, STATIC
    system prompt (guardrails + workflow + Definition of Done/Ready - see
    prompts.py's own module docstring and docs/AGENT-PROMPTS.md) costs
    against that role's currently configured model's context window - "the
    context window of the used model may vary" (a real review comment)
    because different roles can be configured with different models
    (`SCRUM_<ROLE>_MODEL` env vars), each with its own context window.

    Deliberately measures prompts.ROLE_PROMPT_TEXT only - the fixed part
    prompts.py actually concatenates at import time - not the
    dynamically-injected `<Role>-identity.md` customization or any other
    per-session context (sprint status, tool results), which vary session
    to session and aren't part of "the concatenated system prompt" as
    prompts.py defines it.

    Per role, returns `{model, prompt_tokens, context_window_tokens,
    usage_percent, available}` - `context_window_tokens`/`usage_percent`
    are `None` and `available` is `False` when the model's context window
    couldn't be determined (LiteLLM proxy unreachable, or this model isn't
    in litellm's known cost/context-window map) - never a crash, since
    this is observability data layered on top of the real KPIs, not a
    correctness gate.
    """
    context_windows = _fetch_model_context_windows()
    usage: Dict[str, Dict[str, Any]] = {}
    for role in ROLE_NAMES:
        alias = _model_alias_for_role(role)
        window = context_windows.get(alias)
        token_count_model = window["real_model"] if window else alias
        prompt_tokens = litellm.token_counter(model=token_count_model, text=ROLE_PROMPT_TEXT[role])

        entry: Dict[str, Any] = {"model": alias, "prompt_tokens": prompt_tokens}
        if window:
            context_window_tokens = window["max_input_tokens"]
            entry["context_window_tokens"] = context_window_tokens
            entry["usage_percent"] = round(100 * prompt_tokens / context_window_tokens, 2)
            entry["available"] = True
        else:
            entry["context_window_tokens"] = None
            entry["usage_percent"] = None
            entry["available"] = False
            entry["note"] = (
                "Could not determine this model's context window (LiteLLM proxy unreachable, or "
                "this model isn't in litellm's known context-window map)."
            )
        usage[role] = entry
    return usage

_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", re.MULTILINE)
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_ERROR_RE = re.compile(r"(\d+)\s+error")
_NO_TESTS_RE = re.compile(r"no tests ran")
_AVG_COMPLEXITY_RE = re.compile(r"Average complexity:\s+[A-F]\s+\(([\d.]+)\)")
_LANGUAGE_SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__"}


def _execute_test_suite_coverage(tool_context=None) -> Dict[str, Any]:
    """
    Runs pytest with coverage against the configured target repo and parses
    the real coverage percentage and pass/fail counts from its output,
    rather than fabricating a number.

    PYTHONPATH is explicitly set to repo_root (prepended ahead of anything
    already set) - ISSUE-0047: a real eval run bounced a story back and
    forth 9 times (QA/DevTeam) on an identical
    "ModuleNotFoundError: No module named 'app'", for a project laid out
    exactly like check_build's own docstring describes as normal (a
    root-level app.py, tests under tests/). Running pytest with cwd=repo_root
    does NOT put repo_root on sys.path - pytest's default "prepend" import
    mode (no tests/__init__.py, no conftest.py/pytest.ini at repo_root)
    inserts each test file's own containing directory (tests/) instead, so
    `from app import ...` in tests/test_app.py never resolves regardless of
    how many times a cheap model rewrites the test file itself (it tried
    inserting tests/'s own directory into sys.path, which was already
    implicitly there and never the actual gap). This is the harness's own
    responsibility to get right, the same way check_build's real `pip
    install` fixed the sibling case of this exact symptom (a missing
    third-party dependency) - not something to leave for whichever
    generated project happens to add its own pytest.ini/conftest.py.
    """
    repo_root = _configured_repo_root(tool_context)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    result = _run(
        ["pytest", "--cov", "--cov-report=term", "-q", "--no-header"],
        cwd=str(repo_root),
        tool_context=tool_context,
        env_overrides={"PYTHONPATH": pythonpath},
    )

    if result.get("status") == "error" and "returncode" not in result:
        # The subprocess itself couldn't be started at all (e.g. pytest not
        # installed in the target repo) - distinct from pytest running and
        # exiting non-zero because tests failed (that path still has stdout
        # to parse below).
        return {
            "available": False,
            "test_coverage": None,
            "tests_run": 0,
            "tests_failed": 0,
            "note": f"pytest could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""

    if _NO_TESTS_RE.search(stdout):
        return {
            "available": True,
            "test_coverage": 0.0,
            "tests_run": 0,
            "tests_failed": 0,
            "note": "no tests collected",
        }

    coverage_match = _COVERAGE_TOTAL_RE.search(stdout)
    test_coverage = int(coverage_match.group(1)) / 100.0 if coverage_match else None

    passed = int(m.group(1)) if (m := _PASSED_RE.search(stdout)) else 0
    failed = int(m.group(1)) if (m := _FAILED_RE.search(stdout)) else 0
    errored = int(m.group(1)) if (m := _ERROR_RE.search(stdout)) else 0

    note = None
    if test_coverage is None:
        # A real eval run hit this repeatedly - QA and DevTeam bounced a
        # story back and forth 9 times, always rejected by the exact same
        # opaque "coverage summary not found" note, with no way to tell
        # whether pytest crashed outright, a dependency was missing, or
        # something else entirely - so every retry guessed blindly at
        # unrelated tooling changes (CI config, requirements.txt, moving
        # test files) instead of whatever the real cause actually was.
        # Surface enough of pytest's own output for that to be diagnosable
        # instead of a black box.
        tail = (stdout or stderr)[-800:].strip()
        note = (
            f"coverage summary not found in pytest output (exit code {result.get('returncode')})"
            + (f" - last output:\n{tail}" if tail else " - no output was captured at all.")
        )

    return {
        "available": test_coverage is not None,
        "test_coverage": test_coverage,
        "tests_run": passed + failed + errored,
        "tests_failed": failed + errored,
        "note": note,
    }


def _is_trivial_test_body(node) -> bool:
    """
    GH issue #370: True if a test function's body carries no real
    assertion at all - just `pass`, a bare docstring, `assert True`, or a
    tautological `assert <literal> == <same literal>`. A real eval run
    showed DevTeam hedge a genuinely flaky real test suite by also writing
    separate stub test files alongside it with exactly this shape, so
    check_build's pass/fail count always had *something* passing - these
    functions verify nothing, but silently inflate the count the same way a
    real test's pass does.

    Deliberately a narrow, explicit heuristic (false positives are fine -
    this only ever produces a non-blocking warning, never a refusal; false
    negatives just mean the nudge doesn't fire) rather than deeper static
    analysis - a test with a fixture argument, multiple statements, or any
    non-tautological assertion is never flagged, even if it turns out to be
    weak in some other way.
    """
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # a leading docstring doesn't itself make a test non-trivial
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if not isinstance(stmt, ast.Assert):
        return False
    test = stmt.test
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    if (
        isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Constant) and isinstance(test.comparators[0], ast.Constant)
        and test.left.value == test.comparators[0].value
    ):
        return True
    return False


def detect_stubbed_tests(repo_root) -> List[Dict[str, str]]:
    """
    Scans test_*.py/*_test.py files under repo_root (pytest's own default
    discovery convention) for suspiciously trivial test functions - see
    _is_trivial_test_body. Returns a list of {"file": <relative path>,
    "function": <name>} for each one found; empty if none. Best-effort: a
    file that fails to parse is simply skipped, not an error.
    """
    repo_root = Path(repo_root)
    findings: List[Dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _LANGUAGE_SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py") or not (fname.startswith("test_") or fname.endswith("_test.py")):
                continue
            fp = Path(dirpath) / fname
            try:
                tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    if _is_trivial_test_body(node):
                        findings.append({"file": str(fp.relative_to(repo_root)), "function": node.name})
    return findings


def _detect_primary_language(repo_root) -> str:
    """
    Best-effort detection of the target repo's primary language, used to
    pick an appropriate static analysis tool. Only Python (via radon) is
    supported today; anything else falls back to "unknown" so callers can
    report "not available" instead of fabricating a number.
    """
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _LANGUAGE_SKIP_DIRS and not d.startswith(".")]
        if any(f.endswith(".py") for f in filenames):
            return "python"
    return "unknown"


def _compute_code_complexity(tool_context=None) -> Dict[str, Any]:
    """
    Computes the average cyclomatic complexity of the target repo via radon
    (Python only for now), rather than a fixed constant.
    """
    repo_root = _configured_repo_root(tool_context)
    language = _detect_primary_language(repo_root)

    if language != "python":
        return {
            "available": False,
            "code_complexity": None,
            "note": f"static analysis not available for language: {language}",
        }

    result = _run(
        ["radon", "cc", str(repo_root), "--total-average"],
        cwd=str(repo_root),
        tool_context=tool_context,
    )

    if result.get("status") == "error" and "returncode" not in result:
        # radon itself couldn't be started (e.g. not installed).
        return {
            "available": False,
            "code_complexity": None,
            "note": f"radon could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""
    match = _AVG_COMPLEXITY_RE.search(stdout)
    if not match:
        return {
            "available": False,
            "code_complexity": None,
            "note": "no average complexity reported by radon",
        }

    return {
        "available": True,
        "code_complexity": float(match.group(1)),
        "note": None,
    }


def _scan_security_vulnerabilities(tool_context=None) -> Dict[str, Any]:
    """
    Scans the target repo for security issues via bandit (Python only for
    now) and reports its real severity counts, rather than fabricating
    findings.
    """
    repo_root = _configured_repo_root(tool_context)
    language = _detect_primary_language(repo_root)

    if language != "python":
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": f"security scan not available for language: {language}",
        }

    result = _run(
        ["bandit", "-r", str(repo_root), "-f", "json", "-q"],
        cwd=str(repo_root),
        tool_context=tool_context,
    )

    if result.get("status") == "error" and "returncode" not in result:
        # bandit itself couldn't be started (e.g. not installed). Note this
        # is distinct from bandit running and exiting non-zero because it
        # found issues - that path still has JSON on stdout to parse below.
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": f"bandit could not be executed: {result.get('message', 'unknown error')}",
        }

    stdout = result.get("stdout", "") or ""
    try:
        report = json.loads(stdout) if stdout else None
    except ValueError:
        report = None

    if not isinstance(report, dict):
        return {
            "available": False,
            "vulnerability_scan_results": None,
            "note": "could not parse bandit output",
        }

    totals = report.get("metrics", {}).get("_totals", {})

    return {
        "available": True,
        "vulnerability_scan_results": {
            # bandit has no CRITICAL severity tier - real absence, not a
            # fabricated default.
            "critical": 0,
            "high": int(totals.get("SEVERITY.HIGH", 0)),
            "medium": int(totals.get("SEVERITY.MEDIUM", 0)),
            "low": int(totals.get("SEVERITY.LOW", 0)),
        },
        "note": None,
    }


def check_build(tool_context=None) -> Dict[str, Any]:
    """
    Actually installs the project's declared dependencies - the mechanical
    Definition-of-Done check (see spec-templates/DOD.md) for "the build
    runs", which QA must run for every story before it's accepted as Done.
    Catches the exact class of failure a real eval run hit (requirements.txt
    pinning SQLAlchemy==3.1.1, a version that doesn't exist - the app would
    never even install, let alone run) that code review alone missed.

    A REAL install, not `pip install --dry-run`/`npm install --dry-run` (an
    earlier version of this used those) - a dry run only resolves versions,
    it never actually installs anything, so a later real `pytest` run (see
    advance_story_stage's Tested gate/_execute_test_suite_coverage) could
    never import any third-party dependency regardless of what DevTeam did
    to the test file. A real eval run hit exactly this: QA and DevTeam
    bounced a story back and forth 9 times on an identical
    ModuleNotFoundError, since the true fix (an actual install, which no
    tool in the pipeline ever did) was never something either role could
    reach. Safe to install for real here: this runs inside the agent's own
    disposable container, not a developer's host machine - and it still
    catches an unresolvable pinned version exactly the same way a dry run
    did.

    Supports Python (requirements.txt) and Node (package.json) projects
    today; anything else is reported as "not checked" rather than a false
    pass or a hard block on stacks this can't verify.
    """
    repo_root = _configured_repo_root(tool_context)

    if (repo_root / "requirements.txt").exists():
        checked = "requirements.txt"
        cmd = ["pip", "install", "-r", "requirements.txt"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context)
    elif (repo_root / "package.json").exists():
        checked = "package.json"
        cmd = ["npm", "install"]
        result = _run(cmd, cwd=str(repo_root), tool_context=tool_context)
    else:
        result = {
            "status": "ok",
            "checked": None,
            "passing": None,
            "message": "No requirements.txt or package.json found - no recognized dependency manifest to check.",
        }
        if tool_context and getattr(tool_context, "state", None):
            tool_context.state["last_check_build"] = {
                "checked": None,
                "passing": None,
                "manifest_write_count_at_check": tool_context.state.get("dependency_manifest_write_count", 0),
            }
        return result

    passing = result.get("status") == "ok"
    # Persisted so advance_story_stage's "Tested" gate (ISSUE-0004) can
    # verify check_build actually ran and passed, instead of trusting QA's
    # own say-so that it did. manifest_write_count_at_check snapshots
    # write_file's dependency-manifest counter (docs.py) at the moment this
    # install actually ran - see the Tested gate's own freshness check for
    # why: a real eval run kept trusting a stale passing=True after
    # requirements.txt was rewritten (dropping Flask) with no re-run of
    # check_build in between.
    if tool_context and getattr(tool_context, "state", None):
        tool_context.state["last_check_build"] = {
            "checked": checked,
            "passing": passing,
            "manifest_write_count_at_check": tool_context.state.get("dependency_manifest_write_count", 0),
        }
    return {
        "status": "ok" if passing else "error",
        "checked": checked,
        "command": " ".join(cmd),
        "passing": passing,
        "output": ((result.get("stdout") or "") + (result.get("stderr") or ""))[-4000:],
    }


def _compute_say_do_ratio(tool_context=None) -> Dict[str, Any]:
    """
    Say-Do Ratio: how much of what this sprint committed to
    (ScrumState.sprint_backlog, Epics excluded - not a committed delivery
    unit themselves, same exclusion already used for the analysis harness's
    own accepted/stories_implemented series in run_eval_analysis.py) was
    actually delivered (reached Accepted) - a real ratio derived from
    session state, not a fixed placeholder.

    Previously hardcoded to 0.8 regardless of actual sprint outcome - a
    real eval run (0.1.0-run39) showed this exact fixed value in the
    report's KPI table next to 0 real stories accepted across every one of
    its 5 sprints, reading as "things are fine" when they weren't.
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return {"say_do_ratio": None, "note": "no session state available"}
    s = tool_context.state
    sprint_backlog = s.get("sprint_backlog", []) or []
    product_backlog = s.get("product_backlog", []) or []
    committed = [item for item in sprint_backlog if item.get("type") != "Epic"]
    if not committed:
        return {"say_do_ratio": None, "note": "no stories committed to this sprint yet"}

    from .requirements import _story_stages_completed
    delivered = sum(
        1 for item in committed
        if "Accepted" in _story_stages_completed(
            next((p for p in product_backlog if p.get("id") == item.get("id")), {}), item
        )
    )
    return {"say_do_ratio": round(delivered / len(committed), 2), "note": None}


def calculate_kpis(tool_context=None) -> Dict[str, Any]:
    """
    Calculates and returns a dictionary of quality KPIs.

    test_coverage/tests_run/tests_failed are derived from actually executing
    the target repo's test suite (US-0005). code_complexity is derived from
    a real static analysis tool (US-0006). vulnerability_scan_results is
    derived from a real security scan (US-0007). say_do_ratio is derived
    from the sprint's own committed-vs-accepted backlog (GH eval run39).

    commitment_reliability/customer_satisfaction/defect_escape_rate remain
    unavailable - no principled way to compute them exists in this system yet
    (no separate estimate-accuracy tracking, human satisfaction survey, or
    defect/bug-lifecycle data). GH issue #389: commitment_reliability/
    customer_satisfaction used to be hardcoded to 1.0/4.5 - plausible-looking
    fake numbers presented as real measurements in every sprint report,
    contradicting this exact docstring's own admission that no real
    computation exists. All three now consistently report unavailable
    (None + an explanatory note) rather than a fabricated constant - the
    same "never fabricate, report unavailable" rule already applied to
    maintainability/security below.
    """
    coverage_result = _execute_test_suite_coverage(tool_context)
    complexity_result = _compute_code_complexity(tool_context)
    security_result = _scan_security_vulnerabilities(tool_context)
    say_do_result = _compute_say_do_ratio(tool_context)

    maintainability = {
        "code_complexity": complexity_result["code_complexity"],
        "code_complexity_available": complexity_result["available"],
        "test_coverage": coverage_result["test_coverage"],
        "test_coverage_available": coverage_result["available"],
        "tests_run": coverage_result["tests_run"],
        "tests_failed": coverage_result["tests_failed"],
    }
    if complexity_result["note"]:
        maintainability["code_complexity_note"] = complexity_result["note"]
    if coverage_result["note"]:
        maintainability["test_coverage_note"] = coverage_result["note"]

    security = {
        "vulnerability_scan_available": security_result["available"],
        "vulnerability_scan_results": security_result["vulnerability_scan_results"],
    }
    if security_result["note"]:
        security["vulnerability_scan_note"] = security_result["note"]

    team_effectiveness = {
        "say_do_ratio": say_do_result["say_do_ratio"],
        # GH issue #389: previously hardcoded to 1.0 - no estimate-accuracy
        # tracking exists in this system to compute a real reliability score
        # from, so this was always a fabricated constant, never a
        # measurement. Reported unavailable instead, same rule as
        # defect_escape_rate below.
        "commitment_reliability": None,
        "commitment_reliability_note": "not available - no estimate-accuracy tracking exists yet",
    }
    if say_do_result["note"]:
        team_effectiveness["say_do_ratio_note"] = say_do_result["note"]

    return {
        "team_effectiveness": team_effectiveness,
        "result_quality": {
            # GH eval run39: no defect/bug-lifecycle tracking exists in this
            # system to compute a real escape rate from - reported
            # unavailable rather than swapped for a different, equally
            # fabricated constant (the same "never fabricate, report
            # unavailable" rule already applied to maintainability/security
            # below).
            "defect_escape_rate": None,
            "defect_escape_rate_note": "not available - no defect/bug-lifecycle tracking exists yet",
            # GH issue #389: previously hardcoded to 4.5 - no human
            # satisfaction survey or equivalent signal exists in this system,
            # so this was always a fabricated constant too.
            "customer_satisfaction": None,
            "customer_satisfaction_note": "not available - no user/customer satisfaction survey exists yet",
        },
        "maintainability": maintainability,
        "security": security,
        "prompt_context_usage": calculate_prompt_context_usage(),
    }

def update_sprint_report(kpis: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Adds the KPI dashboard to the sprint report.

    kpis should be the actual dict calculate_kpis() returns. Two malformed
    shapes seen in real eval runs are recovered automatically instead of
    erroring: the literal tool name ("calculate_kpis"/"calculate_kpis()"),
    self-healed by actually calling calculate_kpis() here; and a
    stringified dict (JSON or a Python repr with single quotes), recovered
    via _coerce_dict_arg. Anything else that still isn't a dict is a
    genuine caller error - rejected before it ever reaches state, since
    every later before_model_callback re-validates the whole state via
    ScrumState(**data) (see get_scrum_state in agent.py), and a bad
    sprint_report_kpis shape there crashes the *next* turn - regardless of
    which agent's - nowhere near this tool or this call.

    Bumps kpi_update_count (see create_sprint_report's kpi_baseline gate,
    tools/budget.py) - ISSUE-0046: across every real eval run so far,
    QualityGuardian was never once transferred to, since nothing in the
    SPRINT CLOSE SEQUENCE actually told another agent to hand off to it -
    the KPI trends in every report came back "never computed." The prompt
    alone saying this step matters wasn't enough, the same lesson
    create_sprint_report's retro_baseline gate already learned about the
    retrospective step - so a fresh call here is now mechanically required
    before create_sprint_report will complete, not just requested.
    """
    if isinstance(kpis, str) and kpis.strip() in _CALCULATE_KPIS_CALL_ALIASES:
        kpis = calculate_kpis(tool_context=tool_context)
    else:
        try:
            kpis = _coerce_dict_arg(kpis, "update_sprint_report")
        except ValueError:
            return {
                "status": "error",
                "message": (
                    f"kpis must be the actual KPI dictionary calculate_kpis() returns, not {kpis!r} - "
                    "call calculate_kpis() first and pass its returned object here, not a tool name."
                ),
            }
    if tool_context and hasattr(tool_context, "state"):
        tool_context.state["sprint_report_kpis"] = kpis
        tool_context.state["kpi_update_count"] = tool_context.state.get("kpi_update_count", 0) + 1
    return {"status": "ok", "kpis": kpis}
