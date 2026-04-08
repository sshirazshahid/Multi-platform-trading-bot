"""
setup.py — Quick setup wizard for first-time configuration.
Run once after installing dependencies.

Usage: python setup.py
"""

import shutil
from pathlib import Path


def main():
    print("\n" + "=" * 55)
    print("  TRADING BOT — FIRST TIME SETUP")
    print("=" * 55)

    env_path     = Path(".env")
    example_path = Path(".env.example")
    if not env_path.exists() and example_path.exists():
        shutil.copy(example_path, env_path)
        print("OK  Created .env from .env.example")
        print("    >> Open .env and fill in your API keys before running the bot!")
    elif env_path.exists():
        print("    .env already exists — skipping")

    for folder in ["logs", "data"]:
        Path(folder).mkdir(exist_ok=True)
        print(f"OK  Created /{folder} directory")

    gitignore_path = Path(".gitignore")
    gitignore_content = """.env
*.pyc
__pycache__/
logs/
data/
*.egg-info/
.venv/
venv/
dist/
build/
"""
    if not gitignore_path.exists():
        gitignore_path.write_text(gitignore_content)
        print("OK  Created .gitignore")
    else:
        print("    .gitignore already exists — skipping")

    print("\n" + "=" * 55)
    print("  NEXT STEPS")
    print("=" * 55)
    print("""
  1. Edit .env with your API keys (Option 5 in launcher)
  2. Keep DRY_RUN=true until you are confident
  3. Run a backtest first (Option 3 in launcher)
  4. Start the bot (Option 2 in launcher)
""")


if __name__ == "__main__":
    main()
