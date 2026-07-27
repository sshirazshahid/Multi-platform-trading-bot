#!/usr/bin/env python3
"""Launch Mission Control on 127.0.0.1:8787.

Requires MISSION_CONTROL_TOKEN in the environment or .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    host = os.environ.get("MISSION_CONTROL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("MISSION_CONTROL_PORT", "8787"))
    token = os.environ.get("MISSION_CONTROL_TOKEN", "").strip()
    if not token:
        print("ERROR: set MISSION_CONTROL_TOKEN in .env before starting Mission Control", file=sys.stderr)
        return 2

    from mission_control.app import create_app
    from mission_control.state import validate_bind_host

    allow_lan = os.environ.get("MISSION_CONTROL_ALLOW_LAN", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    validate_bind_host(host, allow_lan=allow_lan, token_set=bool(token))
    app = create_app(root=ROOT, token=token, bind_host=host, allow_lan=allow_lan)

    import uvicorn

    print(f"Mission Control → http://{host}:{port}  (token auth required)")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
