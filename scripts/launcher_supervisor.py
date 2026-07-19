"""Safe process supervision and credential prompting for ``TradingBot.bat``.

The batch launcher delegates long-running process ownership and secret input to
this module. Secrets are collected in-process and passed directly to
``bot_helper`` functions, so they never appear in a child process command line.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot_helper  # noqa: E402
from core.entry_policy import (  # noqa: E402
    STANDARD_PAPER_PROFILE,
    normalize_paper_profile,
)
from utils.process_lock import acquire_process_lock as _acquire_process_lock  # noqa: E402

ALREADY_RUNNING = 0
LIVE_MODE_REFUSED = 3
PROCESS_CHECK_FAILED = 4
INTERRUPTED = 130
RESTART_DELAY_SECONDS = 10
MAX_RESTART_DELAY_SECONDS = 300
STABLE_RUN_SECONDS = 600
# Scheduled research/learning jobs run synchronously in the engine loop. A
# three-minute deadline killed healthy PAPER workers mid-job; fifteen minutes
# remains bounded while allowing the measured workload to complete.
HEARTBEAT_MAX_AGE_SECONDS = 900
HEARTBEAT_STARTUP_GRACE_SECONDS = 600
HEARTBEAT_POLL_SECONDS = 5
HEARTBEAT_FAILURE_STRIKES = 3
HELPER_RECHECK_SECONDS = 300
WORKER_STOP_GRACE_SECONDS = 15
WORKER_HUNG_EXIT_CODE = 76
SAFE_MODES = frozenset({"PAPER", "OBSERVATION"})

_ENV_DEFAULTS = {
    "BINANCE_API_KEY": "your_binance_api_key_here",
    "BINANCE_SECRET_KEY": "your_binance_secret_key_here",
    "BINANCE_TESTNET": "false",
    "BYBIT_API_KEY": "your_bybit_api_key_here",
    "BYBIT_SECRET_KEY": "your_bybit_secret_key_here",
    "BITGET_API_KEY": "your_bitget_api_key_here",
    "BITGET_SECRET_KEY": "your_bitget_secret_key_here",
    "BITGET_PASSPHRASE": "your_bitget_passphrase_here",
    "GMAIL_SENDER": "",
    "GMAIL_APP_PASSWORD": "",
    "GMAIL_RECIPIENT": "",
    "EMAIL_SUBJECT_PREFIX": "[TradingBot]",
    "OPERATING_MODE": "PAPER",
    "CONTROLLED_LIVE_ENABLED": "false",
    "DRY_RUN": "true",
    "LOG_LEVEL": "INFO",
    "TRADING_MODE": "usdt_only",
    "PORTFOLIO_MIN_VALUE_USD": "2.0",
    "PORTFOLIO_RESCAN_MINUTES": "60",
    "DRY_RUN_BALANCE": "100.0",
}


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip().strip("\"'")
    return values


def _launcher_mode(root: Path) -> str:
    return _read_env(root / ".env").get("OPERATING_MODE", "PAPER").upper()


def _default_paper_profile(root: Path) -> str:
    """Default PAPER profile when ``run`` gets no --paper-profile flag.

    2026-07-19 max-flow band engine: resolved from the ``.env``
    PAPER_TRADING_PROFILE line so the owner's env block is the single
    activation surface (rollback = revert .env + restart). Unset ->
    STANDARD, exactly the old hardcoded default. The value is still
    validated by normalize_paper_profile in supervise(), so an unknown
    profile refuses startup instead of silently degrading.
    """
    return _read_env(root / ".env").get(
        "PAPER_TRADING_PROFILE", STANDARD_PAPER_PROFILE
    )


def _safe_worker_env(
    root: Path,
    *,
    environ: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Return a child environment pinned to the launcher's safe mode.

    ``python-dotenv`` intentionally preserves inherited variables by default.
    Without this boundary, a parent ``OPERATING_MODE=CONTROLLED_LIVE`` value
    could override a PAPER ``.env`` after the launcher had approved the file.
    Re-read the file immediately before spawn and force both live rails off.
    """

    mode = _launcher_mode(root)
    if mode not in SAFE_MODES:
        raise RuntimeError(f"refusing worker environment for unsafe mode {mode!r}")
    child_env = dict(os.environ if environ is None else environ)
    profile = normalize_paper_profile(
        child_env.get("PAPER_TRADING_PROFILE", STANDARD_PAPER_PROFILE)
    )
    if mode != "PAPER" and profile != STANDARD_PAPER_PROFILE:
        raise RuntimeError(f"PAPER profile {profile!r} requires PAPER mode")
    child_env["OPERATING_MODE"] = mode
    child_env["CONTROLLED_LIVE_ENABLED"] = "false"
    child_env["DRY_RUN"] = "true"
    child_env["PAPER_TRADING_PROFILE"] = profile
    return child_env


