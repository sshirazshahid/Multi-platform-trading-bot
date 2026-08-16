"""Regenerate the MCP_DIRECTIONAL_PAPER spec universe from live pair discovery.

The spec at ``data/strategy_specs/MCP_DIRECTIONAL_PAPER.json`` is the route-gate
authorization for directional paper OPENs. Its symbol list previously pinned 44
hand-picked bases, which starved crypto-native bases the bot's own discovery
(TRADING_MODE=all) proposes (``strategy_spec_route_not_approved``).

This script derives the spec's symbols from the bot's OWN eligibility rules
(``core.pair_discovery``) over keyless read-only ccxt clients, so every
exclusion pair_discovery enforces (commodity/stock disabled bases incl.
metadata-flagged TradFi RWA perps like BZ/CL, stables/wrapped, meme tokens,
leveraged tokens, incident quarantine) is applied by CALLING its rejection
logic — never by copying its lists. Eligibility is applied UNCAPPED (the
ALL-mode 25-per-venue cap is a scan budget, not an authorization rule). All
non-symbol fields of the spec are preserved and a provenance entry is appended.

Usage:
    venv/Scripts/python.exe scripts/regen_directional_spec.py --dry-run
    venv/Scripts/python.exe scripts/regen_directional_spec.py

The write is atomic. Fails closed (no write) when discovery yields no bases.
Note: the bundle-MR shadow probes derive their universe from this spec at boot,
so their universe follows every regen (disclosed via funnel universe_widened_utc).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402,F401  (loads .env BEFORE pair_discovery bakes INCIDENT_QUARANTINE_BASES)
from core import pair_discovery  # noqa: E402

DEFAULT_SPEC_PATH = _ROOT / "data" / "strategy_specs" / "MCP_DIRECTIONAL_PAPER.json"
_VENUES = ("binance", "bybit", "bitget")
_PROVENANCE_CAP = 10


def _fresh_quarantine_bases() -> set:
    """Re-read INCIDENT_QUARANTINE_BASES at call time (belt-and-braces vs the
    import-time bake inside pair_discovery)."""
    return {
        b.strip().upper()
        for b in os.getenv("INCIDENT_QUARANTINE_BASES", "").split(",")
        if b.strip()
    }


def derive_futures_bases(all_pairs: dict) -> tuple:
    """Union of discovered futures bases across venues, re-filtered through the
    REAL pair_discovery._asset_rejection_reason plus fresh quarantine env.

    Returns (sorted base list, stats dict).
    """
    quarantine = _fresh_quarantine_bases()
    bases: set = set()
    stats = {"per_venue_futures": {}, "asset_rejected": 0, "quarantined": 0}
    for venue, pairs in (all_pairs or {}).items():
        futures = (pairs or {}).get("futures") or []
        stats["per_venue_futures"][venue] = len(futures)
        for symbol in futures:
            base = str(symbol or "").split("/", 1)[0].split(":", 1)[0].strip().upper()
            if not base:
                continue
            if pair_discovery._asset_rejection_reason(base, {}) is not None:
                stats["asset_rejected"] += 1
                continue
            if base in quarantine:
                stats["quarantined"] += 1
                continue
            bases.add(base)
    return sorted(bases), stats


def build_updated_spec(current: dict, bases: list, stats: dict,
                       widen_min_volume_usd: float = 0.0) -> dict:
    """Return a new spec dict: symbols replaced, everything else preserved,
    provenance appended (capped history)."""
    spec = json.loads(json.dumps(current))  # deep copy, never mutate input
    old_bases = sorted(
        {
            str(s or "").split("/", 1)[0].split(":", 1)[0].strip().upper()
            for s in current.get("symbols") or []
        }
    )
    new_bases = sorted(set(bases))
    provenance = {
        "regenerated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/regen_directional_spec.py",
        "base_count": len(new_bases),
        "added": sorted(set(new_bases) - set(old_bases)),
        "removed": sorted(set(old_bases) - set(new_bases)),
        "exclusions": dict(stats),
    }
    if widen_min_volume_usd:
        # Prereg 80 §4 (binding): the n=36 / 83.3% WR cohort was measured on
        # 44 bases. Post-widening trades are a DIFFERENT population, so the
        # boundary must stay recoverable forever — any analysis spanning it
        # reports pre/post split, never pooled.
        provenance["universe_widened_utc"] = provenance["regenerated_utc"]
        provenance["widen_min_volume_usd"] = float(widen_min_volume_usd)
        provenance["prereg"] = "80_prereg_universe_widening (f7c38f5dd8ff)"
    spec["symbols"] = [f"{b}/USDT:USDT" for b in new_bases]
    history = list(spec.get("regen_provenance") or [])
    history.append(provenance)
    spec["regen_provenance"] = history[-_PROVENANCE_CAP:]
    return spec


def _validate_routes(spec_payload: dict) -> int:
    """Fail-closed check: the regenerated payload must still yield execution
    routes through the exact code the scorer uses at boot."""
    from core.strategy_spec import StrategySpec, approved_paper_futures_routes

    routes = approved_paper_futures_routes([StrategySpec.from_dict(spec_payload)])
    if not routes:
        raise RuntimeError("regenerated spec yields ZERO execution routes")
    return len(routes)


def _write_atomic(spec: dict, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _candidate_bases(current_spec: dict) -> set:
    """Authorization candidates: the bot's own priority sets (the only bases
    ALL-mode discovery can surface, since load_markets metadata carries no 24h
    volume) plus the current spec's incumbents (never silently shrink an
    authorized base while its market is still eligible)."""
    incumbents = {
        str(s or "").split("/", 1)[0].split(":", 1)[0].strip().upper()
        for s in current_spec.get("symbols") or []
    }
    return (
        set(pair_discovery.CORE_SYMBOLS)
        | set(pair_discovery.EXTENDED_CRYPTO)
        | {b for b in incumbents if b}
    )


def _fetch_tickers_by_venue(venues=("binance", "bybit", "bitget")) -> dict:
    """{venue: fetch_tickers()} via keyless public ccxt clients.

    Read-only and per-venue fail-open: a venue that errors contributes
    nothing rather than aborting the widening, mirroring _discover_live's
    posture. Keyless by design — this reads public market data only and must
    never touch account state.
    """
    import ccxt

    out: dict = {}
    markets: dict = {}
    for name in venues:
        try:
            ex = getattr(ccxt, name)({"timeout": 30000,
                                      "options": {"defaultType": "swap"}})
            markets[name] = ex.load_markets()
            out[name] = ex.fetch_tickers()
        except Exception as exc:  # one venue down != no universe
            print(f"[regen] ticker fetch skipped for {name}: "
                  f"{type(exc).__name__}: {str(exc)[:80]}")
    return {"tickers": out, "markets": markets}


def _tradfi_bases_from_markets(markets_by_venue) -> set:
    """Bases ANY venue's metadata identifies as TradFi/tokenized equity.

    Cross-venue metadata is INCONSISTENT: binance tags these
    (TRADIFI_PERPETUAL/COMMODITY, caught by pair_discovery's own asset
    rejection) but bybit and bitget do not — verified 2026-08-16, where
    SAMSUNG/MRVL/SOXL/NBIS were rejected on binance yet ACCEPTED on both
    others. Widening candidacy therefore unions the rejection across venues:
    if any venue says a base is TradFi, it is TradFi everywhere.

    Scoped to widening candidacy. pair_discovery._market_rejection_reason has
    six live runtime callers and is deliberately NOT modified here — changing
    shared selection logic is outside prereg 80 (§6) and is filed as its own
    finding.
    """
    tradfi: set = set()
    for markets in (markets_by_venue or {}).values():
        for market in (markets or {}).values():
            if not isinstance(market, dict):
                continue
            base = pair_discovery._upper(market.get("base"))
            if not base:
                continue
            reason = pair_discovery._market_rejection_reason(market, "futures")
            if reason and str(reason).startswith("tradfi_asset"):
                tradfi.add(base)
    return tradfi


def _volume_widened_bases(tickers_by_venue, *, min_volume_usd: float,
                          min_venues: int = 2, exclude_bases=None) -> set:
    """Bases eligible for the prereg-80 widening, from LIVE ticker volume.

    Frozen rule (_workspace/strategy_pipeline/80_prereg_universe_widening.md,
    sha256 f7c38f5dd8ff...): eligible when max 24h quoteVolume ACROSS venues
    >= ``min_volume_usd`` AND the base trades as an active USDT perp on
    >= ``min_venues`` venues. The caller unions this with the incumbents —
    widening must never shrink the authorized set.

    Volume comes from ``fetch_tickers``, never ``load_markets``: market
    metadata carries no 24h volume (the trap the 2026-07-20 regen note
    flags), which is why _candidate_bases could previously offer only the
    bot's hand-maintained priority lists.

    MAX across venues, not min: we route to the venue holding the book, so
    the deepest venue is the honest statistic. Dated/expiry contracts are
    excluded (not perps). A venue returning nothing is skipped, not fatal —
    one venue outage must not empty the universe.
    """
    vol: dict = {}
    venues: dict = {}
    for venue, tickers in (tickers_by_venue or {}).items():
        if not isinstance(tickers, dict):
            continue
        for symbol, row in tickers.items():
            sym = str(symbol or "")
            # USDT perps only; a "-<expiry>" tail means a dated contract.
            if "/USDT" not in sym or "-" in sym.split(":")[-1]:
                continue
            base = sym.split("/", 1)[0].strip().upper()
            # Non-ASCII tickers exist on live venues (binance/bitget list
            # CJK-named meme tokens). They flow into symbol strings, warehouse
            # rows and log lines, and crashed this very script on a cp1252
            # console. Excluded from WIDENING candidacy only — incumbents and
            # the shared rejection rules are untouched.
            if not base or not base.isascii():
                continue
            try:
                qv = float((row or {}).get("quoteVolume") or 0.0)
            except (TypeError, ValueError):
                continue
            venues.setdefault(base, set()).add(venue)
            vol[base] = max(vol.get(base, 0.0), qv)
    floor = max(float(min_volume_usd), 1.0)  # a 0 floor must not admit dead books
    banned = {str(b).upper() for b in (exclude_bases or ())}
    return {
        b for b, q in vol.items()
        if q >= floor and len(venues.get(b, ())) >= int(min_venues)
        and b not in banned
    }


def _eligible_futures_symbols_from_markets(markets, candidate_bases) -> list:
    """Candidate bases whose USDT-perp passes the bot's OWN rejection logic,
    UNCAPPED.

    Calls pair_discovery's real ``_market_rejection_reason`` (→
    ``_asset_rejection_reason`` incl. metadata TradFi detection), so memes,
    stables/wrapped, commodity/stock and TradFi RWA perps drop out even when
    they are candidates. The ALL-mode 25-per-venue cap is deliberately NOT
    applied: it is a per-cycle scan budget, not an authorization rule — the
    spec must authorize every base discovery can rotate in, or route
    starvation recurs. Liquidity is enforced at runtime by universe_filter.
    """
    from collections.abc import Mapping

    symbols = []
    for market_key, market in (markets or {}).items():
        if not isinstance(market, Mapping):
            continue
        symbol = pair_discovery._market_symbol(market_key, market)
        if not symbol:
            continue
        base = pair_discovery._upper(market.get("base"))
        if base not in candidate_bases:
            continue
        if pair_discovery._market_rejection_reason(market, "futures") is not None:
            continue
        symbols.append(symbol)
    return symbols


def _discover_live(candidate_bases) -> dict:
    """Keyless read-only market discovery via the bot's own discovery rules.

    Fails CLOSED on any venue outage (after one retry): regenerating from a
    partial venue set could wrongly remove incumbents listed only on the
    failed venue.
    """
    import ccxt

    out = {}
    for name in _VENUES:
        last_exc = None
        for _attempt in (1, 2):
            try:
                client = getattr(ccxt, name)({"enableRateLimit": True})
                shim = SimpleNamespace(exchange=client, _connected=True, _futures_blocked=False)
                markets = pair_discovery._load_current_markets(shim)
                symbols = _eligible_futures_symbols_from_markets(markets, candidate_bases)
                out[name] = {"spot": [], "futures": symbols}
                print(
                    f"[regen] {name}: {len(symbols)} eligible candidate "
                    "USDT-perp symbols (uncapped)"
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise RuntimeError(f"{name} discovery failed after retry: {last_exc}")
    return out


def _safe_print(msg: str) -> None:
    """print() that survives a cp1252 console.

    Some venues list bases with non-ASCII tickers. The legacy 44-base
    universe happened to be all-ASCII, so this never surfaced; the prereg-80
    widening (168 bases) hit it immediately and crashed the run AFTER
    discovery succeeded. Report text must never be able to abort a spec
    regeneration.
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "ascii")
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def main(argv=None, *, discover_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", default=str(DEFAULT_SPEC_PATH), help="path to the spec JSON")
    parser.add_argument("--dry-run", action="store_true", help="print the diff without writing")
    parser.add_argument(
        "--widen-min-volume-usd", type=float, default=0.0,
        help=(
            "prereg 80: widen candidacy to EVERY base whose max 24h quoteVolume "
            "across venues clears this floor and that trades as an active USDT "
            "perp on >=2 venues, UNION the incumbents. 0 (default) keeps the "
            "legacy CORE|EXTENDED|incumbents candidacy. Volume is read from live "
            "fetch_tickers — load_markets carries none. Every existing rejection "
            "rule (TradFi/meme/stable/quarantine) still applies afterwards, and "
            "runtime universe_filter still enforces per-trade liquidity."
        ),
    )
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    current = json.loads(spec_path.read_text(encoding="utf-8"))

    try:
        if discover_fn is not None:
            all_pairs = discover_fn()
        else:
            candidates = _candidate_bases(current)
            if args.widen_min_volume_usd > 0:
                live = _fetch_tickers_by_venue()
                tradfi = _tradfi_bases_from_markets(live["markets"])
                widened = _volume_widened_bases(
                    live["tickers"],
                    min_volume_usd=args.widen_min_volume_usd,
                    exclude_bases=tradfi,
                )
                if tradfi:
                    print(f"[regen] cross-venue TradFi exclusion: "
                          f"{len(tradfi)} bases barred from widening")
                if not widened:
                    print("[regen] FAIL-CLOSED: widening found no eligible "
                          "bases (ticker fetch empty?); spec left untouched")
                    return 1
                before = len(candidates)
                candidates = candidates | widened
                print(f"[regen] prereg-80 widening: candidacy {before} -> "
                      f"{len(candidates)} (floor "
                      f"${args.widen_min_volume_usd:,.0f} 24h vol, >=2 venues)")
            all_pairs = _discover_live(candidates)
    except Exception as exc:
        print(f"[regen] FAIL-CLOSED: discovery unavailable: {exc}; spec left untouched")
        return 1
    bases, stats = derive_futures_bases(all_pairs)
    if not bases:
        print(
            "[regen] FAIL-CLOSED: discovery produced no eligible futures bases "
            f"(stats={stats}); spec left untouched"
        )
        return 1

    updated = build_updated_spec(current, bases, stats,
                                 widen_min_volume_usd=args.widen_min_volume_usd)
    prov = updated["regen_provenance"][-1]
    try:
        route_count = _validate_routes(updated)
    except Exception as exc:
        print(f"[regen] FAIL-CLOSED: regenerated spec invalid: {exc}; spec left untouched")
        return 1

    print(f"[regen] spec: {spec_path}")
    print(f"[regen] per-venue futures counts: {stats['per_venue_futures']}")
    print(
        f"[regen] excluded: asset_rejected={stats['asset_rejected']} "
        f"quarantined={stats['quarantined']}"
    )
    print(f"[regen] bases: {len(bases)} (routes: {route_count})")
    _safe_print(f"[regen] added ({len(prov['added'])}): "
                f"{', '.join(prov['added']) or '-'}")
    _safe_print(f"[regen] removed ({len(prov['removed'])}): "
                f"{', '.join(prov['removed']) or '-'}")

    if args.dry_run:
        print("[regen] DRY-RUN: no changes written")
        return 0

    _write_atomic(updated, spec_path)
    print(f"[regen] wrote {spec_path} ({len(bases)} bases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
