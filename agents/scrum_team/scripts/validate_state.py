import sys
import json
import os
from pathlib import Path

# Add project root to sys.path to allow importing agents.scrum_team.state
sys.path.append(str(Path(__file__).resolve().parents[3]))

try:
    from agents.scrum_team.state import ScrumState
    from pydantic import ValidationError
except ImportError as e:
    print(f"Error importing required modules: {e}")
    sys.exit(1)

def validate_state_file(file_path):
    if not os.path.exists(file_path):
        print(f"ERROR: State file not found: {file_path}")
        return False

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to decode JSON from {file_path}: {e}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error reading {file_path}: {e}")
        return False

    try:
        # Validate using Pydantic
        state = ScrumState(**data)
        print(f"SUCCESS: State file {file_path} is valid. (Version: {state.version})")
        return True
    except ValidationError as e:
        print(f"ERROR: State file {file_path} failed validation:")
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error['loc'])
            print(f"  - {loc}: {error['msg']} ({error['type']})")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected validation error: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_state.py <path_to_state.json>")
        sys.exit(1)

    file_to_validate = sys.argv[1]
    if validate_state_file(file_to_validate):
        sys.exit(0)
    else:
        sys.exit(1)