def set_safe_mode(mode: str) -> None:
    """Set a launcher-supported mode and force the live latch off."""

    normalized = mode.strip().upper()
    if normalized not in SAFE_MODES:
        raise ValueError("TradingBot.bat only permits PAPER or OBSERVATION mode")
    bot_helper.set_env_var("OPERATING_MODE", normalized)
    bot_helper.set_env_var("CONTROLLED_LIVE_ENABLED", "false")


def _ensure_env_defaults(root: Path = ROOT) -> None:
    existing = _read_env(root / ".env")
    for key, value in _ENV_DEFAULTS.items():
        if key not in existing:
            bot_helper.set_env_var(key, value)


def secure_keys(
    exchange: str,
    *,
    secret_input: Callable[[str], str] = getpass.getpass,
) -> int:
    """Prompt for one exchange's credentials without command-line exposure."""

    normalized = exchange.strip().lower()
    if normalized not in {"binance", "bybit", "bitget"}:
        raise ValueError(f"Unsupported exchange: {exchange}")

    label = normalized.capitalize()
    api_key = secret_input(f"  {label} API key (hidden; Enter to keep current): ")
    if not api_key:
        print(f"  {label} credentials unchanged.")
        return 0
    secret = secret_input(f"  {label} secret key (hidden): ")
    if not secret:
        print("  Secret key is required; credentials unchanged.")
        return 2
    passphrase = ""
    if normalized == "bitget":
        passphrase = secret_input("  Bitget passphrase (hidden): ")
        if not passphrase:
            print("  Passphrase is required; credentials unchanged.")
            return 2

    bot_helper.update_keys(normalized, api_key, secret, passphrase)
    return 0


def secure_email(
    *,
    text_input: Callable[[str], str] = input,
    secret_input: Callable[[str], str] = getpass.getpass,
) -> int:
    """Prompt for email settings while keeping the app password off argv."""

    sender = text_input("  Gmail address (Enter to keep current): ").strip()
    if not sender:
        print("  Email settings unchanged.")
        return 0
    password = secret_input("  Gmail app password (hidden): ")
    recipient = text_input("  Recipient email: ").strip()
    if not password or not recipient:
        print("  App password and recipient are required; email settings unchanged.")
        return 2
    bot_helper.update_email(sender, password, recipient)
    return 0


def secure_setup(root: Path = ROOT) -> int:
    """Create missing safe defaults, then optionally update credentials."""

    _ensure_env_defaults(root)
    print("\n  Exchange credentials (input is hidden; Enter keeps current values).\n")
    for exchange in ("binance", "bybit", "bitget"):
        secure_keys(exchange)
        print()
    secure_email()
    set_safe_mode("PAPER")
    return 0


def _start_helpers(root: Path) -> int:
    script = root / "scripts" / "start_all.ps1"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=root,
        check=False,
    )
    return int(completed.returncode)


def _heartbeat_is_stale(
    root: Path,
    *,
    now: float,
    worker_started_at: float,
    max_age_seconds: float = HEARTBEAT_MAX_AGE_SECONDS,
    startup_grace_seconds: float = HEARTBEAT_STARTUP_GRACE_SECONDS,
) -> bool:
    """Return true only when the owned worker has missed its heartbeat budget.

    An old heartbeat from a prior process is deliberately ignored during the
    startup grace period.  After that deadline, a missing heartbeat or one not
    written by the current worker is considered a hang.  This check uses file
    metadata so the supervisor remains independent of bot imports and a partly
    written JSON document cannot crash the monitor.
    """

    if now - worker_started_at < startup_grace_seconds:
        return False
    path = Path(root) / "data" / "heartbeat.json"
    try:
        modified_at = float(path.stat().st_mtime)
    except OSError:
        return True
    if modified_at < worker_started_at:
        return True
    return now - modified_at > max_age_seconds


