"""
core/claude_schemas.py — strict schemas for the ClaudeCode advisory tasks.

NOTE (doc-rot fix 2026-06-12, per the 2026-06-07 wiring audit): system-wide,
Claude is no longer advisory-only — the LIVE decision path is Claude-PRIMARY
with algorithmic fallback (core/mcp_brain.py), and it does not route through
these schemas. The spec Appendix A constraints below apply to the
claude_advisor tasks these schemas gate (outside the live decision path):
within them, Claude cannot place/modify/cancel orders, override stops, change
leverage, move funds, or self-modify. Every response goes through
`validate(task, response)` — if the shape doesn't match exactly, the response
is rejected, logged to `data/claude_schema_failures.jsonl`, and the bot
behaves as if no review happened.

This file intentionally uses no third-party JSON-schema library. We are
describing four small, flat response shapes; a ~100-line explicit validator
is faster, has zero dependencies, and leaves no ambiguity about what is
allowed or disallowed.

Guardrails:
 - Unknown top-level keys are rejected (no sneaking in 'override_stop' etc.).
 - Enums are strict — only the literal strings in the schema pass.
 - Scalar bounds (confidence ranges) are enforced.
 - Arrays may be empty but must be lists.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

FAILURE_LOG = Path("data/claude_schema_failures.jsonl")


# ── The four allowed tasks (spec §10, Appendix A) ────────────────────

HISTORICAL_ANNOTATION = {
    "required": {"decision", "confidence", "reason_labels", "commentary"},
    "optional": set(),
    "types": {
        "decision":      ("enum", {"GOOD", "ACCEPTABLE", "BAD", "INCONCLUSIVE"}),
        "confidence":    ("int_range", 1, 5),
        "reason_labels": ("list_str", 0, 16),
        "commentary":    ("str_max", 2000),
    },
}

PRE_TRADE_REVIEW = {
    "required": {"decision", "confidence", "red_flags", "commentary"},
    "optional": set(),
    "types": {
        "decision":   ("enum", {"ALLOW", "SKIP", "REVIEW"}),
        "confidence": ("int_range", 1, 5),
        "red_flags":  ("list_str", 0, 16),
        "commentary": ("str_max", 2000),
    },
}

WEEKLY_DIAGNOSTIC = {
    "required": {
        "top_loss_drivers", "top_win_drivers",
        "symbols_to_disable", "setup_families_to_review",
        "exchange_health_notes", "summary",
    },
    "optional": set(),
    "types": {
        "top_loss_drivers":         ("list_str", 0, 16),
        "top_win_drivers":          ("list_str", 0, 16),
        "symbols_to_disable":       ("list_str", 0, 32),
        "setup_families_to_review": ("list_str", 0, 16),
        "exchange_health_notes":    ("list_str", 0, 16),
        "summary":                  ("str_max", 4000),
    },
}

DRIFT_DETECTION = {
    "required": {
        "drift_level", "mismatched_features",
        "commentary", "recommended_action",
    },
    "optional": set(),
    "types": {
        "drift_level":          ("enum", {"LOW", "MEDIUM", "HIGH"}),
        "mismatched_features":  ("list_str", 0, 32),
        "commentary":           ("str_max", 2000),
        "recommended_action":   ("enum", {"NONE", "WATCH", "PAUSE_SYMBOL", "PAUSE_FAMILY", "FORCE_OBSERVATION"}),
    },
}

SCHEMAS = {
    "historical_annotation": HISTORICAL_ANNOTATION,
    "pre_trade_review":      PRE_TRADE_REVIEW,
    "weekly_diagnostic":     WEEKLY_DIAGNOSTIC,
    "drift_detection":       DRIFT_DETECTION,
}


# ── Validator ────────────────────────────────────────────────────────

def _check_field(name: str, value: Any, rule: tuple) -> str | None:
    """Return None on pass, or an error string."""
    kind = rule[0]
    if kind == "enum":
        allowed = rule[1]
        if value not in allowed:
            return f"{name}={value!r} not in {sorted(allowed)}"
        return None
    if kind == "int_range":
        lo, hi = rule[1], rule[2]
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be int, got {type(value).__name__}"
        if not (lo <= value <= hi):
            return f"{name}={value} outside [{lo}, {hi}]"
        return None
    if kind == "list_str":
        lo, hi = rule[1], rule[2]
        if not isinstance(value, list):
            return f"{name} must be list, got {type(value).__name__}"
        if not (lo <= len(value) <= hi):
            return f"{name} length {len(value)} outside [{lo}, {hi}]"
        for i, item in enumerate(value):
            if not isinstance(item, str):
                return f"{name}[{i}] must be str, got {type(item).__name__}"
        return None
    if kind == "str_max":
        max_len = rule[1]
        if not isinstance(value, str):
            return f"{name} must be str, got {type(value).__name__}"
        if len(value) > max_len:
            return f"{name} length {len(value)} > {max_len}"
        return None
    return f"{name}: unknown rule kind {kind!r}"


def validate(task: str, response: Any) -> dict | None:
    """Return the response dict if it matches the schema, else None.

    Invalid responses are logged to `data/claude_schema_failures.jsonl` with
    enough context to debug and tighten the prompts if Claude drifts.
    """
    if task not in SCHEMAS:
        _fail(task, response, f"unknown task {task!r}")
        return None

    if not isinstance(response, dict):
        _fail(task, response, f"response must be a dict, got {type(response).__name__}")
        return None

    schema = SCHEMAS[task]
    allowed_keys = schema["required"] | schema["optional"]

    # Unknown keys are rejected — no 'suggested_leverage', no 'override_stop'.
    extra = set(response.keys()) - allowed_keys
    if extra:
        _fail(task, response, f"unknown keys rejected: {sorted(extra)}")
        return None

    # Missing required keys.
    missing = schema["required"] - set(response.keys())
    if missing:
        _fail(task, response, f"missing required keys: {sorted(missing)}")
        return None

    # Type + bounds checks.
    for name, rule in schema["types"].items():
        if name not in response:
            continue
        err = _check_field(name, response[name], rule)
        if err:
            _fail(task, response, err)
            return None

    return response


def _fail(task: str, response: Any, error: str) -> None:
    """Append a failure record to the schema-failure log."""
    entry = {
        "ts":       time.time(),
        "task":     task,
        "error":    error,
        "response": response if isinstance(response, (dict, list, str, int, float, bool)) or response is None else repr(response),
    }
    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FAILURE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.debug(f"[ClaudeSchema] failure-log write error: {e}")
    logger.warning(f"[ClaudeSchema] REJECTED {task}: {error}")
