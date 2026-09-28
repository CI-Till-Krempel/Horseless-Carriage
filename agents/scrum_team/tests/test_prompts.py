# agents/scrum_team/tests/test_prompts.py
import unittest

from agents.scrum_team.prompts import (
    ORCHESTRATOR_PROMPT,
    PO_PROMPT,
    SM_PROMPT,
    DEV_PROMPT,
    QA_PROMPT,
    ARCH_PROMPT,
    QUALITY_GUARDIAN_PROMPT,
    ROLE_NAMES,
    _load_role_prompt,
    load_role_identity_default,
)


class TestPromptModuleAssembly(unittest.TestCase):
    """
    Acceptance Criteria: every role's system prompt is guardrails.md +
    workflow.md, concatenated in that order (guardrails first, since they
    take precedence over everything after them) - never a single hand-
    edited blob. See prompts.py's own module docstring for why identity.md
    is deliberately NOT part of this static assembly.
    """

    ALL_PROMPTS = [
        (ORCHESTRATOR_PROMPT, "ScrumOrchestrator"),
        (PO_PROMPT, "ProductOwner"),
        (SM_PROMPT, "ScrumMaster"),
        (DEV_PROMPT, "DevTeam"),
        (QA_PROMPT, "QA"),
        (ARCH_PROMPT, "Architect"),
        (QUALITY_GUARDIAN_PROMPT, "QualityGuardian"),
    ]

    def test_every_role_name_has_a_loadable_prompt(self):
        for role in ROLE_NAMES:
            with self.subTest(role=role):
                prompt = _load_role_prompt(role)
                self.assertIn("GUARDRAILS", prompt)
                self.assertIn("WORKFLOW", prompt)

    def test_guardrails_precede_workflow_in_every_role(self):
        for prompt, role in self.ALL_PROMPTS:
            with self.subTest(role=role):
                self.assertLess(prompt.index("GUARDRAILS ("), prompt.index("WORKFLOW ("))

    def test_every_role_states_the_customization_precedence_rule(self):
        """The core anti-override defense: every role's own prompt must
        state, in its own words, that project customization can never
        override the guardrails/workflow above it."""
        for prompt, role in self.ALL_PROMPTS:
            with self.subTest(role=role):
                self.assertIn("take absolute precedence", prompt)
                self.assertIn("CUSTOMIZATION loaded from the product/", prompt)

    def test_unknown_role_raises(self):
        with self.assertRaises(FileNotFoundError):
            _load_role_prompt("NotARealRole")

    def test_every_role_has_a_default_identity_file(self):
        prompts_by_role = {role: prompt for prompt, role in self.ALL_PROMPTS}
        for role in ROLE_NAMES:
            with self.subTest(role=role):
                content = load_role_identity_default(role)
                self.assertTrue(content.strip())
                # identity.default.md is never concatenated into the static
                # prompts above - confirm it's genuinely absent from them.
                self.assertNotIn(content.strip(), prompts_by_role[role])


class TestSelfTransferWarning(unittest.TestCase):
    """
    Acceptance Criteria: a real eval run showed multiple roles repeatedly
    calling transfer_to_agent with their own agent_name - agent.py's
    log_tool_invocation_callback mechanically rejects this (and escalates
    to the transfer-loop breaker after repeated attempts), but the model
    should ideally never try in the first place. Every role's own system
    prompt must state its own exact internal agent_name (agent.py's
    LlmAgent name= values) explicitly, so the model is aware before ever
    calling the tool - not just after being rejected.
    """

    def test_every_role_prompt_warns_against_its_own_exact_agent_name(self):
        cases = [
            (ORCHESTRATOR_PROMPT, "ScrumOrchestrator"),
            (PO_PROMPT, "ProductOwner"),
            (SM_PROMPT, "ScrumMaster"),
            (DEV_PROMPT, "DevTeam"),
            (QA_PROMPT, "QA"),
            (ARCH_PROMPT, "Architect"),
            (QUALITY_GUARDIAN_PROMPT, "QualityGuardian"),
        ]
        for prompt, agent_name in cases:
            with self.subTest(agent_name=agent_name):
                self.assertIn("NEVER call transfer_to_agent", prompt)
                self.assertIn(f'agent_name="{agent_name}"', prompt)


if __name__ == "__main__":
    unittest.main()
