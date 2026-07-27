"""
Shared .env file helpers for Horseless Carriage's host-side setup/doctor
scripts. Stdlib-only (no pip install required) so these scripts work before
`requirements.txt` has ever been installed, on macOS/Linux/Windows alike.

Edits preserve the rest of the file (comments, ordering, blank lines) by
substituting/appending a single "KEY=..." line via regex, mirroring how a
human would hand-edit a .env file.
"""

import re
import secrets
from pathlib import Path


def is_placeholder(value: str) -> bool:
    """True if empty or still a "<...>" placeholder like "<your_api_key>"."""
    if not value:
        return True
    return bool(re.match(r"^<.*>$", value))


def read_env_var(path: Path, key: str) -> str:
    """Reads a single KEY="value" (or KEY=value) line; "" if missing/not found."""
    if not Path(path).is_file():
        return ""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(rf'^{re.escape(key)}=\"?([^"\n]*)\"?$', text, re.MULTILINE)
    return m.group(1) if m else ""


def update_env_var(path: Path, key: str, value: str) -> None:
    """Sets KEY="value" in the file, replacing an existing line or appending one."""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f'{key}="{value}"'
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text, encoding="utf-8")


def gen_secret() -> str:
    return secrets.token_hex(24)


def ensure_master_key(path: Path) -> None:
    """Generates a real LITELLM_MASTER_KEY (and matching LITELLM_PROXY_API_KEY)
    if the current value is missing or still a placeholder."""
    current = read_env_var(path, "LITELLM_MASTER_KEY")
    if is_placeholder(current):
        new_key = gen_secret()
        update_env_var(path, "LITELLM_MASTER_KEY", new_key)
        update_env_var(path, "LITELLM_PROXY_API_KEY", new_key)
        print(">> Generated a new LITELLM_MASTER_KEY (and set LITELLM_PROXY_API_KEY to match).")


def load_env_file(path: Path) -> dict:
    """Parses a .env file into a dict, for read-only inspection (e.g. doctor.py).
    Does not mutate os.environ or the file; does not evaluate shell syntax."""
    result = {}
    p = Path(path)
    if not p.is_file():
        return result
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result
