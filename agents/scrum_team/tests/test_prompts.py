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
)


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
