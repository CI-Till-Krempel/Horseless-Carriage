# agents/scrum_team/helpers.py
import os

def get_process_overhead_percentage() -> float:
    """Gets the process overhead percentage from environment variables."""
    return float(os.getenv("PROCESS_OVERHEAD_PERCENTAGE", "10.0"))
