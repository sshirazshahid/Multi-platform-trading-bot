"""De-Emotion Phase D0 — AST equivalence harness and safety-file freeze."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "ast_golden"
INVENTORY_PATH = ROOT / "tests" / "fixtures" / "monkeypatch_inventory.json"
SAFETY_HASHES_PATH = GOLDEN_DIR / "safety_hashes.json"

EXPECTED_AUTHORIZE_SITES = 10

METHOD_GOLDENS: tuple[tuple[str, str, str], ...] = (
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

MONOLITH_KEYS = ("bot_engine", "order_manager", "mcp_brain", "dashboard", "config")


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


def _extract_method_payload(rel_path: str, class_name: str, method_name: str) -> dict[str, str]:
    path = ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if class_name:
        func = _find_class_method(tree, class_name, method_name)
        qualname = f"{class_name}.{method_name}"
    else:
        func = _find_module_function(tree, method_name)
        qualname = method_name
    assert func is not None, f"{qualname} missing in {rel_path}"
    module = rel_path.replace("/", ".").removesuffix(".py")
    return {
        "module": module,
        "qualname": qualname,
        "ast_dump": _normalize_function(func),
    }


def _production_py_files():
    for base in ("core", "exchanges", "strategies"):
        for p in (ROOT / base).rglob("*.py"):
            if "test" in p.parts:
                continue
            yield p


def test_safety_file_hashes_frozen():
    assert SAFETY_HASHES_PATH.is_file(), "run scripts/gen_ast_golden.py first"
    expected = json.loads(SAFETY_HASHES_PATH.read_text(encoding="utf-8"))
    for rel, digest in expected.items():
        data = (ROOT / rel).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        assert actual == digest, f"safety file changed: {rel}"


@pytest.mark.parametrize(
    "rel_path,class_name,method_name",
    METHOD_GOLDENS,
    ids=[
        f"{c}_{m}" if c else m for _, c, m in METHOD_GOLDENS
    ],
)
def test_ast_equivalence(rel_path: str, class_name: str, method_name: str):
    golden_name = (
        f"{class_name}_{method_name}.json"
        if class_name
        else f"{method_name}.json"
    )
    golden_path = GOLDEN_DIR / golden_name
    assert golden_path.is_file(), f"missing golden {golden_path.name}; run gen_ast_golden.py"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    live = _extract_method_payload(rel_path, class_name, method_name)
    assert live["module"] == golden["module"]
    assert live["qualname"] == golden["qualname"]
    assert live["ast_dump"] == golden["ast_dump"], (
        f"AST drift in {golden['qualname']}; regenerate only after intentional refactor"
    )


def test_authorize_runtime_entry_census():
    call_pat = re.compile(r"\bauthorize_runtime_entry\s*\(")
    bind_pat = re.compile(r"\bauthorize_runtime_entry\s+if\s+entry_authorizer")
    hits = []
    for p in _production_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "def authorize_runtime_entry" in line:
                continue
            if "from core.entry_policy import" in line and "authorize_runtime_entry" in line:
                continue
            if "import authorize_runtime_entry" in line:
                continue
            if call_pat.search(line) or bind_pat.search(line):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    assert len(hits) == EXPECTED_AUTHORIZE_SITES, (
        f"expected {EXPECTED_AUTHORIZE_SITES} authorize sites, found {len(hits)}:\n"
        + "\n".join(hits)
    )


def test_monkeypatch_inventory_present():
    assert INVENTORY_PATH.is_file(), "run scripts/gen_ast_golden.py first"
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    for key in MONOLITH_KEYS:
        assert key in inventory, f"monkeypatch inventory missing key: {key}"
        assert isinstance(inventory[key], list)
        assert inventory[key], f"monkeypatch inventory empty for {key}"
