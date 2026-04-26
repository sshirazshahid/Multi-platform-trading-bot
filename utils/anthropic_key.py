"""
Patch for claude_analyst.py — reads ANTHROPIC_API_KEY from .env
and injects it into the request headers automatically.

This is already integrated in claude_analyst.py via the helper below.
"""

import os
from pathlib import Path

def get_anthropic_key() -> str:
    """Read key from env or .env file."""
    # Already in environment
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # Try reading .env directly
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip()
                val = val.strip('"').strip("'")
                if val:
                    return val
    return ""
