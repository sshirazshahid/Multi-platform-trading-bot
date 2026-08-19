"""
core/live_gate.py — CONTROLLED_LIVE sign-off gate (spec Appendix B).

Standalone helper that verifies the owner has signed
`docs/CONTROLLED_LIVE_CHECKLIST.md`. A valid signature is a line that
matches ``Signed-By: <name> <YYYY-MM-DD>`` where the date parses as an
ISO date and is not in the future.

Used by `BotEngine.run()` when `OPERATING_MODE == "CONTROLLED_LIVE"`: an
unsigned checklist raises SystemExit before any live trading begins.

We isolate this as its own module so it can be tested without importing
the full engine (which drags in `schedule`, exchanges, etc.).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path

CHECKLIST_PATH = Path("docs/CONTROLLED_LIVE_CHECKLIST.md")
MAX_SIGNATURE_AGE_DAYS = 30
MODEL_POINTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "models"
    / "ensemble_futures_latest.json"
)

# Matches `Signed-By: Owner Name 2026-12-31`. HTML-comment wrappers
# (`<!-- ... -->`, including a missing `-->`) are rejected in
# `_line_is_signature` so a commented template cannot count as sign-off.
_SIGN_RE = re.compile(
    r"^\s*Signed-By:\s*(?P<name>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$"
)


def _line_is_signature(line: str) -> tuple[str, date] | None:
    """Parse `line` as a signature. Returns (name, date) or None."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    # Reject commented-out examples — including an unclosed `<!--` with no
    # `-->`, which used to match as a live signature.
    if "<!--" in s:
        return None
    m = _SIGN_RE.match(s)
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    if d > date.today():
        return None
    return (m.group("name").strip(), d)


def is_checklist_signed(
    path: Path | str | None = None,
    *,
    max_signature_age_days: int = MAX_SIGNATURE_AGE_DAYS,
) -> tuple[bool, str]:
    """Return (ok, message). `ok=True` means CONTROLLED_LIVE may proceed."""
    p = Path(path) if path else CHECKLIST_PATH
    if not p.exists():
        return False, f"checklist not found at {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"cannot read checklist: {e}"

    unchecked = [
        raw.strip()
        for raw in text.splitlines()
        if re.match(r"^\s*-\s*\[\s\]\s+", raw, flags=re.IGNORECASE)
    ]
    if unchecked:
        return False, (
            f"{len(unchecked)} acceptance item(s) remain unchecked in {p} "
            "— refusing to run CONTROLLED_LIVE"
        )

    signatures = []
    for raw in text.splitlines():
        sig = _line_is_signature(raw)
        if sig:
            signatures.append(sig)
    if not signatures:
        return False, (
            "no valid 'Signed-By: <name> <YYYY-MM-DD>' line in "
            f"{p} — refusing to run CONTROLLED_LIVE. See file for criteria."
        )
    name, d = signatures[-1]
    if max_signature_age_days >= 0:
        oldest_allowed = date.today() - timedelta(days=max_signature_age_days)
        if d < oldest_allowed:
            return False, (
                f"signature by {name} on {d.isoformat()} is older than "
                f"{max_signature_age_days} days — re-verify and re-sign {p}"
            )
    return True, f"signed by {name} on {d.isoformat()}"


