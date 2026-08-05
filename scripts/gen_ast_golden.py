#!/usr/bin/env python3
"""Regenerate De-Emotion Phase D0 AST golden fixtures and monkeypatch inventory."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "ast_golden"
FIXTURES_DIR = ROOT / "tests" / "fixtures"

# (rel_path, class_name or "", function_name). Empty class_name = module function.
METHOD_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("core/engine/entry_exec.py", "_EntryExecMixin", "_execute_open"),
    ("core/engine/close_exec.py", "_CloseExecMixin", "_execute_close"),
    ("core/engine/sizing_gates.py", "_SizingGatesMixin", "_apply_mcp_directional_economic_gate"),
    ("core/order_mgmt/entry.py", "_EntryMixin", "open_position"),
    ("core/order_mgmt/closing.py", "_ClosingMixin", "close_position"),
    ("core/order_mgmt/closing.py", "_ClosingMixin", "_close_position_impl"),
    ("core/order_mgmt/monitor.py", "_MonitorMixin", "check_sl_tp"),
    ("core/scoring/entry_score.py", "", "score_coin"),
    ("core/scoring/entry_score.py", "", "score_coin_scalp"),
    ("core/scoring/portfolio.py", "", "algorithmic_portfolio"),
)

SAFETY_FILES: tuple[str, ...] = (
    "core/entry_policy.py",
    "core/kill_switch.py",
    "core/live_gate.py",
    "core/risk_manager.py",
    "core/promotion_gate.py",
)

MONOLITH_KEYS = ("bot_engine", "order_manager", "mcp_brain", "dashboard", "config")

MODULE_ALIASES: dict[str, str] = {
    "core.bot_engine": "bot_engine",
    "core.order_manager": "order_manager",
    "core.mcp_brain": "mcp_brain",
    "dashboard": "dashboard",
    "config": "config",
}


def _strip_locations(node: ast.AST) -> None:
    for child in ast.walk(node):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(child, attr):
                setattr(child, attr, None)


def _strip_docstring(func_def: ast.FunctionDef) -> None:
    if not func_def.body:
        return
    first = func_def.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            func_def.body = func_def.body[1:]


def _normalize_function(func_def: ast.FunctionDef) -> str:
    copied = ast.parse(ast.unparse(func_def)).body[0]
    assert isinstance(copied, ast.FunctionDef)
    _strip_docstring(copied)
    _strip_locations(copied)
    return ast.dump(copied, include_attributes=False)


def _find_class_method(
    tree: ast.Module, class_name: str, method_name: str
) -> ast.FunctionDef | None:
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == method_name:
                return item
    return None


def _find_module_function(tree: ast.Module, func_name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def extract_method_ast_dump(rel_path: str, class_name: str, method_name: str) -> dict[str, str]:
    path = ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if class_name:
        func = _find_class_method(tree, class_name, method_name)
        qualname = f"{class_name}.{method_name}"
    else:
        func = _find_module_function(tree, method_name)
        qualname = method_name
    if func is None:
        label = f"{class_name}.{method_name}" if class_name else method_name
        raise LookupError(f"{label} not found in {rel_path}")
    module = rel_path.replace("/", ".").removesuffix(".py")
    return {
        "module": module,
        "qualname": qualname,
        "ast_dump": _normalize_function(func),
    }


def _sha256_file(rel_path: str) -> str:
    data = (ROOT / rel_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def _register_import(aliases: dict[str, str], node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            asname = alias.asname or alias.name.split(".")[-1]
            if name in MODULE_ALIASES:
                aliases[asname] = MODULE_ALIASES[name]
            elif name in ("config", "dashboard"):
                aliases[asname] = name
    elif isinstance(node, ast.ImportFrom):
        if node.module == "core":
            for alias in node.names:
                mod = alias.name
                asname = alias.asname or alias.name
                if mod in ("bot_engine", "order_manager", "mcp_brain"):
                    aliases[asname] = mod
        elif node.module in MODULE_ALIASES:
            key = MODULE_ALIASES[node.module]
            for alias in node.names:
                asname = alias.asname or alias.name
                aliases[asname] = key
        elif node.module in ("config", "dashboard"):
            for alias in node.names:
                asname = alias.asname or alias.name
                aliases[asname] = node.module


def _import_alias_map(source: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return aliases
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _register_import(aliases, node)
    return aliases


def _add_symbol(bucket: dict[str, set[str]], monolith: str, symbol: str) -> None:
    if monolith in MONOLITH_KEYS and symbol:
        bucket.setdefault(monolith, set()).add(symbol)


def _scan_monkeypatch_inventory() -> dict[str, list[str]]:
    found: dict[str, set[str]] = {k: set() for k in MONOLITH_KEYS}
    string_pat = re.compile(
        r"""monkeypatch\.setattr\(\s*"""
        r"""["']((?:core\.(?:bot_engine|order_manager|mcp_brain)|config|dashboard))"""
        r"""\.([^"']+)["']""",
        re.MULTILINE,
    )
    config_obj_pat = re.compile(
        r"""monkeypatch\.setattr\(\s*config\s*,\s*["']([^"']+)["']""",
        re.MULTILINE,
    )
    sys_config_pat = re.compile(
        r"""monkeypatch\.setattr\(\s*sys\.modules\["config"\]\s*,\s*["']([^"']+)["']""",
        re.MULTILINE,
    )

    tests_dir = ROOT / "tests"
    for path in sorted(tests_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in string_pat.finditer(text):
            module_path, symbol = match.groups()
            monolith = MODULE_ALIASES.get(module_path, module_path.split(".")[-1])
            _add_symbol(found, monolith, symbol.split(".")[0])

        for match in config_obj_pat.finditer(text):
            _add_symbol(found, "config", match.group(1))

        for match in sys_config_pat.finditer(text):
            _add_symbol(found, "config", match.group(1))

        aliases = _import_alias_map(text)
        alias_pat = re.compile(
            r"""monkeypatch\.setattr\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*["']([^"']+)["']""",
            re.MULTILINE,
        )
        for match in alias_pat.finditer(text):
            alias, symbol = match.groups()
            monolith = aliases.get(alias)
            if monolith:
                _add_symbol(found, monolith, symbol)

    return {k: sorted(found[k]) for k in MONOLITH_KEYS}


def write_golden_files() -> list[Path]:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for rel_path, class_name, method_name in METHOD_TARGETS:
        payload = extract_method_ast_dump(rel_path, class_name, method_name)
        golden_name = (
            f"{class_name}_{method_name}.json"
            if class_name
            else f"{method_name}.json"
        )
        out = GOLDEN_DIR / golden_name
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append(out)
    return written


def write_safety_hashes() -> Path:
    payload = {rel: _sha256_file(rel) for rel in SAFETY_FILES}
    out = GOLDEN_DIR / "safety_hashes.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def write_monkeypatch_inventory() -> Path:
    payload = _scan_monkeypatch_inventory()
    out = FIXTURES_DIR / "monkeypatch_inventory.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: Iterable[str] | None = None) -> int:
    _ = argv
    golden = write_golden_files()
    safety = write_safety_hashes()
    inventory = write_monkeypatch_inventory()
    print(f"Wrote {len(golden)} AST golden files under {GOLDEN_DIR.relative_to(ROOT)}")
    print(f"Wrote safety hashes -> {safety.relative_to(ROOT)}")
    print(f"Wrote monkeypatch inventory -> {inventory.relative_to(ROOT)}")
    for key in MONOLITH_KEYS:
        count = len(json.loads(inventory.read_text(encoding="utf-8"))[key])
        print(f"  {key}: {count} patched symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
