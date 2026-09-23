
import unittest
import os
import requests
import json
import time
from agents.scrum_team.tools.budget import create_litellm_virtual_key
from agents.scrum_team.state import ScrumState

class TestLiteLLMIntegration(unittest.TestCase):
    def setUp(self):
        self.master_key = os.environ.get("LITELLM_MASTER_KEY")
        self.proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://litellm:4000")
        self.budget_id = "scrum-sprint-budget"
        
        if not self.master_key:
            self.skipTest("LITELLM_MASTER_KEY not set")

    def test_key_creation_and_usage(self):
        # 0. Wait for proxy to be ready
        print("Waiting for proxy to be ready...")
        for _ in range(10):
            try:
                requests.get(f"{self.proxy_base}/health/readiness", timeout=2)
                break
            except:
                time.sleep(1)
        
        # 1. Create a virtual key
        tool_context = MagicMock()
        tool_context.state = ScrumState().model_dump()
        tool_context.state["budgets"]["total_usd"] = 1.0
        
        print("Creating virtual key...")
        import uuid
        test_agent_name = f"IntegrationTestAgent_{uuid.uuid4().hex[:8]}"
        res = create_litellm_virtual_key(
            test_agent_name, 
            models=["scrum-orchestrator", "scrum-test-mock"], 
            tool_context=tool_context
        )
        if res["status"] != "ok":
            print(f"Error creating key: {res.get('message')}")
        self.assertEqual(res["status"], "ok")
        agent_key = res["key"]
        print(f"Key created: {agent_key[:10]}...")

        # 1.1 Verify key info
        print("Verifying key info...")
        key_info_resp = requests.get(
            f"{self.proxy_base}/key/info",
            headers={"Authorization": f"Bearer {self.master_key}"},
            params={"key": agent_key},
            timeout=5
        )
        key_info_resp.raise_for_status()
        info = key_info_resp.json().get("info", {})
        print(f"Key info budget_id: {info.get('budget_id')}")
        self.assertEqual(info.get('budget_id'), self.budget_id)
        
        # 2. Get initial spend - via this key's own /key/info, not
        # /budget/info for the shared budget_id. Confirmed by a real
        # incident (sprint reports always showed $0.00 actual spend while
        # the LiteLLM dashboard showed the true numbers for the same
        # sprint): LiteLLM tracks spend per-key, not as an aggregate on the
        # shared Budget policy object - /budget/info's own "spend" field,
        # if present at all, never reflected this key's real usage. See
        # check_cost_budget_callback (agent.py) for the production fix this
        # mirrors.
        print("Fetching initial spend...")
        initial_spend = requests.get(
            f"{self.proxy_base}/key/info",
            headers={"Authorization": f"Bearer {self.master_key}"},
            params={"key": agent_key},
            timeout=5,
        ).json().get("info", {}).get("spend", 0.0)
        print(f"Initial spend: {initial_spend}")

        # 3. Make a call using the virtual key
        print("Making LLM call through proxy with Mock-Response header...")
        call_resp = requests.post(
            f"{self.proxy_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {agent_key}",
                "Content-Type": "application/json",
                "LiteLLM-Proxy-Mock-Response": "Integration test mock response"
            },
            json={
                "model": "scrum-test-mock",
                "messages": [{"role": "user", "content": "Say hello"}]
            },
            timeout=10
        )
        print(f"Call response status: {call_resp.status_code}")
        if call_resp.status_code != 200:
            print(f"Error: {call_resp.text}")
        self.assertEqual(call_resp.status_code, 200)
        
        # Wait a bit for spend to be recorded (it's often async in LiteLLM)
        time.sleep(2)

        # 4. Check spend again - same /key/info source as step 2.
        print("Fetching final spend...")
        final_spend = requests.get(
            f"{self.proxy_base}/key/info",
            headers={"Authorization": f"Bearer {self.master_key}"},
            params={"key": agent_key},
            timeout=5,
        ).json().get("info", {}).get("spend", 0.0)
        print(f"Final spend: {final_spend}")

        self.assertGreater(
            final_spend, initial_spend,
            "the mock LLM call above should have recorded real spend against this key",
        )

from unittest.mock import MagicMock
if __name__ == "__main__":
    unittest.main()
