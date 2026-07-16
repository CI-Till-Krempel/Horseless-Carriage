import unittest
import pydantic
import json
from unittest.mock import patch, MagicMock
from agents.scrum_team.tools.base import _run
from agents.scrum_team.tools.docs import read_doc
from pathlib import Path

class TestUnicodeSafety(unittest.TestCase):
    @patch("subprocess.run")
    def test_run_replaces_surrogates(self, mock_run):
        # We simulate a case where subprocess output would have surrogates if decoded with surrogateescape
        # but since we use errors="replace", they should be replaced by U+FFFD
        
        # In our case, we want to test that the string returned by _run DOES NOT have surrogates
        # and CAN be serialized by Pydantic.
        
        # Mocking subprocess.run to return a string that WOULD HAVE surrogates if we didn't handle it
        # Wait, if we mock it, we just return a string.
        # Let's mock it to return a string with a surrogate character to see if our code handles it.
        # Actually, our code calls subprocess.run with errors="replace".
        # So we should test that our call to subprocess.run INCLUDES errors="replace".
        
        mock_run.return_value = MagicMock(returncode=0, stdout="bad \udcc3 char", stderr="")
        
        res = _run(["ls"])
        
        # res["stdout"] will have the surrogate because we mocked it that way.
        # But in REALITY, subprocess.run(..., errors="replace") would have replaced it.
        
        # To truly test this, we'd need to mock the BYTES output and let subprocess.run decode it.
        # But we are mocking subprocess.run itself.
        
        # Let's check that the call was made with errors="replace"
        self.assertEqual(mock_run.call_args.kwargs.get("errors"), "replace")

    def test_pydantic_serialization_with_replacement_char(self):
        # The replacement character \ufffd is perfectly valid UTF-8
        good_str = "something \ufffd else"
        
        class MockEvent(pydantic.BaseModel):
            content: str
            
        event = MockEvent(content=good_str)
        # This should NOT raise UnicodeEncodeError
        json_str = event.model_dump_json()
        data = json.loads(json_str)
        self.assertEqual(data["content"], good_str)

    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.exists")
    def test_read_doc_uses_replace(self, mock_exists, mock_read_text):
        mock_exists.return_value = True
        mock_read_text.return_value = "content"
        
        # We need a real-ish path that passes the security checks in read_doc
        # or we just check if read_text was called with errors="replace"
        
        # We'll just call it and see
        with patch("agents.scrum_team.tools.docs._project_root", return_value=Path("/app")):
            with patch("agents.scrum_team.tools.docs._configured_repo_root", return_value=Path("/app/repo")):
                 read_doc("spec-templates/test.md")
        
        self.assertEqual(mock_read_text.call_args.kwargs.get("errors"), "replace")

if __name__ == "__main__":
    unittest.main()
