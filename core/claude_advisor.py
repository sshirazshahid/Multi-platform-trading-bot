"""
core/claude_advisor.py — advisory-only ClaudeCode wrapper (spec §5, §9, §10).

Every Claude call goes through `call_advisory(task, payload)`:

  1. Load the task prompt template from prompts/claude/<task>.md
  2. Substitute `{{KEY}}` placeholders from payload
  3. Call the Claude CLI via utils.claude_client (strips ANTHROPIC_API_KEY
     so the call uses the Max subscription, not API credits)
  4. Extract the first JSON object from the response
  5. Validate against core.claude_schemas.SCHEMAS[task]
  6. On validation failure: return None, log to claude_schema_failures.jsonl
  7. On success: record the validated response in the warehouse

No fallback ever uses an unvalidated response. Rejected output is dead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from core.claude_schemas import validate as _validate
from utils.claude_client import call_claude_cli

PROMPT_DIR = Path("prompts/claude")


def _load_template(task: str) -> str | None:
    path = PROMPT_DIR / f"{task}.md"
    if not path.exists():
        logger.warning(f"[ClaudeAdvisor] missing prompt template: {path}")
        return None
    return path.read_text(encoding="utf-8")


def _substitute(template: str, payload: dict) -> str:
    """Replace {{KEY}} placeholders with JSON-serialized payload values.

    Keys are matched case-insensitively. Unknown placeholders are left as-is
    so a mismatch is obvious in the Claude output rather than silently filled.
    """
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        for k, v in payload.items():
            if k.lower() == key.lower():
                if isinstance(v, str):
                    return v
                return json.dumps(v, default=str, indent=2)
        return match.group(0)

    return re.sub(r"\{\{\s*([A-Z_]+)\s*\}\}", repl, template)


def _extract_json_object(text: str) -> Any:
    """Return the first top-level JSON object found in `text`, or None."""
    if not text:
        return None
    text = text.strip()
    # Strip code fences if Claude added them despite our instructions
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: grab the first {...} span
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def call_advisory(
    task: str,
    payload: dict,
    *,
    model: str = "sonnet",
    effort: str = "low",
    timeout: int = 120,
    warehouse=None,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> dict | None:
    """Run a single advisory Claude call end-to-end.

    Returns the validated response dict on success, or None on any failure
    (missing template, CLI error, invalid JSON, schema rejection). Validated
    responses are automatically written to `warehouse.claude_reviews` if a
    warehouse reference is provided.
    """
    template = _load_template(task)
    if template is None:
        return None

    prompt = _substitute(template, payload)

    # Split the template's SYSTEM: / USER: sections (simple convention).
    system_prompt = ""
    user_prompt = prompt
    m = re.search(r"(?ms)^SYSTEM:\s*(.*?)\n\s*USER:\s*(.*)$", prompt)
    if m:
        system_prompt = m.group(1).strip()
        user_prompt = m.group(2).strip()

    logger.info(f"[ClaudeAdvisor] task={task} prompt_len={len(user_prompt)}")
    raw = call_claude_cli(
        prompt=user_prompt, system_prompt=system_prompt,
        model=model, effort=effort, timeout=timeout,
    )
    if not raw:
        logger.info(f"[ClaudeAdvisor] task={task} CLI returned nothing")
        return None

    parsed = _extract_json_object(raw)
    if parsed is None:
        logger.info(f"[ClaudeAdvisor] task={task} no JSON object found in output")
        return None

    validated = _validate(task, parsed)
    if validated is None:
        # _validate already logs to claude_schema_failures.jsonl
        return None

    if warehouse is not None:
        try:
            warehouse.record_claude_review(
                ref_type=ref_type or "none",
                ref_id=ref_id,
                task=task,
                decision=str(validated.get("decision") or validated.get("drift_level") or ""),
                confidence=float(validated["confidence"]) if "confidence" in validated else None,
                red_flags=list(validated.get("red_flags") or validated.get("mismatched_features") or []),
                reason_labels=list(validated.get("reason_labels") or []),
                commentary=str(validated.get("commentary") or validated.get("summary") or ""),
            )
        except Exception as e:
            logger.debug(f"[ClaudeAdvisor] warehouse record error: {e}")

    return validated
