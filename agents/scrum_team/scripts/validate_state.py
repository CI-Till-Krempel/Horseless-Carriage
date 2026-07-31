import sys
import json
import os
from pathlib import Path

# Add project root to sys.path to allow importing agents.scrum_team.state
sys.path.append(str(Path(__file__).resolve().parents[3]))

# Exit codes matter here, not just "zero vs nonzero" - check_state_repo.py
# (GH issue #109) uses this specific code to decide whether to offer a
# repair/reset/delete menu, which must only happen for genuine content
# corruption (bad JSON, or JSON that doesn't match the schema) - never for
# an environment/dependency problem (missing pydantic, Docker daemon down
# before this script even ran) that has nothing to do with the file's
# actual content. USAGE_ERROR_EXIT_CODE (1) covers everything else:
# ImportError, a missing file, an unexpected exception, or a bad CLI
# invocation - "couldn't validate", not "validated and found corrupt".
CORRUPTION_EXIT_CODE = 3
USAGE_ERROR_EXIT_CODE = 1

try:
    from agents.scrum_team.state import ScrumState
    from pydantic import ValidationError
except ImportError as e:
    print(f"Error importing required modules: {e}")
    sys.exit(USAGE_ERROR_EXIT_CODE)


def validate_state_file(file_path):
    """Returns (ok, corrupted): ok is True only on successful validation;
    when ok is False, corrupted distinguishes "the content itself is bad"
    (bad JSON, or JSON that fails the ScrumState schema) from any other
    failure to validate at all."""
    if not os.path.exists(file_path):
        print(f"ERROR: State file not found: {file_path}")
        return False, False

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to decode JSON from {file_path}: {e}")
        return False, True
    except Exception as e:
        print(f"ERROR: Unexpected error reading {file_path}: {e}")
        return False, False

    try:
        # Validate using Pydantic
        state = ScrumState(**data)
        print(f"SUCCESS: State file {file_path} is valid. (Version: {state.version})")
        return True, False
    except ValidationError as e:
        print(f"ERROR: State file {file_path} failed validation:")
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error['loc'])
            print(f"  - {loc}: {error['msg']} ({error['type']})")
        return False, True
    except Exception as e:
        print(f"ERROR: Unexpected validation error: {e}")
        return False, False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_state.py <path_to_state.json>")
        sys.exit(USAGE_ERROR_EXIT_CODE)

    file_to_validate = sys.argv[1]
    ok, corrupted = validate_state_file(file_to_validate)
    if ok:
        sys.exit(0)
    sys.exit(CORRUPTION_EXIT_CODE if corrupted else USAGE_ERROR_EXIT_CODE)