def _stop_owned_worker(process) -> None:
    """Stop only the exact child created by this launcher."""

    process.terminate()
    try:
        process.wait(timeout=WORKER_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=WORKER_STOP_GRACE_SECONDS)


def _run_worker(
    root: Path,
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ensure_helpers: Callable[[Path], int] = _start_helpers,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run and externally monitor one PAPER/OBSERVATION worker.

    Process supervision is a separate failure domain from ``BotEngine``.  A
    blocked scheduler therefore cannot keep claiming health indefinitely: once
    its heartbeat is stale, the launcher stops its own child and returns a
    non-zero code so the bounded restart loop can recover it.
    """

    process = popen(
        [sys.executable, str(root / "main.py")],
        cwd=root,
        env=_safe_worker_env(root),
    )
    worker_started_at = clock()
    last_helper_check_at = worker_started_at
    stale_strikes = 0
    while True:
        return_code = process.poll()
        if return_code is not None:
            return int(return_code)
        now = clock()
        if _heartbeat_is_stale(
            root,
            now=now,
            worker_started_at=worker_started_at,
        ):
            stale_strikes += 1
            if stale_strikes < HEARTBEAT_FAILURE_STRIKES:
                print(
                    "[launcher] main.py heartbeat stale strike "
                    f"{stale_strikes}/{HEARTBEAT_FAILURE_STRIKES}; confirming."
                )
                sleep(HEARTBEAT_POLL_SECONDS)
                continue
            print(
                "[launcher] main.py heartbeat remained stale; stopping the owned "
                "worker so supervision can restart it."
            )
            _stop_owned_worker(process)
            return WORKER_HUNG_EXIT_CODE
        stale_strikes = 0
        if now - last_helper_check_at >= HELPER_RECHECK_SECONDS:
            helper_status = ensure_helpers(root)
            if helper_status:
                print(
                    "[launcher] Auxiliary health pass returned "
                    f"{helper_status}; retrying on the next interval."
                )
            last_helper_check_at = now
        sleep(HEARTBEAT_POLL_SECONDS)


def _worker_is_running(root: Path) -> Optional[bool]:
    """Fail closed when a legacy ``main.py`` process is already present."""

    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter "
                "\"name='python.exe' or name='pythonw.exe'\" "
                "-ErrorAction Stop).CommandLine",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("[launcher] Cannot query existing Python workers.")
        return None
    if completed.returncode != 0:
        print("[launcher] Cannot query existing Python workers.")
        return None

    target = str((root / "main.py").resolve()).lower()
    for command_line in completed.stdout.splitlines():
        normalized = command_line.strip().strip('"').lower()
        if target in normalized or re.search(
            r"(?:^|[\\/\s\"'])main\.py(?:$|[\s\"'])", normalized
        ):
            return True
    return False


def supervise(
    *,
    root: Path = ROOT,
    restart: bool = False,
    paper_profile: str = STANDARD_PAPER_PROFILE,
    acquire_lock: Callable[..., Optional[TextIO]] = _acquire_process_lock,
    worker_is_running: Callable[[Path], Optional[bool]] = _worker_is_running,
    start_helpers: Callable[[Path], int] = _start_helpers,
    run_worker: Callable[[Path], int] = _run_worker,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Run one singleton bot worker, optionally restarting after crashes."""

    root = Path(root).resolve()
    lock = acquire_lock("trading_bot_launcher", root=root)
    if lock is None:
        print("[launcher] A supervised bot worker is already running; not starting another.")
        return ALREADY_RUNNING

    try:
        mode = _launcher_mode(root)
        if mode not in SAFE_MODES:
            print(
                "[launcher] Refusing to start: TradingBot.bat supports PAPER and "
                "OBSERVATION only. CONTROLLED_LIVE requires the audited direct procedure."
            )
            return LIVE_MODE_REFUSED

        try:
            profile = normalize_paper_profile(paper_profile)
        except ValueError as exc:
            print(f"[launcher] Refusing startup: {exc}")
            return LIVE_MODE_REFUSED
        if mode != "PAPER" and profile != STANDARD_PAPER_PROFILE:
            print(
                f"[launcher] Refusing {profile} profile in {mode} mode; "
                "non-standard profiles require PAPER."
            )
            return LIVE_MODE_REFUSED

        old_profile = os.environ.get("PAPER_TRADING_PROFILE")
        old_started_at = os.environ.get("PAPER_PROFILE_STARTED_AT")
        os.environ["PAPER_TRADING_PROFILE"] = profile
        os.environ["PAPER_PROFILE_STARTED_AT"] = str(time.time())

        try:
            worker_state = worker_is_running(root)
            if worker_state is None:
                print("[launcher] Refusing startup because duplicate safety could not be verified.")
                return PROCESS_CHECK_FAILED
            if worker_state:
                print("[launcher] main.py is already running; not starting another worker.")
                return ALREADY_RUNNING

            helper_status = start_helpers(root)
            if helper_status:
                print(f"[launcher] Auxiliary startup returned {helper_status}; continuing safely.")

            print(f"[launcher] PAPER trading profile: {profile}")
            consecutive_short_failures = 0
            while True:
                worker_started_at = monotonic()
                try:
                    exit_code = run_worker(root)
                except KeyboardInterrupt:
                    return INTERRUPTED
                runtime_seconds = max(0.0, monotonic() - worker_started_at)
                if exit_code == 0 or not restart:
                    return exit_code
                if runtime_seconds >= STABLE_RUN_SECONDS:
                    consecutive_short_failures = 0
                else:
                    consecutive_short_failures += 1
                delay_seconds = min(
                    RESTART_DELAY_SECONDS * (2 ** max(0, consecutive_short_failures - 1)),
                    MAX_RESTART_DELAY_SECONDS,
                )
                print(
                    f"[launcher] main.py exited with code {exit_code}; "
                    f"restarting in {delay_seconds} seconds."
                )
                sleep(delay_seconds)
        finally:
            if old_profile is None:
                os.environ.pop("PAPER_TRADING_PROFILE", None)
            else:
                os.environ["PAPER_TRADING_PROFILE"] = old_profile
            if old_started_at is None:
                os.environ.pop("PAPER_PROFILE_STARTED_AT", None)
            else:
                os.environ["PAPER_PROFILE_STARTED_AT"] = old_started_at
    finally:
        lock.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradingBot launcher supervisor")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="run the supervised paper/read-only bot")
    run_parser.add_argument("--restart", action="store_true", help="restart after non-zero exits")
    run_parser.add_argument(
        "--paper-profile",
        default=None,
        choices=("standard", "aggressive-research", "max-flow-band"),
        help=(
            "PAPER-only research profile; aggressive/max-flow keep aggregate "
            "risk unchanged. Omitted -> the .env PAPER_TRADING_PROFILE line "
            "(unset there -> standard)."
        ),
    )

    commands.add_parser("secure-setup", help="prompt for initial credentials securely")
    key_parser = commands.add_parser("secure-keys", help="prompt for exchange credentials securely")
    key_parser.add_argument("exchange", choices=("binance", "bybit", "bitget"))
    commands.add_parser("secure-email", help="prompt for email credentials securely")
    mode_parser = commands.add_parser("set-mode", help="select a launcher-safe operating mode")
    mode_parser.add_argument("mode")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    os.chdir(ROOT)
    if args.command == "run":
        return supervise(
            restart=args.restart,
            paper_profile=args.paper_profile or _default_paper_profile(ROOT),
        )
    if args.command == "secure-setup":
        return secure_setup()
    if args.command == "secure-keys":
        return secure_keys(args.exchange)
    if args.command == "secure-email":
        return secure_email()
    if args.command == "set-mode":
        try:
            set_safe_mode(args.mode)
        except ValueError as exc:
            print(f"[launcher] {exc}")
            return LIVE_MODE_REFUSED
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
