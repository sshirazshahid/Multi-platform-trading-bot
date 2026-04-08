"""
utils/claude_client.py -- Claude Code CLI interface (CLI-only, no API fallback)

Usage:
    from utils.claude_client import call_claude

    text = call_claude(prompt, system_prompt="...", model="sonnet")
"""

import json
import os
import shutil
import subprocess
from loguru import logger

_CLAUDE_BIN = shutil.which("claude") or "claude"
_claude_code_available: bool | None = None  # cached result


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
            logger.debug("[ClaudeClient] CLI found: {}".format(r.stdout.strip()))
            return True
    except Exception as e:
        logger.debug("[ClaudeClient] CLI check failed: {}".format(e))
    _claude_code_available = False
    return False


# ── CLI call ────────────────────────────────────────────────────────────

def call_claude_cli(
    prompt: str,
    system_prompt: str = "",
    model: str = "sonnet",
    timeout: int = 120,
) -> str | None:
    """
    Call Claude via the Claude Code CLI in non-interactive mode.
    Returns the response text, or None on any failure.
    """
    if not _check_claude_code():
        return None

    cmd = [
        _CLAUDE_BIN,
        "-p",                           # print mode (reads prompt from stdin)
        "--output-format", "json",      # structured JSON envelope
        "--model", model,
        "--max-turns", "1",             # single response, no tool use
        "--no-session-persistence",     # don't save session to disk
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

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

        if not r.stdout.strip():
            logger.debug(
                "[ClaudeClient] CLI empty output (rc={})".format(r.returncode))
            if r.stderr:
                logger.debug(
                    "[ClaudeClient] stderr: {}".format(r.stderr[:300]))
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
                    "[ClaudeClient] Cannot parse output: {}".format(
                        r.stdout[:200]))
                return None

        if envelope.get("is_error"):
            err = (" | ".join(envelope.get("errors", []))
                   or envelope.get("result", "unknown error"))
            logger.debug("[ClaudeClient] CLI error: {}".format(err[:200]))
            return None

        text = envelope.get("result", "").strip()
        if not text:
            logger.debug("[ClaudeClient] CLI returned no result text")
            return None

        cost = envelope.get("total_cost_usd", 0)
        if cost > 0:
            logger.debug("[ClaudeClient] Cost: ${:.4f}".format(cost))
        return text

    except subprocess.TimeoutExpired:
        logger.debug(
            "[ClaudeClient] CLI timed out ({}s)".format(timeout))
        return None
    except FileNotFoundError:
        global _claude_code_available
        _claude_code_available = False
        logger.debug("[ClaudeClient] CLI binary not found")
        return None
    except Exception as e:
        logger.debug("[ClaudeClient] CLI invocation failed: {}".format(e))
        return None


# ── Unified entry point ────────────────────────────────────────────────

def call_claude(
    prompt: str,
    system_prompt: str = "",
    model: str = "sonnet",
    timeout: int = 120,
    **_kwargs,
) -> str | None:
    """
    Call Claude via Claude Code CLI.

    Args:
        prompt:        The user message / analysis prompt.
        system_prompt: Optional system-level instructions.
        model:         CLI model name ("sonnet", "opus", "haiku").
        timeout:       Timeout in seconds.

    Returns:
        Response text, or None on failure.
    """
    text = call_claude_cli(prompt, system_prompt, model, timeout)
    if text is None:
        logger.warning("[ClaudeClient] CLI failed — no response")
    return text


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences from Claude responses."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            l for l in lines if not l.strip().startswith("```")
        ).strip()
    return text


def is_available() -> bool:
    """Return True if Claude Code CLI is usable."""
    return _check_claude_code()