def enforce_controlled_live_gate(operating_mode: str,
                                  path: Path | str | None = None,
                                  controlled_live_enabled: bool | None = None) -> None:
    """Raise SystemExit if mode=CONTROLLED_LIVE and the checklist is unsigned.

    No-op for OBSERVATION or PAPER modes.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    if controlled_live_enabled is False:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
            "CONTROLLED_LIVE_ENABLED is not true"
        )
    ok, msg = is_checklist_signed(path)
    if not ok:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: " + msg
        )


def enforce_live_runtime_invariants(
    operating_mode: str,
    *,
    config_module: object | None = None,
) -> None:
    """Reject CONTROLLED_LIVE when a safety-critical runtime rail is weakened."""
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return

    try:
        if config_module is None:
            import config as config_module
        from core.entry_policy import mode_profile_for
    except Exception as exc:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
            f"live safety configuration unavailable: {exc}"
        ) from exc

    failures: list[str] = []

    def value(name: str, default=None):
        if isinstance(config_module, dict):
            return config_module.get(name, default)
        return getattr(config_module, name, default)

    def number(name: str) -> float | None:
        try:
            result = float(value(name))
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    if value("SL_FAIL_EMERGENCY_CLOSE_ENABLED") is not True:
        failures.append("stop-loss emergency close must be enabled")
    if value("OHLCV_VALIDATION_ENABLED") is not True:
        failures.append("OHLCV validation must be enabled")
    if value("PER_POSITION_LOCK_ENABLED") is not True:
        failures.append("per-position lock must be enabled")

    profile = mode_profile_for("CONTROLLED_LIVE")
    exposure_pct = number("MAX_PORTFOLIO_EXPOSURE_PCT")
    max_exposure_pct = profile.gross_exposure_pct * 100.0
    if exposure_pct is None or not 0 < exposure_pct <= max_exposure_pct:
        failures.append(
            f"portfolio exposure cap must be in (0, {max_exposure_pct:g}] percent"
        )

    aggregate_risk = number("MAX_AGGREGATE_OPEN_RISK_PCT")
    if aggregate_risk is None or not 0 < aggregate_risk <= profile.aggregate_open_risk_pct:
        failures.append(
            "aggregate open-risk cap must be positive and no greater than "
            f"{profile.aggregate_open_risk_pct:.4f}"
        )

    stressed_exit_cost = number("STRESSED_EXIT_COST_FRAC")
    if stressed_exit_cost is None or stressed_exit_cost < 0.002:
        failures.append("stressed exit-cost fraction must be at least 0.002")

    book_age = number("EXECUTION_BOOK_MAX_AGE_SEC")
    if book_age is None or not 0 < book_age <= 5.0:
        failures.append("execution-book age limit must be in (0, 5] seconds")

    slippage_bps = number("MAX_ENTRY_SLIPPAGE_BPS")
    if slippage_bps is None or not 0 <= slippage_bps <= 30.0:
        failures.append("entry-slippage cap must be between 0 and 30 bps")

    daily_breaker = value("DAILY_LOSS_BREAKER")
    try:
        daily_loss_pct = float(daily_breaker.get("max_loss_pct"))
        daily_enabled = daily_breaker.get("enabled") is True
    except (AttributeError, TypeError, ValueError):
        daily_loss_pct, daily_enabled = math.nan, False
    if (
        not daily_enabled
        or not math.isfinite(daily_loss_pct)
        or not 0 < daily_loss_pct <= 0.02
    ):
        failures.append("daily-loss breaker must be enabled at no more than 2 percent")

    risk = value("RISK")
    try:
        leverage = float(risk.get("futures_max_leverage"))
    except (AttributeError, TypeError, ValueError):
        leverage = math.nan
    if not math.isfinite(leverage) or not 0 < leverage <= profile.max_leverage:
        failures.append(
            f"leverage cap must be positive and no greater than {profile.max_leverage:g}x"
        )

    if failures:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: unsafe live "
            "configuration: " + "; ".join(failures)
        )


def enforce_strategy_readiness_gate(
    operating_mode: str,
    *,
    db_path: Path | str = "data/warehouse.sqlite",
    strategy_family: str | None = None,
) -> None:
    """Raise SystemExit if CONTROLLED_LIVE lacks proven strategy evidence.

    Checklist signature proves operator intent. This gate proves the selected
    strategy has recent after-cost evidence. Missing or negative evidence is a
    hard stop for live startup.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    from core.strategy_readiness import evaluate_warehouse

    report = evaluate_warehouse(db_path, mode="PAPER", strategy_family=strategy_family)
    if report.get("ready"):
        return
    reasons = "; ".join((report.get("reasons") or [])[:5]) or "not promotion-ready"
    name = report.get("name", "strategy")
    raise SystemExit(
        "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
        f"{name} evidence gate failed: {reasons}"
    )


