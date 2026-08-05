"""Environment bootstrap — load_dotenv once."""
import os

from dotenv import load_dotenv

# Test isolation hook: subprocess-based safety-default tests must be able to
# validate SOURCE defaults without the operator's real .env leaking in.
if os.getenv("PYTHON_DOTENV_DISABLED", "").strip().lower() not in ("1", "true", "yes"):
    load_dotenv()
