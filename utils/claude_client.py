"""
utils/claude_client.py -- Claude interface (CLI primary, Anthropic API fallback)

Usage:
    from utils.claude_client import call_claude

    text = call_claude(prompt, system_prompt="...", model="sonnet")
"""

import json
import os
import random
import shutil
import subprocess
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_CLAUDE_BIN = shutil.which("claude") or "claude"
_claude_code_available: bool | None = None  # cached result
_last_error: str = ""  # last failure reason — read by callers for diagnostics

# ── Retry configuration ────────────────────────────────────────────────
MAX_CLI_RETRIES = 2       # 1 original + 1 retry
CLI_BACKOFF_BASE = 2.0    # seconds

# Owner directive (2026-05-29): keep Opus's answers as short / to-the-point as
# possible. Appended to the system prompt of EVERY call (additive, so it never
# clobbers a caller's --system-prompt). Brevity is orthogonal to --effort max
# (depth of thinking) and also speeds generation (fewer output tokens). Phrased
# to PRESERVE required JSON structure — terse, not truncated.
_BREVITY_DIRECTIVE = (
    "BREVITY IS MANDATORY. Answer in the absolute fewest words that fully address "
    "the request — default to 1-3 sentences. Do NOT use headers, section titles, or "
    "bullet lists unless explicitly asked. No preamble, no restating the question, "
    "no background, no caveats, no closing summary, no commentary or explanation "
    "unless explicitly requested, no markdown code fences. If a specific output "
    "format (e.g. JSON) is required, return ONLY that structure — complete, with "
    "nothing before or after it."
)

# ── Audit log ──────────────────────────────────────────────────────────
AUDIT_DIR = Path("data/claude_audit")

def _audit_log(entry: dict):
    """Append a JSONL audit entry for every Claude API call."""
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        path = AUDIT_DIR / f"calls_{month}.jsonl"
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass



def _check_claude_code() -> bool:
    """Check whether the Claude Code CLI is installed and runnable."""
    global _claude_code_available
    if _claude_code_available is not None:
        return _claude_code_available
    try:
        r = subprocess.run(
            [_CLAUDE_BIN, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            _claude_code_available = True
            logger.debug(f"[ClaudeClient] CLI found: {r.stdout.strip()}")
            return True
    except Exception as e:
        logger.debug(f"[ClaudeClient] CLI check failed: {e}")
    _claude_code_available = False
    return False


# ── CLI call ────────────────────────────────────────────────────────────

def call_claude_cli(
    prompt: str,
    system_prompt: str = "",
    model: str = "claude-opus-4-8",
    timeout: int = 120,
    effort: str = "max",
) -> str | None:
    """
    Call Claude via the Claude Code CLI in non-interactive mode.
    Retries once on transient failures with exponential backoff.
    Logs every call (input + output) to audit trail.
    Returns the response text, or None on any failure.
    """
    global _last_error
    # Owner directive (2026-05-29): force EVERY bot Claude call to Opus 4.8 at
    # max effort, regardless of the model/effort each caller passed (older
    # callers still request "sonnet"/"haiku"/"low"). Escape hatch via env:
    #   BOT_CLAUDE_MODEL / BOT_CLAUDE_EFFORT  (e.g. if Opus is rate-limited).
    # Note: --effort max is the deepest (slowest) level; there is NO headless
    # "fast mode" flag in the CLI (fast mode is interactive-only via /fast), so
    # speed and max-effort are opposing knobs — effort is the only speed lever.
    model = os.getenv("BOT_CLAUDE_MODEL", "claude-opus-4-8")
    effort = os.getenv("BOT_CLAUDE_EFFORT", "max")
    if not _check_claude_code():
        _last_error = "CLI binary not found or not runnable"
        return None

    cmd = [
        _CLAUDE_BIN,
        "-p",                           # print mode (reads prompt from stdin)
        "--output-format", "json",      # structured JSON envelope
        "--model", model,
        "--max-turns", "1",             # single response, no tool use
        "--no-session-persistence",     # don't save session to disk
        "--effort", effort,             # low=fast classification, high/max=deep analysis
        "--append-system-prompt", _BREVITY_DIRECTIVE,  # owner directive: terse answers
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    for attempt in range(MAX_CLI_RETRIES):
        t0 = _time.time()
        audit = {
            "attempt": attempt + 1,
            "model": model,
            "effort": effort,
            "timeout": timeout,
            "prompt_len": len(prompt),
            "system_prompt_len": len(system_prompt),
        }
        try:
            # Strip ANTHROPIC_API_KEY so CLI uses Max subscription, not API credits
            cli_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            r = subprocess.run(
                cmd,
                input=prompt,               # Pipe prompt via stdin (no length limit)
                capture_output=True,
                text=True,
                encoding="utf-8",           # Force UTF-8 (Windows default cp1252 can't handle Unicode)
                timeout=timeout,
                env=cli_env,
            )
            audit["latency_sec"] = round(_time.time() - t0, 2)
            audit["returncode"] = r.returncode

            if not r.stdout.strip():
                _last_error = f"empty output (rc={r.returncode})"
                if r.stderr:
                    _last_error += f" stderr: {r.stderr[:200]}"
                logger.debug(f"[ClaudeClient] CLI {_last_error}")
                audit["status"] = "empty_output"
                audit["error"] = _last_error
                _audit_log(audit)
                if attempt < MAX_CLI_RETRIES - 1:
                    delay = CLI_BACKOFF_BASE * (2 ** attempt) + random.random()
                    logger.debug(f"[ClaudeClient] Retrying in {delay:.1f}s...")
                    _time.sleep(delay)
                    continue
                return None

            # Parse the JSON envelope
            try:
                envelope = json.loads(r.stdout)
            except json.JSONDecodeError:
                start = r.stdout.find("{")
                end = r.stdout.rfind("}") + 1
                if start >= 0 and end > start:
                    envelope = json.loads(r.stdout[start:end])
                else:
                    logger.debug(
                        f"[ClaudeClient] Cannot parse output: {r.stdout[:200]}")
                    audit["status"] = "parse_error"
                    _audit_log(audit)
                    return None

            if envelope.get("is_error"):
                err = (envelope.get("error")
                       or " | ".join(envelope.get("errors", []))
                       or envelope.get("result", "unknown error"))
                _last_error = f"CLI error: {err[:200]}"
                logger.debug(f"[ClaudeClient] {_last_error}")
                audit["status"] = "cli_error"
                audit["error"] = err[:200]
                _audit_log(audit)
                if attempt < MAX_CLI_RETRIES - 1:
                    delay = CLI_BACKOFF_BASE * (2 ** attempt) + random.random()
                    _time.sleep(delay)
                    continue
                return None

            text = envelope.get("result", "").strip()
            if not text:
                _last_error = "CLI returned no result text"
                logger.debug(f"[ClaudeClient] {_last_error}")
                audit["status"] = "empty_result"
                _audit_log(audit)
                return None

            cost = envelope.get("total_cost_usd", 0)
            if cost > 0:
                logger.debug(f"[ClaudeClient] Cost: ${cost:.4f}")
            audit["status"] = "success"
            audit["cost_usd"] = cost
            audit["response_len"] = len(text)
            _audit_log(audit)
            return text

        except subprocess.TimeoutExpired:
            _last_error = f"timed out ({timeout}s)"
            logger.debug(f"[ClaudeClient] CLI {_last_error}")
            audit["status"] = "timeout"
            audit["latency_sec"] = round(_time.time() - t0, 2)
            _audit_log(audit)
            if attempt < MAX_CLI_RETRIES - 1:
                delay = CLI_BACKOFF_BASE * (2 ** attempt) + random.random()
                logger.debug(f"[ClaudeClient] Retrying in {delay:.1f}s...")
                _time.sleep(delay)
                continue
            return None
        except FileNotFoundError:
            _claude_code_available = False
            _last_error = "CLI binary not found"
            logger.debug(f"[ClaudeClient] {_last_error}")
            audit["status"] = "not_found"
            _audit_log(audit)
            return None  # no point retrying
        except Exception as e:
            _last_error = f"invocation failed: {e}"
            logger.debug(f"[ClaudeClient] CLI {_last_error}")
            audit["status"] = "exception"
            audit["error"] = str(e)[:200]
            _audit_log(audit)
            if attempt < MAX_CLI_RETRIES - 1:
                delay = CLI_BACKOFF_BASE * (2 ** attempt) + random.random()
                _time.sleep(delay)
                continue
            return None
    return None


# ── Unified entry point ────────────────────────────────────────────────

def call_claude(
    prompt: str,
    system_prompt: str = "",
    model: str = "claude-opus-4-8",
    timeout: int = 120,
    effort: str = "max",
    **_kwargs,
) -> str | None:
    """
    Call Claude via CLI only (Opus 4.8). No API fallback.

    NOTE: model/effort passed here are overridden inside call_claude_cli to
    Opus 4.8 + max effort (owner directive); pass BOT_CLAUDE_MODEL /
    BOT_CLAUDE_EFFORT env vars to change that.

    Args:
        prompt:        The user message / analysis prompt.
        system_prompt: Optional system-level instructions.
        model:         CLI model name (forced to claude-opus-4-8).
        timeout:       Timeout in seconds.
        effort:        CLI effort level (low|medium|high|xhigh|max; forced to max).

    Returns:
        Response text, or None on failure.
    """
    text = call_claude_cli(prompt, system_prompt, model, timeout, effort)
    if text is not None:
        return text

    logger.warning("[ClaudeClient] Claude Code CLI failed — no response")
    return None


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences from Claude responses."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.split("\n")
        # Remove only the opening and closing fence lines
        text = "\n".join(lines[1:-1]).strip()
    return text


def validate_json_response(text: str, required_fields: list[str] = None,
                           field_types: dict = None) -> dict | None:
    """Parse JSON from Claude response and validate required fields + types.

    Args:
        text: Raw response text (may have markdown fences).
        required_fields: List of field names that must be present.
        field_types: Dict of {field_name: expected_type} for type checking.

    Returns:
        Parsed dict if valid, None if invalid (with error logged).
    """
    try:
        cleaned = strip_markdown_fences(text)
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[ClaudeClient] JSON parse failed: {e} — text: {text[:200]}")
        _audit_log({"status": "validation_fail", "reason": "json_parse",
                     "error": str(e)[:200]})
        return None

    if not isinstance(data, dict):
        logger.error(f"[ClaudeClient] Expected JSON object, got {type(data).__name__}")
        return None

    # Check required fields
    if required_fields:
        missing = [f for f in required_fields if f not in data]
        if missing:
            logger.warning(
                f"[ClaudeClient] Missing required fields: {missing} — "
                f"got keys: {list(data.keys())[:10]}")
            _audit_log({"status": "validation_fail", "reason": "missing_fields",
                         "missing": missing})
            return None

    # Check field types
    if field_types:
        for field, expected_type in field_types.items():
            if field in data and data[field] is not None:
                val = data[field]
                if expected_type in (float, int) and isinstance(val, (int, float, str)):
                    try:
                        data[field] = expected_type(val)
                    except (ValueError, TypeError):
                        logger.warning(
                            f"[ClaudeClient] Field '{field}' cannot convert to {expected_type.__name__}: {val}")

    return data


def is_available() -> bool:
    """Return True if Claude Code CLI is usable."""
    return _check_claude_code()
