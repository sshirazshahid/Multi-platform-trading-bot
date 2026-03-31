"""
clear_cache.py — Remove Python __pycache__ directories.
Run before building an executable or when switching branches.
"""

import shutil
from pathlib import Path


def main():
    root    = Path(__file__).parent
    removed = 0
    for cache in root.rglob("__pycache__"):
        if "venv" in str(cache):
            continue
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    print(f"  Cleared {removed} __pycache__ director{'y' if removed==1 else 'ies'}.")


if __name__ == "__main__":
    main()
