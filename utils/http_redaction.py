"""Bounded, credential-safe rendering for HTTP/SDK diagnostics."""

from __future__ import annotations

import json
import os
import re

_SENSITIVE_FIELD = (
    r"api[_-]?key|apikey|secret(?:key)?|signature|authorization|"
    r"access[_-]?token|refresh[_-]?token|token|passphrase|password|"
    r"x-bapi-api-key|x-mbx-apikey|ok-access-key|ok-access-passphrase"
)
_ASSIGNMENT_RE = re.compile(
    rf"(?i)((?:[\"']?(?:{_SENSITIVE_FIELD})[\"']?)\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^&,\s}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,}\]]+")


def redact_http_debug(value, *, max_chars: int = 4000) -> str:
    """Render diagnostics while removing named and configured credentials."""
    if value is None:
        return "None"
    try:
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, default=str, sort_keys=True)
        else:
            rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}>"

    rendered = _BEARER_RE.sub(r"\1[REDACTED]", rendered)
    rendered = _ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]", rendered
    )
    for key, secret in os.environ.items():
        if re.search(_SENSITIVE_FIELD, key, re.IGNORECASE) and len(secret) >= 4:
            rendered = rendered.replace(secret, "[REDACTED]")

    limit = max(128, int(max_chars))
    if len(rendered) > limit:
        rendered = rendered[:limit] + "...[truncated]"
    return rendered
