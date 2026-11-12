"""
Regression test: `.github/workflows/eval.yml`'s checkout step resolves
`${{ github.event.inputs.branch || github.ref }}` - a real run (triggered via
the Actions "Run workflow" dialog, selecting a non-main branch from the
top-level "Use workflow from" selector) still evaluated `main`, because the
custom `branch` dispatch input's own default was hardcoded to the literal
string `"main"`. GitHub Actions always sends a workflow_dispatch input's
default value when the field is left blank in the dispatch form, so
`inputs.branch` was never falsy and always won the `||`, silently discarding
whatever branch the user actually selected via "Use workflow from". The input
default must stay empty so a blank custom field falls through to `github.ref`
(the real dispatch-selected branch).
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestEvalWorkflowDispatchBranchInput:
    def test_branch_input_default_is_not_hardcoded_to_main(self):
        data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8"))
        # PyYAML's default loader treats a bare `on:` key as the boolean
        # True (YAML 1.1 `on`/`off` alias) - look it up either way so this
        # doesn't depend on which PyYAML version/loader is in use.
        on_section = data.get("on", data.get(True))
        branch_input = on_section["workflow_dispatch"]["inputs"]["branch"]
        assert branch_input.get("default", "") == "", (
            "the 'branch' workflow_dispatch input must default to empty, not a hardcoded branch "
            "name - the checkout step's `github.event.inputs.branch || github.ref` only falls "
            "through to the actually-dispatched-against branch (github.ref) when this input is "
            "falsy; a non-empty default always wins and silently ignores the 'Use workflow from' "
            "selector."
        )

    def test_checkout_step_falls_through_to_github_ref(self):
        text = (REPO_ROOT / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8")
        assert "ref: ${{ github.event.inputs.branch || github.ref }}" in text
