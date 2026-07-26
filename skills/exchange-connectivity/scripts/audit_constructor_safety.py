#!/usr/bin/env python3
"""Fail when exchange construction can reach venue-account mutations.

The check is intentionally narrow: it follows ``self.<method>()`` calls from
``__init__`` and ``_init_exchange`` within each class and flags known order or
account mutation calls. It is a guardrail, not a proof of side-effect freedom.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

MUTATING_METHODS = {
    "borrow_margin",
    "cancel_all_orders",
    "cancel_order",
    "cancel_orders",
    "close_position",
    "create_limit_order",
    "create_market_order",
    "create_order",
    "create_orders",
    "edit_order",
    "repay_margin",
    "set_leverage",
    "set_margin_mode",
    "set_position_mode",
    "set_risk_limit",
    "set_trading_stop",
    "transfer",
    "withdraw",
}

ROOT_METHODS = {"__init__", "_init_exchange"}


def _call_name(node: ast.Call) -> str:
    current = node.func
    parts: List[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _self_call(node: ast.Call) -> Optional[str]:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    ):
        return func.attr
    return None


def _is_mutation(call_name: str) -> bool:
    leaf = call_name.rsplit(".", 1)[-1]
    normalized = leaf.replace("_", "").lower()
    if leaf in MUTATING_METHODS:
        return True
    # Raw ccxt implicit API methods encode HTTP verbs in names, for example
    # fapiPrivatePostPositionSideDual. Private GET reads remain allowed.
    return any(token in normalized for token in ("privatepost", "privateput", "privatedelete"))


def audit_source(source: str, path: str = "<memory>") -> List[Dict[str, object]]:
    tree = ast.parse(source, filename=path)
    findings: List[Dict[str, object]] = []
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        methods = {
            node.name: node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        pending = [name for name in ROOT_METHODS if name in methods]
        reachable: Set[str] = set()
        while pending:
            method_name = pending.pop()
            if method_name in reachable:
                continue
            reachable.add(method_name)
            method = methods[method_name]
            for node in ast.walk(method):
                if not isinstance(node, ast.Call):
                    continue
                target = _self_call(node)
                if target in methods and target not in reachable:
                    pending.append(target)

        for method_name in sorted(reachable):
            method = methods[method_name]
            for node in ast.walk(method):
                if not isinstance(node, ast.Call):
                    continue
                call_name = _call_name(node)
                if _is_mutation(call_name):
                    findings.append(
                        {
                            "path": path,
                            "line": node.lineno,
                            "class": class_node.name,
                            "reachable_from": method_name,
                            "call": call_name,
                            "reason": "venue mutation is reachable during adapter construction",
                        }
                    )
    return findings


def audit_files(paths: Iterable[Path], project_root: Path) -> Tuple[List[str], List[Dict[str, object]]]:
    checked: List[str] = []
    findings: List[Dict[str, object]] = []
    for path in sorted(set(path.resolve() for path in paths)):
        try:
            label = str(path.relative_to(project_root))
        except ValueError:
            label = str(path)
        checked.append(label)
        try:
            source = path.read_text(encoding="utf-8")
            findings.extend(audit_source(source, label))
        except (OSError, SyntaxError, UnicodeError) as exc:
            findings.append(
                {
                    "path": label,
                    "line": getattr(exc, "lineno", None),
                    "class": None,
                    "reachable_from": None,
                    "call": None,
                    "reason": "could not audit source: %s" % exc,
                }
            )
    return checked, findings


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Trading bot repository root")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("files", nargs="*", help="Optional Python files relative to project root")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print("project root is not a directory: %s" % project_root, file=sys.stderr)
        return 2

    if args.files:
        paths = [project_root / value for value in args.files]
    else:
        paths = list((project_root / "exchanges").glob("*.py"))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        print("missing input file(s): %s" % ", ".join(missing), file=sys.stderr)
        return 2

    checked, findings = audit_files(paths, project_root)
    result = {
        "valid": not findings,
        "checked": checked,
        "findings": findings,
        "scope": "constructor hooks and reachable same-class helpers",
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("constructor safety: %s" % ("PASS" if result["valid"] else "FAIL"))
        for finding in findings:
            print(
                "{path}:{line}: {call}: {reason}".format(
                    path=finding["path"],
                    line=finding["line"] or "?",
                    call=finding["call"] or "parse",
                    reason=finding["reason"],
                )
            )
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
