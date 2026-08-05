#!/usr/bin/env python3
"""Point structural bot_engine source greps at the D5 engine package."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

IMPORT = "from tests.bot_engine_source import bot_engine_source_for_grep\n"
HELPER = "bot_engine_source_for_grep()"

PATTERNS = [
    re.compile(
        r'Path\(\s*"core/bot_engine\.py"\s*\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'Path\(\s*ROOT\s*/\s*"core"\s*/\s*"bot_engine\.py"\s*\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'\(\s*ROOT\s*/\s*"core"\s*/\s*"bot_engine\.py"\s*\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'\(\s*Path\(__file__\)\.resolve\(\)\.parents\[1\]\s*/\s*"core"\s*/\s*"bot_engine\.py"\s*\)'
        r'\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'\(\s*repo\s*/\s*"core"\s*/\s*"bot_engine\.py"\s*\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'Path\("core",\s*"bot_engine\.py"\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'\(\s*Path\(__file__\)\.resolve\(\)\.parent\s*/\s*"core"\s*/\s*"bot_engine\.py"\s*\)'
        r'\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'\(\s*Path\("core/bot_engine\.py"\)\.resolve\(\)\s*\)\.read_text\(\s*encoding\s*=\s*"utf-8"\s*\)'
    ),
    re.compile(
        r'Path\("core/bot_engine\.py"\)\.read_text\(\)'
    ),
]


def patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "bot_engine_source_for_grep" in text:
        return False
    original = text
    for pat in PATTERNS:
        text = pat.sub(HELPER, text)
    if text == original:
        return False
    if IMPORT.strip() not in text:
        if 'from __future__ import annotations' in text:
            text = text.replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n\n" + IMPORT,
                1,
            )
        else:
            text = IMPORT + "\n" + text
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    count = 0
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in ("bot_engine_source.py", "patch_bot_engine_greps.py"):
            continue
        if patch_file(path):
            print("patched", path.relative_to(ROOT))
            count += 1
    print(f"done: {count} files")


if __name__ == "__main__":
    main()