def enforce_model_gate_readiness(
    operating_mode: str,
    *,
    config_module: object | None = None,
    pointer_path: Path | str | None = None,
) -> None:
    """Require a valid promoted futures model for an active live MCP gate.

    MCP intentionally falls back to rules when no model bundle can be loaded.
    That is useful for PAPER research, but must not silently turn an explicitly
    active CONTROLLED_LIVE model gate into a rule-only strategy.  Live startup
    therefore revalidates the pointer, ModelManifest, component checksums,
    promotion diagnostics, and freshness at the authorization boundary.

    The check is dormant outside CONTROLLED_LIVE and when the gate is disabled
    or shadow-only.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return

    try:
        if config_module is None:
            import config as config_module

        def value(name: str, default=None):
            if isinstance(config_module, Mapping):
                return config_module.get(name, default)
            return getattr(config_module, name, default)

        signal_source = str(value("SIGNAL_SOURCE", "") or "").strip().lower()
        if signal_source != "mcp":
            return

        model_gate = value("MODEL_GATE")
        if not isinstance(model_gate, Mapping):
            raise ValueError("MODEL_GATE must be a mapping")
        if model_gate.get("enabled") is not True or model_gate.get("shadow_only") is True:
            return

        path = Path(pointer_path) if pointer_path is not None else MODEL_POINTER_PATH
        try:
            pointer = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"latest model pointer is missing: {path}") from exc
        except (OSError, ValueError) as exc:
            raise ValueError(f"latest model pointer is unreadable: {path}: {exc}") from exc
        if not isinstance(pointer, dict):
            raise ValueError("latest model pointer is not a JSON object")

        # Promotion normally records an absolute path. Support relocatable
        # bundles too by resolving a relative artifact before validation.
        diag = pointer.get("diag")
        raw_artifact = pointer.get("artifact_path")
        if not raw_artifact and isinstance(diag, Mapping):
            raw_artifact = diag.get("artifact_path")
        if isinstance(raw_artifact, str) and raw_artifact.strip():
            artifact = Path(raw_artifact)
            if not artifact.is_absolute():
                project_candidate = Path(__file__).resolve().parents[1] / artifact
                pointer_candidate = path.resolve().parent / artifact
                resolved = next(
                    (
                        candidate
                        for candidate in (project_candidate, pointer_candidate)
                        if candidate.is_file()
                    ),
                    project_candidate,
                )
                pointer = dict(pointer)
                pointer["artifact_path"] = str(resolved)
                if isinstance(diag, Mapping):
                    pointer["diag"] = {**diag, "artifact_path": str(resolved)}

        from core.promotion_gate import validate_model_pointer

        ok, reason, _diagnostics = validate_model_pointer(
            pointer,
            market_type="futures",
        )
        if not ok:
            raise ValueError(reason or "model pointer validation failed")
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: active MCP "
            f"model gate is not production-ready: {exc}"
        ) from exc


# ── CONTROLLED_LIVE capability preflight (Codex preflight.py port, 2026-07-12) ──
#
# An ADDITIONAL latch condition — never a replacement for the signed-checklist
# latch above. READ-ONLY by construction: no order is ever placed, modified,
# or cancelled (capability checks are introspective, mirroring the Codex
# reference which validates via market metadata + client capability flags).
# Dormant in OBSERVATION/PAPER.

PREFLIGHT_MAX_CLOCK_SKEW_MS = 1000
PREFLIGHT_MAX_SANE_MIN_COST_USD = 10_000.0


def _preflight_raw_client(ex):
    """Our BaseExchange wrappers hold the raw ccxt client at ``.exchange``;
    accept bare clients too (unit tests, future wrappers)."""
    raw = getattr(ex, "exchange", None)
    return raw if raw is not None else ex


def preflight_exchange(name, ex, symbols, *,
                       expect_one_way: bool | None = None,
                       expected_margin_mode: str | None = None,
                       max_clock_skew_ms: int = PREFLIGHT_MAX_CLOCK_SKEW_MS,
                       now_ms: float | None = None) -> dict:
    """Live-capability preflight for one exchange. Returns
    ``{"exchange", "passed", "failures", "notes"}``.

    Checks: (a) server-clock skew; (b) position mode vs the order code's
    assumption (``expect_one_way``); (c) margin mode vs ``expected_margin_mode``
    (capability note only when no expectation is given — the bot sets no
    margin mode anywhere); (d) protective conditional order placeable,
    validate-only; (e) min-notional floors for ``symbols`` fetched and sane.
    Every internal error is recorded as a failure (fail-closed)."""
    lname = str(name).lower()
    failures: list[str] = []
    notes: list[str] = []
    client = _preflight_raw_client(ex)

    # (a) server-clock skew within tolerance
    try:
        server_ms = float(client.fetch_time())
        local_ms = float(now_ms) if now_ms is not None else float(client.milliseconds())
        skew = abs(server_ms - local_ms)
        if skew > max_clock_skew_ms:
            failures.append(
                f"{lname}: clock skew {skew:.0f}ms exceeds {max_clock_skew_ms}ms")
    except Exception as e:
        failures.append(f"{lname}: clock-skew check errored: {e}")

    has = {}
    try:
        has = dict(getattr(client, "has", {}) or {})
    except Exception:
        has = {}

    # (b) position mode (one-way vs hedge) matches what order code assumes
    if expect_one_way is not None:
        try:
            if has.get("fetchPositionMode"):
                mode = client.fetch_position_mode(symbols[0] if symbols else None)
                actual_one_way = not bool((mode or {}).get("hedged"))
                if actual_one_way != bool(expect_one_way):
                    failures.append(
                        f"{lname}: position mode is "
                        f"{'one-way' if actual_one_way else 'hedge'} but order "
                        f"code assumes {'one-way' if expect_one_way else 'hedge'}")
            else:
                notes.append(
                    f"{lname}: position mode unverifiable (no fetchPositionMode)")
        except Exception as e:
            failures.append(f"{lname}: position-mode check errored: {e}")

    # (c) margin mode as expected
    try:
        if expected_margin_mode:
            if has.get("fetchMarginMode") and symbols:
                mm = client.fetch_margin_mode(symbols[0])
                actual = str((mm or {}).get("marginMode") or "").lower()
                if actual and actual != expected_margin_mode.lower():
                    failures.append(
                        f"{lname}: margin mode '{actual}' != expected "
                        f"'{expected_margin_mode}'")
                elif not actual:
                    failures.append(
                        f"{lname}: margin mode expected "
                        f"'{expected_margin_mode}' but venue reported none")
            else:
                failures.append(
                    f"{lname}: margin mode expected '{expected_margin_mode}' "
                    f"but venue cannot report it")
        elif not has.get("setMarginMode", True):
            notes.append(f"{lname}: setMarginMode capability not advertised")
    except Exception as e:
        failures.append(f"{lname}: margin-mode check errored: {e}")

    # (d) protective conditional order type placeable — VALIDATE-ONLY: the
    # bot's own param builder must produce a shape for this venue AND the
    # venue must advertise a conditional/trigger order path. No probe order
    # is ever sent, so nothing can be left resting on the book.
    try:
        from core.order_manager import build_sl_tp_order_params
        order_type, params = build_sl_tp_order_params(
            lname, "buy",
            bool(expect_one_way) if expect_one_way is not None else True,
            1.0, is_sl=True)
        if not order_type or not isinstance(params, dict) or not params:
            failures.append(
                f"{lname}: protective-order param builder returned an empty shape")
        capability = None
        if hasattr(client, "feature_value"):
            try:
                probe_sym = symbols[0] if symbols else None
                attached = bool(client.feature_value(
                    probe_sym, "createOrder", "stopLossPrice"))
                trigger = bool(client.feature_value(
                    probe_sym, "createOrder", "triggerPrice"))
                capability = attached or trigger
            except Exception:
                capability = None
        if capability is None:
            flags = [has.get(k) for k in (
                "createStopOrder", "createTriggerOrder",
                "createStopMarketOrder", "createStopLimitOrder")]
            if any(f is not None for f in flags):
                capability = any(bool(f) for f in flags)
        if capability is False:
            failures.append(
                f"{lname}: venue does not advertise a protective conditional "
                f"order path")
        elif capability is None:
            notes.append(
                f"{lname}: protective-order capability unverifiable — "
                f"param-builder shape check only")
    except Exception as e:
        failures.append(f"{lname}: protective-order validate-only check errored: {e}")

    # (e) min-notional floors for the whitelisted symbols fetched and sane
    try:
        if hasattr(client, "load_markets"):
            client.load_markets()
        resolved = 0
        for sym in symbols or []:
            market = None
            for candidate in (sym, f"{sym}:USDT"):
                try:
                    market = client.market(candidate)
                except Exception:
                    market = None
                if market:
                    break
            if not market:
                continue  # not every venue lists every whitelisted symbol
            resolved += 1
            limits = market.get("limits") or {}
            min_cost = (limits.get("cost") or {}).get("min")
            min_amount = (limits.get("amount") or {}).get("min")
            floors = []
            for v in (min_cost, min_amount):
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(f) and f > 0:
                    floors.append(f)
            if not floors:
                failures.append(
                    f"{lname}: {sym}: no positive min-notional/min-amount "
                    f"floor in market limits")
            elif min_cost is not None and \
                    float(min_cost) > PREFLIGHT_MAX_SANE_MIN_COST_USD:
                failures.append(
                    f"{lname}: {sym}: min cost {min_cost} fails sanity bound "
                    f"(> {PREFLIGHT_MAX_SANE_MIN_COST_USD:g} USD)")
        if symbols and resolved == 0:
            failures.append(
                f"{lname}: none of the whitelisted symbols resolved to a market")
    except Exception as e:
        failures.append(f"{lname}: min-notional check errored: {e}")

    return {
        "exchange": lname,
        "passed": not failures,
        "failures": failures,
        "notes": notes,
    }


def run_live_preflight(exchanges: dict, symbols, *,
                       expect_one_way: dict | None = None,
                       expected_margin_mode: str | None = None,
                       max_clock_skew_ms: int = PREFLIGHT_MAX_CLOCK_SKEW_MS,
                       now_ms: float | None = None):
    """Run ``preflight_exchange`` over every exchange client.

    ``expect_one_way`` maps lowercase exchange name -> bool (the order
    code's position-mode assumption, e.g. derived from
    ``OrderManager._oneway_mode``). Returns ``(ok, failures, reports)``."""
    failures: list[str] = []
    reports: list[dict] = []
    if not exchanges:
        failures.append("no exchange clients available for preflight")
    for name in sorted(exchanges or {}):
        eow = None
        if expect_one_way is not None:
            eow = expect_one_way.get(str(name).lower())
        report = preflight_exchange(
            name, exchanges[name], symbols,
            expect_one_way=eow,
            expected_margin_mode=expected_margin_mode,
            max_clock_skew_ms=max_clock_skew_ms,
            now_ms=now_ms)
        reports.append(report)
        failures.extend(report["failures"])
    return (not failures), failures, reports


def enforce_live_preflight_gate(operating_mode: str, exchanges: dict, symbols,
                                *, notify=None,
                                expect_one_way: dict | None = None,
                                expected_margin_mode: str | None = None,
                                max_clock_skew_ms: int = PREFLIGHT_MAX_CLOCK_SKEW_MS
                                ) -> None:
    """ADDITIONAL CONTROLLED_LIVE latch condition — raise SystemExit when the
    venue-capability preflight fails, with the specific reasons.

    Runs AFTER (never instead of) the signed-checklist latch. No-op for
    OBSERVATION/PAPER — dormant, no network. Fail-closed: an erroring
    preflight keeps the latch CLOSED. ``notify`` is an optional callable
    (e.g. ``notifier.send``); its own failures never mask the block."""
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    try:
        ok, failures, _reports = run_live_preflight(
            exchanges, symbols,
            expect_one_way=expect_one_way,
            expected_margin_mode=expected_margin_mode,
            max_clock_skew_ms=max_clock_skew_ms)
    except Exception as e:  # fail-closed — never open the latch on an error
        ok, failures = False, [f"preflight itself errored: {e}"]
    if ok:
        return
    msg = "live preflight FAILED: " + "; ".join(failures[:8])
    if notify is not None:
        try:
            notify(f"[LiveGate] REFUSING CONTROLLED_LIVE start — {msg}")
        except Exception:
            pass
    raise SystemExit("[LiveGate] REFUSING TO START in CONTROLLED_LIVE: " + msg)


def live_latch_permits_execution(operating_mode: str,
                                 controlled_live_enabled: bool) -> bool:
    """Latch 2 of the CONTROLLED_LIVE double-latch (the env-var latch).

    Real-order execution is permitted UNLESS we are in CONTROLLED_LIVE without
    the `CONTROLLED_LIVE_ENABLED` env latch set. PAPER places paper orders
    regardless; OBSERVATION is blocked by a separate upstream gate, so this
    latch alone does not block it. Latch 1 (a signed checklist) is enforced
    separately by `enforce_controlled_live_gate` at startup.
    """
    if (operating_mode or "").upper() == "CONTROLLED_LIVE" and not controlled_live_enabled:
        return False
    return True
