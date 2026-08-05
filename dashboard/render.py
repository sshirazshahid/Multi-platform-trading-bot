"""Dashboard render loop (unsplit monolith body)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import state
from dashboard.fetcher import LiveFetcher
from dashboard.loaders import (
    _hour_class,
    load_auto_mut,
    load_block_reasons,
    load_news,
    load_risk_state,
    load_warehouse_stats,
)
from dashboard.state import (
    BLUE,
    BOLD,
    CYAN,
    DIM,
    EX_COLOUR,
    GOLD,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    WHITE,
    YELLOW,
)
from dashboard.stats import (
    calc_daily_pnl,
    calc_exchange_stats,
    calc_stats,
    calc_strategy_stats,
    calc_unrealized,
    calc_weekly_stats,
    sparkline,
    _filter_real_trades,
    _whole_pnl,
)
from dashboard.term import (
    _asset_tag,
    _uptime_str,
    col,
    fg_str,
    pnl_str,
    pnl_str_short,
    vljust,
    vrjust,
    wr_col,
)

def render(open_pos, closed, dry_run, tick, fetcher: LiveFetcher):
    now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    try:
        from config import OPERATING_MODE as _OP_MODE
    except ImportError:
        _OP_MODE = "PAPER" if dry_run else "CONTROLLED_LIVE"
    _mode_labels = {
        "PAPER": ("PAPER", YELLOW),
        "OBSERVATION": ("OBSERVATION", BLUE),
        "CONTROLLED_LIVE": ("CONTROLLED_LIVE", RED + BOLD),
    }
    mode, mc = _mode_labels.get(_OP_MODE, ("PAPER", YELLOW))
    s     = calc_stats(closed)
    ex_s  = calc_exchange_stats(closed, open_pos)
    news  = load_news()
    W     = state.DASH_WIDTH

    upnl_map   = calc_unrealized(open_pos, fetcher)
    live_bals  = fetcher.all_balances()
    statuses   = fetcher.all_statuses()
    wallet_bal = fetcher.wallet_balances()
    coin_bals  = fetcher.coin_balances()
    bal_detail = fetcher.balance_detail()
    age_s      = int(fetcher.seconds_since_fetch())
    age_col    = GREEN if age_s < 15 else (YELLOW if age_s < 30 else RED)
    # Only count unrealized PnL from FUTURES positions — spot holdings are
    # already valued in coin_bals, counting them here would double-count.
    _futures_ids = {p.get("id", "") for p in open_pos if p.get("market_type") == "futures"}
    total_upnl = sum(v["upnl"] for pid, v in upnl_map.items()
                     if v.get("upnl") is not None and pid in _futures_ids)
    upnl_count = sum(1 for pid, v in upnl_map.items()
                     if v.get("upnl") is not None and pid in _futures_ids)
    upnl_total_count = len(_futures_ids)
    tm         = fetcher.trading_mode().upper()

    # ── Box-drawing helpers ───────────────────────────────────────────
    B_TL = "┌"; B_TR = "┐"; B_BL = "└"; B_BR = "┘"
    B_H  = "─"; B_V  = "│"; B_LT = "├"; B_RT = "┤"

    def box_top(title=""):
        if title:
            pad = W - 4 - len(title)
            print(col(B_TL + B_H + " ", CYAN) + col(title, BOLD + CYAN) +
                  col(" " + B_H * max(pad, 1) + B_TR, CYAN))
        else:
            print(col(B_TL + B_H * (W - 2) + B_TR, CYAN))

    def box_mid(title=""):
        if title:
            pad = W - 4 - len(title)
            print(col(B_LT + B_H + " ", DIM) + col(title, BOLD + CYAN) +
                  col(" " + B_H * max(pad, 1) + B_RT, DIM))
        else:
            print(col(B_LT + B_H * (W - 2) + B_RT, DIM))

    def box_bot():
        print(col(B_BL + B_H * (W - 2) + B_BR, CYAN))

    def row(text):
        print(col(B_V, DIM) + " " + text)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    print(col("╔" + "═" * (W - 2) + "╗", CYAN))
    title_txt = "TRADING BOT  --  LIVE DASHBOARD"
    pad_l = (W - 2 - len(title_txt)) // 2
    print(col("║", CYAN) + " " * pad_l + col(title_txt, BOLD + WHITE) +
          " " * (W - 2 - pad_l - len(title_txt)) + col("║", CYAN))
    print(col("╚" + "═" * (W - 2) + "╝", CYAN))

    # Status bar
    ex_icons = []
    for e in ["binance", "bybit", "bitget"]:
        if e not in statuses:
            continue
        ok = statuses.get(e) == "OK"
        ic = col("●", GREEN) if ok else col("●", RED)
        ex_icons.append("{} {}".format(ic, col(e.upper(), EX_COLOUR.get(e, WHITE))))
    tm_col = GREEN if tm == "ALL" else (YELLOW if tm == "PORTFOLIO" else WHITE)
    print("  {} {} {} {} {} {}  {}".format(
        col(now, DIM), col(B_V, DIM), col(mode, mc), col(B_V, DIM),
        col("Mode:", DIM), col(tm, tm_col),
        "  ".join(ex_icons) if ex_icons else col("No exchanges", DIM)))
    print("  {} {} {} {} {}".format(
        col("Uptime:", DIM), col(_uptime_str(), WHITE),
        col(B_V, DIM),
        col("Data:", DIM), col("{}s ago".format(age_s), age_col)))

    # ── Bot Heartbeat Status ──────────────────────────────────────────
    try:
        hb_path = Path("data/heartbeat.json")
        if hb_path.exists():
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
            hb_uptime = hb.get("uptime_seconds", 0)
            hb_hours = hb_uptime // 3600
            hb_mins = (hb_uptime % 3600) // 60
            hb_cycle = hb.get("cycle_count", 0)
            hb_mem = hb.get("memory_mb", 0)
            hb_ts = hb.get("timestamp", "")
            # Calculate age of heartbeat
            hb_age_s = "?"
            try:
                from datetime import datetime as _dt
                hb_dt = _dt.fromisoformat(hb_ts.replace("Z", "+00:00"))
                hb_age_s = str(int((datetime.now(timezone.utc) - hb_dt).total_seconds()))
            except Exception:
                pass
            # 2026-06-22: reflect the bot's real state instead of a hardcoded
            # green "RUNNING". Precedence: risk-manager halt > heartbeat
            # staleness. A crashed/stalled bot leaves a stale heartbeat file
            # (the file-absent guard below never fires for it), so age is the
            # only signal that catches a dead process. halted_exchanges /
            # api_latency are per-cycle-volatile sentinels in this build and
            # are intentionally not surfaced here to avoid false DEGRADED
            # flicker; is_halted + age cover what the operator must not miss.
            try:
                _hb_age = int(hb_age_s)
            except (TypeError, ValueError):
                _hb_age = None
            if hb.get("is_halted"):
                hb_status = col("HALTED", RED + BOLD)
                hb_reason = "  " + col(
                    "[{}]".format(hb.get("halt_reason") or "risk halt"), RED)
            elif _hb_age is not None and _hb_age > 600:
                hb_status = col("STALE", RED + BOLD)
                hb_reason = "  " + col("[no heartbeat update]", RED)
            elif _hb_age is not None and _hb_age > 180:
                hb_status = col("STALE", YELLOW + BOLD)
                hb_reason = "  " + col("[heartbeat lagging]", YELLOW)
            else:
                hb_status = col("RUNNING", GREEN + BOLD)
                hb_reason = ""
            # Mem: bot writes 0 when psutil isn't available in the venv —
            # display "n/a" instead of the misleading "0MB".
            mem_str = "n/a" if (not hb_mem or hb_mem == 0) else "{:.0f}MB".format(hb_mem)
            print("  {} {}  {} {}h {}m  {} {}  {} {}  {} {}s ago{}".format(
                col("Bot:", DIM), hb_status,
                col("Up:", DIM), hb_hours, hb_mins,
                col("Cycle:", DIM), col(str(hb_cycle), WHITE),
                col("Mem:", DIM), mem_str,
                col("HB:", DIM), hb_age_s,
                hb_reason))
        else:
            print("  {} {}".format(
                col("Bot:", DIM),
                col("No heartbeat -- bot may not be running", YELLOW)))
    except Exception:
        pass
    print()

    # ══════════════════════════════════════════════════════════════════
    #  EXCHANGE BALANCES & PORTFOLIO
    # ══════════════════════════════════════════════════════════════════
    box_top("EXCHANGE BALANCES" + ("  (paper)" if dry_run else ""))
    total_live = 0.0
    start_bal  = 0.0
    _stables = {"USDT", "USD", "BUSD", "USDC"}

    if dry_run:  # 2026-05-31: fail-safe — PAPER never falls through to the live
        # real-balance / Est.-Total path (which sums real spot holdings as the paper
        # balance — the class behind the historical ~$24,934 conflation). If the
        # virtual wallet is unavailable, show the start balance, never real holdings.
        wallet_bal = wallet_bal or {}
        try:
            wf = Path("data/virtual_wallet.json")
            if wf.exists():
                wd = json.loads(wf.read_text(encoding="utf-8"))
                start_bal = wd.get("start", 100.0)
        except Exception:
            start_bal = 100.0
        _n_disp = 0
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in statuses:
                continue
            _n_disp += 1
            ec  = EX_COLOUR.get(ex_name, WHITE)
            bal = wallet_bal.get(ex_name, start_bal)
            st  = statuses.get(ex_name, "—")
            diff = bal - start_bal
            total_live += bal
            st_s = col("OK", GREEN) if st == "OK" else col(st[:15], RED)
            row("{} {}  {:>10.2f} USDT  [{}]  {:>+.2f}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8), bal, st_s, diff))
        # 2026-06-22: seed the ROI denominator with the SAME accounts summed
        # into total_live (every displayed exchange), not just the OK ones.
        # Previously a transient non-OK status dropped an account from the
        # denominator while its balance stayed in the numerator, skewing ROI.
        start_total = start_bal * max(_n_disp, 1)
        roi_pct = ((total_live - start_total) / start_total * 100) if start_total > 0 else 0
        rc = GREEN if roi_pct >= 0 else RED
        box_mid()
        row("{}  {:>10.2f} USDT   {}".format(
            col("TOTAL", BOLD + WHITE), total_live,
            col("ROI: {:+.2f}% (sim wallet vs seed)".format(roi_pct), rc)))
        # 2026-06-11: the sim wallet measures "since its last seed" and was
        # re-seeded by a test-clobber bug, so its ROI can diverge from real
        # paper results. Always show trade-history truth alongside it.
        try:
            _wh = load_warehouse_stats()
            if _wh.get("total_trades", 0) > 0:
                _net = _wh.get("mode_net", 0.0)
                row("{}  {}".format(
                    col("Trade PnL ({}, all history, {} trades):".format(
                        _wh.get("mode", ""), _wh["total_trades"]), DIM),
                    col("{:+.2f} USDT".format(_net),
                        GREEN if _net >= 0 else RED)))
        except Exception:
            pass
    elif live_bals:
        bal_det = fetcher.balance_detail()
        grand_coins = {}
        grand_usdt_vals = {}
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in live_bals and ex_name not in statuses:
                continue
            ec   = EX_COLOUR.get(ex_name, WHITE)
            bal  = live_bals.get(ex_name, 0.0)
            st   = statuses.get(ex_name, "—")
            det  = bal_det.get(ex_name, {})
            total_live += bal
            st_s = col("OK", GREEN) if st == "OK" else col(st[:15], RED)
            if det.get("unified"):
                detail_s = col("Unified Account", DIM)
            else:
                s_bal = det.get("spot", 0.0)
                f_bal = det.get("futures", 0.0)
                detail_s = "{}:{:.2f}  {}:{:.2f}".format(
                    col("Spot", CYAN), s_bal, col("Fut", ORANGE), f_bal)
            row("{} {}  {:>10.2f} USDT  [{}]  {}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8), bal, st_s, detail_s))
            # Coin holdings for this exchange
            ex_coins = coin_bals.get(ex_name, {})
            is_unified = det.get("unified", False)
            coin_rows = []
            for asset, info in ex_coins.items():
                amt = info.get("total", 0)
                free = float(info.get("free", 0) or 0)
                if amt <= 0:
                    continue
                if asset in _stables:
                    usdt_val = amt
                else:
                    sym = "{}/USDT".format(asset)
                    px = fetcher.get_price(ex_name, sym)
                    usdt_val = amt * px if px > 0 else 0.0
                if usdt_val < 0.01:
                    continue
                coin_rows.append((asset, amt, free, usdt_val))
                # Unified exchanges (Bybit): totalEquity in total_live already
                # includes all coin values, so skip adding to grand totals to
                # avoid double-counting in Est. Total.
                if not is_unified:
                    grand_coins[asset] = grand_coins.get(asset, 0.0) + amt
                    grand_usdt_vals[asset] = grand_usdt_vals.get(asset, 0.0) + usdt_val
            coin_rows.sort(key=lambda r: (0 if r[0] in _stables else 1, -r[3]))
            if coin_rows:
                parts = []
                for asset, amt, free, uval in coin_rows[:6]:
                    if asset in _stables:
                        # 2026-05-01 fix: show FREE (truly available for new
                        # orders) instead of TOTAL (wallet balance). On Bybit
                        # Unified Account, margin locked on open positions
                        # makes free < total — the user cares about free.
                        # When there's a meaningful gap, append "(+N locked)"
                        # so they can see at a glance how much margin is
                        # currently used.
                        locked = max(0.0, float(amt) - free)
                        if free > 0 and locked > 0.5:
                            parts.append("{} {:.2f} {}".format(
                                col(asset, YELLOW), free,
                                col("(+{:.2f} locked)".format(locked), DIM),
                            ))
                        elif free > 0:
                            parts.append("{} {:.2f}".format(
                                col(asset, YELLOW), free))
                        else:
                            # No free reported — fall back to total so the
                            # user still sees a number rather than zero.
                            parts.append("{} {:.2f}".format(
                                col(asset, YELLOW), amt))
                    else:
                        parts.append("{} {:.6g} {}".format(
                            col(asset, CYAN), amt,
                            col("~{:.2f}$".format(uval), DIM)))
                row("  " + col("└─", DIM) + " " + "  ".join(parts))
                if len(coin_rows) > 6:
                    extra = len(coin_rows) - 6
                    row("    " + col("+{} more coins".format(extra), DIM))

        box_mid("PORTFOLIO TOTALS")
        row("  {} {:>10.2f} USDT".format(
            col("USDT Balance:", WHITE), total_live))
        if total_upnl != 0 or upnl_count > 0:
            uc = GREEN if total_upnl >= 0 else RED
            cnt_s = " ({} futures)".format(upnl_count) if upnl_count > 0 else ""
            row("  {} {}{}".format(
                col("Unrealized:  ", WHITE),
                col("{:>+10.4f} USDT".format(total_upnl), uc),
                col(cnt_s, DIM)))
        # Aggregated coin holdings (non-stable only)
        # Excludes unified exchange coins (Bybit) — already in USDT Balance
        if grand_coins:
            non_stable = []
            for asset, amt in grand_coins.items():
                if asset in _stables:
                    continue
                uval = grand_usdt_vals.get(asset, 0.0)
                if uval >= 0.01:
                    non_stable.append((asset, amt, uval))
            non_stable.sort(key=lambda r: -r[2])
            non_stable_val = sum(r[2] for r in non_stable)
            if non_stable:
                hld = "  ".join("{} {:.6g}".format(col(r[0], CYAN), r[1])
                               for r in non_stable[:10])
                row("  {} {} {}".format(
                    col("Coins:       ", WHITE), hld,
                    col("~{:.0f}$".format(non_stable_val), DIM)))
            # Est. Total = USDT balances + non-stable coin value + futures unrealized
            total_est = total_live + non_stable_val + total_upnl
            row("  {} {:>10.2f} USDT".format(
                col("Est. Total:  ", BOLD + WHITE), total_est))
        else:
            row("  {} {:>10.2f} USDT".format(
                col("Est. Total:  ", BOLD + WHITE), total_live + total_upnl))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  PERFORMANCE
    # ══════════════════════════════════════════════════════════════════
    box_top("PERFORMANCE")
    tl = s["today_n"]     - s["today_wins"]
    yl = s["yesterday_n"] - s["yesterday_wins"]
    al = s["total_n"]     - s["all_wins"]

    sk = s["streak"]; stype = s["streak_type"]
    streak_s = col("{}{}".format(sk, stype), GREEN if stype == "W" else RED) if sk > 0 else col("--", DIM)
    pf = s["profit_factor"]
    pf_c = GREEN if pf >= 1.5 else (YELLOW if pf >= 1.0 else RED)
    # Phase 43: 999.0 is the no-losses sentinel — show as ∞ instead of literal "999.00"
    pf_s = col("∞" if pf >= 100 else "{:.2f}".format(pf), pf_c)

    row("  {}  {}  trades:{}  W:{} L:{}  WR:{}".format(
        vljust(col("Today (UTC)", WHITE), 13), pnl_str(s["today_pnl"]),
        s["today_n"], col(s["today_wins"], GREEN), col(tl, RED),
        col("{:.1f}%".format(s["today_wr"]), wr_col(s["today_wr"]))))
    if s["yesterday_n"]:
        row("  {}  {}  trades:{}  W:{} L:{}  WR:{}".format(
            vljust(col("Yesterday", WHITE), 13), pnl_str(s["yesterday_pnl"]),
            s["yesterday_n"], col(s["yesterday_wins"], GREEN), col(yl, RED),
            col("{:.1f}%".format(s["yesterday_wr"]), wr_col(s["yesterday_wr"]))))
    else:
        row("  {}  {}".format(vljust(col("Yesterday", WHITE), 13),
                              col("no closed trades", DIM)))
    # Phase 47: show date range + ties (if any) for clarity. WR uses the
    # classic formula (W / (W + L)) excluding ties from denominator; the
    # raw display L count includes ties for compactness.
    # 2026-06-11: position_tracker caps closed history at 500 rows, so once
    # full this window is a rolling ring buffer, NOT inception-to-date — label
    # honestly ("since" = oldest SURVIVING row, which advances every close).
    # Below the cap nothing has been evicted yet, so "All Time" stays true.
    _at_base = "Last 500 trades" if s["total_n"] >= 500 else "All Time"
    _at_label = _at_base
    _earliest = s.get("earliest_ts")
    if _earliest:
        try:
            _at_label = "{} (since {})".format(
                _at_base,
                datetime.fromtimestamp(_earliest, tz=timezone.utc).strftime("%d %b"))
        except (OSError, ValueError, OverflowError):
            pass
    _ties = s.get("all_ties", 0)
    _ties_str = "  T:{}".format(_ties) if _ties > 0 else ""
    _wr_disp = s.get("all_wr_classic", s["all_wr"])
    row("  {}  {}  trades:{}  W:{} L:{}{}  WR:{}".format(
        vljust(col(_at_label, WHITE), 22), pnl_str(s["all_pnl"]),
        s["total_n"], col(s["all_wins"], GREEN), col(al, RED), _ties_str,
        col("{:.1f}%".format(_wr_disp), wr_col(_wr_disp))))
    row("  Avg Win: {}  Avg Loss: {}  PF: {}  Streak: {}".format(
        pnl_str_short(s["avg_win"]),
        pnl_str_short(-s["avg_loss"]) if s["avg_loss"] else col("--", DIM),
        pf_s, streak_s))
    if s["all_best"] != 0 or s["all_worst"] != 0:
        row("  Best Trade: {}  Worst Trade: {}  Fees: {}".format(
            pnl_str_short(s["all_best"]), pnl_str_short(s["all_worst"]),
            col("-{:.4f}".format(s["all_fees"]), RED)))
    # Phase 45: show all-time best/worst DAY — "highest numbers achieved
    # since bot started". Captures the bot's high-water mark in cumulative
    # daily PnL, which is more meaningful for "achievement" than a single
    # trade.
    if s.get("best_day_alltime", 0) != 0 or s.get("worst_day_alltime", 0) != 0:
        bd_date = s.get("best_day_date")
        wd_date = s.get("worst_day_date")
        bd_str  = bd_date.strftime("%d %b") if bd_date else "?"
        wd_str  = wd_date.strftime("%d %b") if wd_date else "?"
        row("  Peak Day:   {} ({})  Worst Day:   {} ({})".format(
            pnl_str_short(s["best_day_alltime"]),  col(bd_str, DIM),
            pnl_str_short(s["worst_day_alltime"]), col(wd_str, DIM)))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  TRADING GATES — hour gate + whitelist/blacklist + BTC macro + tiers
    # ══════════════════════════════════════════════════════════════════
    try:
        from config import (
            WHITELIST_SYMBOLS, BLACKLIST_HARD, LEVERAGE_TIERS,
            MAX_LOSS_PER_TRADE_PCT, SHORTS_REQUIRE_BTC_BEAR,
            BTC_TREND_TIMEFRAME,
        )
        cur_utc = datetime.now(timezone.utc)
        cur_hour = cur_utc.hour
        hc_label, hc_color = _hour_class(cur_hour)

        box_top("TRADING GATES")
        # Line 1: UTC hour status
        row("  {} {}  hour {:02d} → {}   Loss clamp: {}".format(
            col("UTC:", DIM),
            col(cur_utc.strftime("%H:%M"), WHITE),
            cur_hour,
            col(hc_label, hc_color),
            col("{:.1f}%".format(MAX_LOSS_PER_TRADE_PCT * 100),
                YELLOW)))

        # Line 2: static whitelist / blacklist sizes
        row("  {} {} symbols   {} {} symbols".format(
            col("Whitelist:", DIM),
            col(str(len(WHITELIST_SYMBOLS)), GREEN),
            col("Blacklist:", DIM),
            col(str(len(BLACKLIST_HARD)), RED)))

        # Line 3: leverage tiers
        tier_parts = []
        for tname, tcfg in LEVERAGE_TIERS.items():
            tier_parts.append("{}: {}x/{:.1f}%SL/conf{:.0f}%".format(
                col(tname[:4], CYAN),
                tcfg.get("leverage", 1),
                tcfg.get("sl_pct", 0) * 100,
                tcfg.get("min_confidence", 0) * 100))
        row("  Tiers: " + "  ".join(tier_parts))

        # Line 4: BTC macro + shorts gate
        btc_trend = "?"
        btc_col = DIM
        try:
            # Read the BTC trend the engine persists (data/btc_trend.json).
            btc_state = _file_cache.load("data/btc_trend.json") or {}
            # Prefer the engine's actual trend string so BULL/BEAR matches the bot's
            # +/-0.2% logic; fall back to slope sign for older files.
            _t = btc_state.get("trend")
            if _t in ("bull", "bear", "neutral"):
                btc_trend, btc_col = {
                    "bull": ("BULL", GREEN),
                    "bear": ("BEAR", RED),
                    "neutral": ("FLAT", YELLOW),
                }[_t]
            else:
                btc_slope = btc_state.get("ema200_slope", 0)
                if btc_slope > 0:
                    btc_trend = "BULL"
                    btc_col = GREEN
                elif btc_slope < 0:
                    btc_trend = "BEAR"
                    btc_col = RED
                else:
                    btc_trend = "FLAT"
                    btc_col = YELLOW
        except Exception:
            pass
        shorts_ok = (not SHORTS_REQUIRE_BTC_BEAR) or (btc_trend == "BEAR")
        shorts_s = col("ALLOWED", GREEN) if shorts_ok else col("BLOCKED (needs BTC bear)", RED)
        row("  BTC {}: {}   Shorts: {}".format(
            BTC_TREND_TIMEFRAME, col(btc_trend, btc_col), shorts_s))
        # 2026-06-22: surface the BTC-vol circuit breaker (core/btc_vol_pause.py,
        # active in the live engine path). It gates ALL new entries when BTC 1h
        # ATR spikes vs its trailing median. Previously invisible — when it
        # paused, the operator saw entries stop with no on-screen reason.
        try:
            _bv = _file_cache.load("data/btc_vol_state.json") or {}
            _pu = float(_bv.get("pause_until") or 0.0)
            _buf = _bv.get("buf") or []
            _cur_vol = (_buf[-1][1] if _buf and isinstance(_buf[-1], (list, tuple))
                        and len(_buf[-1]) > 1 else None)
            _vol_s = col("  ATR {:.2f}%".format(_cur_vol), DIM) if _cur_vol is not None else ""
            if _pu > time.time():
                _mins = int((_pu - time.time()) / 60) + 1
                row("  Vol gate: {}{}".format(
                    col("PAUSED {}m (BTC vol spike)".format(_mins), RED + BOLD), _vol_s))
            else:
                row("  Vol gate: {}{}".format(col("armed", GREEN), _vol_s))
        except Exception:
            pass
        box_bot()
        print()
    except Exception as _e:
        # Config changed or not importable — skip silently
        pass

    # ══════════════════════════════════════════════════════════════════
    #  AUTO-MUTATIONS — closed-loop post-mortem learner state
    # ══════════════════════════════════════════════════════════════════
    amut = load_auto_mut()
    if amut:
        box_top("AUTO-MUTATIONS  (post-mortem learner)")
        # Dynamic blacklist
        dbl = amut.get("blacklist", {})
        now_ts = time.time()
        active_bl = [(sym, float(expires) - now_ts)
                     for sym, expires in dbl.items()
                     if float(expires) > now_ts]
        if active_bl:
            active_bl.sort(key=lambda x: -x[1])
            parts = ["{} ({:.0f}h)".format(
                col(sym.split("/")[0], RED), secs / 3600)
                for sym, secs in active_bl[:6]]
            row("  {} {}".format(
                col("Dyn-blacklist:", DIM), "  ".join(parts)))
            if len(active_bl) > 6:
                row("  " + col("  +{} more".format(len(active_bl) - 6), DIM))
        else:
            row("  {} {}".format(
                col("Dyn-blacklist:", DIM), col("(empty)", DIM)))

        # Leverage cap
        lev_cap = amut.get("leverage_cap")
        lev_until = float(amut.get("leverage_cap_until", 0) or 0)
        if lev_cap and lev_until > now_ts:
            hours_left = (lev_until - now_ts) / 3600
            row("  {} {}x for {:.1f}h".format(
                col("Leverage cap:", DIM),
                col(str(int(lev_cap)), YELLOW),
                hours_left))
        else:
            row("  {} {}".format(
                col("Leverage cap:", DIM), col("(none)", DIM)))

        # Shorts block
        sb_until = float(amut.get("shorts_blocked_until", 0) or 0)
        if sb_until > now_ts:
            hours_left = (sb_until - now_ts) / 3600
            row("  {} {} for {:.1f}h".format(
                col("Shorts:", DIM), col("BLOCKED", RED), hours_left))
        else:
            row("  {} {}".format(
                col("Shorts:", DIM), col("allowed", GREEN)))

        # Last scan
        last_scan = float(amut.get("last_scan_at", 0) or 0)
        if last_scan > 0:
            mins_ago = (now_ts - last_scan) / 60
            tail = amut.get("last_scan_loss_tail", 0)
            age_c = GREEN if mins_ago < 10 else (YELLOW if mins_ago < 30 else DIM)
            row("  {} {}m ago   lookback={} losses".format(
                col("Last scan:", DIM),
                col("{:.0f}".format(mins_ago), age_c),
                tail))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WHY NO TRADES — block-reason telemetry from today's log tail
    # ══════════════════════════════════════════════════════════════════
    try:
        br = load_block_reasons()
        if br.get("total", 0) > 0 or br.get("signal_empty", 0) > 0:
            box_top("WHY NO TRADES  (last ~800 log lines)")
            parts = []
            for k, label, c in [
                ("BLACKLIST", "static-BL",  RED),
                ("DYN_BL",    "dyn-BL",     RED),
                ("UNIVERSE",  "universe",   YELLOW),
                ("RISK",      "risk-mgr",   YELLOW),
                ("HOUR",      "hour-gate",  YELLOW),
                ("TIER",      "tier",       CYAN),
                ("RR",        "r:r",        CYAN),
                ("SHORTS",    "shorts",     CYAN),
                ("LEV",       "lev-cap",    CYAN),
                ("OTHER",     "other",      DIM),
            ]:
                v = br.get(k, 0)
                if v:
                    parts.append("{}:{}".format(col(label, c), v))
            if parts:
                row("  Blocks: " + "  ".join(parts) +
                    "  " + col("total={}".format(br["total"]), BOLD + WHITE))
            else:
                row("  " + col("No blocks in recent tail", GREEN))
            ce = br.get("signal_empty", 0)
            az = br.get("algo_zero", 0)
            ap = br.get("actions", 0)
            row("  {} {}   {} {}   {} {}".format(
                col("Signal empty:", DIM),
                col(str(ce), RED if ce > 10 else YELLOW if ce else GREEN),
                col("Algo 0-action:", DIM),
                col(str(az), RED if az > 10 else YELLOW if az else GREEN),
                col("Algo-proposed:", DIM),
                col(str(ap), GREEN if ap else DIM)))
            box_bot()
            print()
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════
    #  OPEN POSITIONS
    # ══════════════════════════════════════════════════════════════════
    n_fut = sum(1 for p in open_pos if p.get("market_type") == "futures")
    n_spt = len(open_pos) - n_fut
    box_top("OPEN POSITIONS  [Spot:{} Futures:{}  Total:{}]".format(n_spt, n_fut, len(open_pos)))
    if open_pos:
        by_ex = defaultdict(list)
        for p in open_pos:
            by_ex[(p.get("exchange") or "?").lower()].append(p)
        first_ex = True
        for ex_name, pos_list in sorted(by_ex.items()):
            if not first_ex:
                box_mid(ex_name.upper())
            else:
                first_ex = False
            ec = EX_COLOUR.get(ex_name, WHITE)
            row("{} {} {} positions".format(
                col("●", ec), col(ex_name.upper(), BOLD + ec), len(pos_list)))
            for pos in pos_list:
                pid    = pos.get("id", "")
                side   = (pos.get("side") or "?").upper()
                sym    = pos.get("symbol", "?")
                entry  = pos.get("entry_price", 0)
                sz     = float(pos.get("size", 0) or 0)
                sl     = pos.get("stop_loss", 0)
                tp     = pos.get("take_profit", 0)
                raw_s  = pos.get("strategy", "?") or "?"
                strat  = raw_s.split("|")[0]
                prof   = raw_s.split("|")[1] if "|" in raw_s else ""
                mtype  = pos.get("market_type", "spot")
                lev    = pos.get("leverage", 1)
                dur    = int((time.time() - pos.get("open_time", time.time())) / 60)
                paper  = pos.get("paper_trade", True)
                ud     = upnl_map.get(pid, {})
                live_px  = ud.get("price", 0.0)
                upnl     = ud.get("upnl")
                upnl_pct = ud.get("upnl_pct")
                move     = ud.get("move_pct", 0.0)
                sc     = GREEN if side == "BUY" else RED
                tag    = col("P", PURPLE) if paper else col("L", GREEN + BOLD)
                lev_s  = col(" {}x".format(lev), YELLOW) if lev > 1 else ""
                prof_s = col(" {}".format(prof[:4].upper()), YELLOW) if prof else ""
                at     = _asset_tag(sym)
                mt_tag = col("SPOT", CYAN) if mtype == "spot" else col("FUT ", ORANGE)
                if live_px > 0:
                    px_col = GREEN if move >= 0 else RED
                    live_s = col("{:.6g}".format(live_px), px_col)
                    move_s = col("{:+.2f}%".format(move), px_col)
                else:
                    live_s = col("--", DIM); move_s = col("--", DIM)
                if upnl is not None:
                    uc     = GREEN if upnl >= 0 else RED
                    upnl_s = col("{:+.4f}({:+.1f}%)".format(upnl, upnl_pct or 0), uc)
                else:
                    upnl_s = col("--", DIM)
                # Line 1: tag, side, market, symbol
                row("  [{}] {} {} {}{}{}{}".format(
                    tag, col(side, sc), mt_tag, col(sym, WHITE), lev_s, prof_s, at))
                # Line 2: entry, live, move, upnl, duration
                price_lbl = "Buy" if side == "BUY" else "Sell"
                if entry and entry > 0:
                    row("      {} @{:.6g}  Now:{}  {}  uPnL:{}  {}m".format(
                        price_lbl, entry, live_s, move_s, upnl_s, dur))
                else:
                    # Holdings with unknown entry price — show value instead
                    val = ud.get("value", sz * live_px if live_px else 0)
                    val_s = col("${:.2f}".format(val), WHITE) if val else col("--", DIM)
                    row("      Now:{}  Value:{}  Qty:{:.6g}  {}m".format(
                        live_s, val_s, sz, dur))
                # Line 3: SL/TP or value
                # 2026-05-02: show SL protection coverage. _exchange_sl=True
                # means the SL is registered on the exchange (hard, fires
                # automatically). False means soft-SL only (relies on bot's
                # monitor cycle — at risk if bot crashes or has API delay).
                exch_sl = pos.get("_exchange_sl", False)
                if sl or tp:
                    # 2026-06-22: previously this required BOTH sl AND tp, which
                    # hid the stop-loss for any futures position that has an SL
                    # but no TP — TSMOM disaster-stop-only holds, and live
                    # exchange positions whose only stop is the liquidation
                    # price (take_profit=0). Render whichever protections exist
                    # so the operator always sees the active stop.
                    sl_tag = (col("hard", GREEN) if exch_sl
                              else col("soft", YELLOW))
                    seg = []
                    if sl:
                        if entry and entry > 0:
                            seg.append("SL:{:.6g}({:.1f}%) {}".format(
                                sl, abs(sl - entry) / entry * 100, sl_tag))
                        else:
                            seg.append("SL:{:.6g} {}".format(sl, sl_tag))
                    if tp:
                        if entry and entry > 0:
                            seg.append("TP:{:.6g}({:.1f}%)".format(
                                tp, abs(tp - entry) / entry * 100))
                        else:
                            seg.append("TP:{:.6g}".format(tp))
                    row("      " + "  ".join(seg) + "  [{}]".format(col(strat, DIM)))
                elif mtype == "spot" and sz > 0 and live_px > 0:
                    value = sz * live_px
                    row("      Qty:{:.6g}  Value:{:.2f} USDT  [{}]".format(
                        sz, value, col(strat, DIM)))
    else:
        row("  " + col("No open positions", DIM))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  EXCHANGE BREAKDOWN
    # ══════════════════════════════════════════════════════════════════
    if ex_s:
        box_top("EXCHANGE BREAKDOWN  (Spot vs Futures)")
        total_spot_pnl = 0.0
        total_futures_pnl = 0.0
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in ex_s:
                continue
            d    = ex_s[ex_name]
            ec   = EX_COLOUR.get(ex_name, WHITE)
            pnl  = d["pnl"]; n = d["n"]; wins = d["wins"]
            open_n = d["open"]
            wr   = (wins / n * 100) if n > 0 else 0
            # 2026-06-11: in PAPER show the paper wallet balance, NOT the
            # user's real exchange balance (previously this column leaked
            # live account balances into the paper dashboard).
            live_b = wallet_bal.get(ex_name) if dry_run else live_bals.get(ex_name)
            bal_s  = col("bal:{:.2f}".format(live_b), DIM) if live_b else ""
            row("{} {} trades:{:>3}  WR:{}  PnL:{}  open:{}  {}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8),
                n, col("{:.1f}%".format(wr), wr_col(wr)),
                col("{:+.4f}".format(pnl), GREEN if pnl >= 0 else RED),
                col(str(open_n), CYAN if open_n > 0 else DIM), bal_s))
            sp = d.get("spot_pnl", 0.0); sn = d.get("spot_n", 0)
            fp = d.get("futures_pnl", 0.0); fn = d.get("futures_n", 0)
            total_spot_pnl += sp
            total_futures_pnl += fp
            if sn > 0 or fn > 0:
                parts = []
                if sn > 0:
                    sw = d.get("spot_wins", 0)
                    swr = (sw / sn * 100) if sn > 0 else 0
                    parts.append("{}: {} ({} trd, {:.0f}%WR)".format(
                        col("SPOT", CYAN),
                        col("{:+.4f}".format(sp), GREEN if sp >= 0 else RED), sn, swr))
                if fn > 0:
                    fw = d.get("futures_wins", 0)
                    fwr = (fw / fn * 100) if fn > 0 else 0
                    parts.append("{}: {} ({} trd, {:.0f}%WR)".format(
                        col("FUT", ORANGE),
                        col("{:+.4f}".format(fp), GREEN if fp >= 0 else RED), fn, fwr))
                row("  " + col("└─", DIM) + " " + ("  " + col(B_V, DIM) + "  ").join(parts))
        if total_spot_pnl != 0 or total_futures_pnl != 0:
            box_mid("TOTAL PROFIT")
            row("  {} {}    {} {}".format(
                col("SPOT:", CYAN),
                col("{:+.4f} USDT".format(total_spot_pnl),
                    GREEN if total_spot_pnl >= 0 else RED),
                col("FUTURES:", ORANGE),
                col("{:+.4f} USDT".format(total_futures_pnl),
                    GREEN if total_futures_pnl >= 0 else RED)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  STRATEGY BREAKDOWN
    # ══════════════════════════════════════════════════════════════════
    strat_s = calc_strategy_stats(closed)
    if strat_s:
        box_top("STRATEGY BREAKDOWN")
        row("  {} {}  {}  {}".format(
            vljust(col("Strategy", DIM), 20),
            vrjust(col("Trades", DIM), 6),
            vrjust(col("WR", DIM), 7),
            vrjust(col("PnL", DIM), 12)))
        row("  " + col("─" * 52, DIM))
        sorted_strats = sorted(strat_s.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for sname, sd in sorted_strats:
            wr = (sd["wins"] / sd["n"] * 100) if sd["n"] > 0 else 0
            pc = GREEN if sd["pnl"] >= 0 else RED
            wc = wr_col(wr)
            star = col(" ★", GOLD) if sd == sorted_strats[0][1] and sd["pnl"] > 0 else ""
            row("  {:<20} {:>6}  {}  {}{}".format(
                sname[:20], sd["n"],
                vrjust(col("{:.1f}%".format(wr), wc), 7),
                vrjust(col("{:+.4f}".format(sd["pnl"]), pc), 12),
                star))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  DAILY PnL
    # ══════════════════════════════════════════════════════════════════
    daily    = calc_daily_pnl(closed, days=7)
    has_data = any(d["trades"] > 0 for d in daily)
    if has_data:
        box_top("DAILY PnL  (7 days, UTC)")
        row("  " + sparkline([d["pnl"] for d in daily]))
        row("  {} {}  {}  {}  {}".format(
            vljust(col("Date", DIM), 12),
            vrjust(col("PnL", DIM), 9),
            vrjust(col("Trd", DIM), 5),
            vrjust(col("WR%", DIM), 6), ""))
        row("  " + col("─" * 50, DIM))
        max_abs = max(abs(v["pnl"]) for v in daily if v["trades"] > 0) or 1
        for d in daily:
            ds   = d["date"].strftime("%a %d %b")
            pnl  = d["pnl"]; n = d["trades"]; wins = d["wins"]
            wr   = (wins / n * 100) if n > 0 else 0
            pc   = GREEN if pnl > 0 else (RED if pnl < 0 else DIM)
            sign = "+" if pnl >= 0 else ""
            bar_l= int(abs(pnl) / max_abs * 14) if n > 0 else 0
            bar  = col("█" * bar_l, GREEN if pnl > 0 else RED) if bar_l else col("·", DIM)
            if n == 0:
                row("  {:<12} {}  {}  {}  {}".format(
                    ds,
                    vrjust(col("--", DIM), 9),
                    vrjust(col("0", DIM), 5),
                    vrjust(col("--", DIM), 6),
                    col("no trades", DIM)))
            else:
                row("  {:<12} {}  {}  {}  {}".format(
                    ds,
                    vrjust(col("{}{:.4f}".format(sign, pnl), pc), 9),
                    vrjust(col(str(n), WHITE), 5),
                    vrjust("{:.1f}%".format(wr), 6),
                    bar))
        wk_pnl = sum(d["pnl"] for d in daily); wk_t = sum(d["trades"] for d in daily)
        wk_w   = sum(d["wins"] for d in daily); wk_wr = (wk_w / wk_t * 100) if wk_t else 0
        wc     = GREEN if wk_pnl >= 0 else RED
        row("  " + col("─" * 50, DIM))
        row("  {}  {}  {}  {}".format(
            vljust(col("WEEK TOTAL", BOLD + WHITE), 12),
            vrjust(col("{}{:.4f}".format("+" if wk_pnl >= 0 else "", wk_pnl), wc), 9),
            vrjust(col(str(wk_t), WHITE), 5),
            vrjust("{:.1f}%".format(wk_wr), 6)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WEEKLY PERFORMANCE  (rolling 7d vs prior 7d)
    # ══════════════════════════════════════════════════════════════════
    wk = calc_weekly_stats(closed)
    tw, lw = wk["this_week"], wk["last_week"]
    if tw["n"] > 0 or lw["n"] > 0:
        box_top("WEEKLY PERFORMANCE")
        # Column widths: label=11, trd=5, W:L=7, WR%=7, PnL=10, PF=6, AvgW=8, AvgL=8, Best=8, Worst=8
        row("  {} {} {} {} {} {} {} {} {} {}".format(
            vljust(col("Period", DIM), 11),
            vrjust(col("Trd",   DIM),  5),
            vrjust(col("W:L",   DIM),  7),
            vrjust(col("WR%",   DIM),  7),
            vrjust(col("PnL",   DIM), 10),
            vrjust(col("PF",    DIM),  6),
            vrjust(col("AvgW",  DIM),  8),
            vrjust(col("AvgL",  DIM),  8),
            vrjust(col("Best",  DIM),  8),
            vrjust(col("Worst", DIM),  8)))
        row("  " + col("─" * (state.DASH_WIDTH - 4), DIM))

        def _row(label, b, label_col=BOLD + WHITE):
            if b["n"] == 0:
                row("  {} {} {} {} {} {} {} {} {} {}".format(
                    vljust(col(label, label_col), 11),
                    vrjust(col("0",  DIM),  5),
                    vrjust(col("--", DIM),  7),
                    vrjust(col("--", DIM),  7),
                    vrjust(col("--", DIM), 10),
                    vrjust(col("--", DIM),  6),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8)))
                return
            pc  = GREEN if b["pnl"] >= 0 else RED
            pfc = GREEN if b["pf"] >= 1.0 else RED
            wrc = wr_col(b["wr"])
            row("  {} {} {} {} {} {} {} {} {} {}".format(
                vljust(col(label, label_col), 11),
                vrjust(col(str(b["n"]), WHITE), 5),
                vrjust(col("{}:{}".format(b["wins"], b["losses"]), WHITE), 7),
                vrjust(col("{:.1f}%".format(b["wr"]), wrc), 7),
                vrjust(col("{}{:.4f}".format("+" if b["pnl"] >= 0 else "", b["pnl"]), pc), 10),
                vrjust(col("{:.2f}".format(b["pf"]) if b["pf"] < 999 else "∞", pfc), 6),
                vrjust(col("{:.3f}".format(b["avg_win"]),  GREEN), 8),
                vrjust(col("{:.3f}".format(b["avg_loss"]), RED),   8),
                vrjust(col("{:+.3f}".format(b["best"]),    GREEN), 8),
                vrjust(col("{:+.3f}".format(b["worst"]),   RED),   8)))

        _row("This week", tw, BOLD + WHITE)
        _row("Last week", lw, DIM)

        # Δ comparison row
        if lw["n"] > 0 and tw["n"] > 0:
            d_n   = tw["n"]   - lw["n"]
            d_pnl = tw["pnl"] - lw["pnl"]
            d_wr  = tw["wr"]  - lw["wr"]
            d_pf  = tw["pf"]  - lw["pf"] if (lw["pf"] < 999 and tw["pf"] < 999) else 0.0
            row("  " + col("─" * (state.DASH_WIDTH - 4), DIM))
            row("  {} {} {} {} {} {}".format(
                vljust(col("Δ vs prev", BOLD + CYAN), 11),
                vrjust(col("{:+d}".format(d_n), GREEN if d_n >= 0 else RED), 5),
                " " * 7,
                vrjust(col("{:+.1f}".format(d_wr), GREEN if d_wr >= 0 else RED), 7),
                vrjust(col("{:+.4f}".format(d_pnl), GREEN if d_pnl >= 0 else RED), 10),
                vrjust(col("{:+.2f}".format(d_pf) if d_pf else "--",
                           GREEN if d_pf >= 0 else RED), 6)))

        # Best / worst day this week
        if wk["best_day"] or wk["worst_day"]:
            row("  " + col("─" * (state.DASH_WIDTH - 4), DIM))
            if wk["best_day"]:
                bd = wk["best_day"]
                row("  {} {} on {} ({} trades)".format(
                    col("Best day :", DIM),
                    col("{:+.4f} USDT".format(bd["pnl"]), GREEN),
                    col(bd["date"].strftime("%a %d %b"), WHITE),
                    bd["trades"]))
            if wk["worst_day"]:
                wd = wk["worst_day"]
                row("  {} {} on {} ({} trades)".format(
                    col("Worst day:", DIM),
                    col("{:+.4f} USDT".format(wd["pnl"]), RED),
                    col(wd["date"].strftime("%a %d %b"), WHITE),
                    wd["trades"]))
            row("  {} {} / 7".format(
                col("Active   :", DIM),
                col(str(wk["active_days"]), WHITE)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  HOURLY HEATMAP
    # ══════════════════════════════════════════════════════════════════
    hourly = calc_hourly_heatmap(closed)
    if hourly:
        box_top("HOURLY HEATMAP  (UTC)")
        sorted_hours = sorted(hourly.items(), key=lambda x: x[1]["pnl"])
        worst_3 = [(h, d) for h, d in sorted_hours[:3] if d["pnl"] < 0]
        best_3  = [(h, d) for h, d in sorted_hours[-3:] if d["pnl"] > 0][::-1]
        if best_3:
            parts = ["{}h {:+.4f}({})".format(h, d["pnl"], d["n"]) for h, d in best_3]
            row("  Best  : " + col("  ".join(parts), GREEN))
        if worst_3:
            parts = ["{}h {:+.4f}({})".format(h, d["pnl"], d["n"]) for h, d in worst_3]
            row("  Worst : " + col("  ".join(parts), RED))
        vals = [hourly.get(h, {"pnl": 0})["pnl"] for h in range(24)]
        cells = []
        for h in range(24):
            v = vals[h]
            if v > 0:
                cells.append(col("█", GREEN))
            elif v < 0:
                cells.append(col("█", RED))
            else:
                cells.append(col("·", DIM))
        row("  " + col("0", DIM) + "".join(cells) + col("23", DIM))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  RECENT TRADES & MARKET INFO (side by side conceptually)
    # ══════════════════════════════════════════════════════════════════
    box_top("RECENT TRADES  (last 8)")
    # 2026-05-20: exclude pure-import phantom entries (reconciled_no_context,
    # reconciled_from_exchange) — those are position-tracker sync events, not
    # real trades, and they cluttered the panel with confusing BUY records
    # (e.g. "BUY AAVE +1.3310 reconciled_no_context" alongside real short closes).
    recent = sorted(
        [t for t in closed if (t.get("close_reason") or "") not in _DASH_RECONCILE_REASONS],
        key=lambda x: x.get("close_time", 0), reverse=True
    )[:8]
    if recent:
        for t in recent:
            sym    = t.get("symbol", "?"); pnl = _whole_pnl(t)
            side   = (t.get("side") or "?").upper()
            raw_s  = t.get("strategy", "?") or "?"
            strat  = raw_s.split("|")[0][:14]
            prof   = raw_s.split("|")[1] if "|" in raw_s else ""
            reason = t.get("close_reason", "?"); paper = t.get("paper_trade", True)
            ct     = t.get("close_time"); ex_n = (t.get("exchange") or "").lower()
            ts     = datetime.fromtimestamp(ct).strftime("%m/%d %H:%M") if ct else "--:--"
            sc     = GREEN if side == "BUY" else RED
            tag    = col("P", PURPLE) if paper else col("L", GREEN + BOLD)
            prof_s = col("[{}]".format(prof[:4].upper()), YELLOW) if prof else ""
            ec     = col(ex_n[:3].upper(), EX_COLOUR.get(ex_n, DIM))
            at     = _asset_tag(sym)
            row("  {} [{}] {} {} {} {:<12}{} {}  ({})".format(
                col(ts, DIM), tag, ec, prof_s, col(side, sc), sym, at,
                pnl_str_short(pnl), col(reason, DIM)))
    else:
        row("  " + col("No closed trades yet", DIM))
    box_bot()
    print()

    # MARKET INTELLIGENCE (Fear&Greed, trending, news) — soft-disabled Phase 1.6:
    # legacy news_cache.json may be absent or stale after De-Emotion; never crash.
    if isinstance(news, dict) and news.get("fear_greed"):
        try:
            box_top("MARKET INTELLIGENCE")
            fg = news.get("fear_greed") or {}
            glb = news.get("global") or {}
            chg = glb.get("market_cap_change_24h", 0) if isinstance(glb, dict) else 0
            row("  Fear & Greed: {}   MCap 24h: {}".format(
                fg_str(fg.get("value", 50) if isinstance(fg, dict) else 50),
                col("{:+.2f}%".format(chg), GREEN if chg >= 0 else RED)))
            trend = news.get("trending") or []
            if isinstance(trend, list) and trend:
                row("  Trending: " + "  ".join(
                    col(c.get("symbol", "?"), YELLOW)
                    for c in trend[:5] if isinstance(c, dict)))
            headlines = news.get("news") or []
            if isinstance(headlines, list):
                for a in headlines[:2]:
                    if not isinstance(a, dict):
                        continue
                    sent = a.get("sentiment", 0)
                    icon = (col("▲", GREEN) if sent > 0
                            else (col("▼", RED) if sent < 0 else col("·", DIM)))
                    row("  {} {}  {}".format(
                        icon, str(a.get("title", ""))[:60],
                        col(str(a.get("source", ""))[:12], DIM)))
            box_bot()
            print()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  MCP BRAIN STATUS
    # ══════════════════════════════════════════════════════════════════
    mcp_state = _file_cache.load("data/mcp_state.json")
    mcp_acc   = _file_cache.load("data/mcp_accuracy.json")
    kelly_st  = _file_cache.load("data/kelly_stats.json")
    has_mcp   = mcp_state or mcp_acc or kelly_st
    if has_mcp:
        box_top("MCP BRAIN  &  KELLY CRITERION")
        # MCP Brain decisions summary
        if mcp_state:
            raw_decs = mcp_state.get("decisions", {})
            # Normalize: can be dict {coin: {...}} or list [{...}, ...]
            if isinstance(raw_decs, dict):
                decs = raw_decs
            elif isinstance(raw_decs, list):
                decs = {}
                for item in raw_decs:
                    if isinstance(item, dict):
                        coin = item.get("coin") or item.get("symbol") or "?"
                        decs[coin] = item
            else:
                decs = {}
            saved_at = mcp_state.get("saved_at", 0)
            age_min = (time.time() - saved_at) / 60 if saved_at else 999
            buys  = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "BUY")
            sells = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "SELL")
            holds = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "HOLD")
            age_c = GREEN if age_min < 5 else (YELLOW if age_min < 15 else RED)
            row("  {} {} BUY  {} SELL  {} HOLD  {}".format(
                col("MCP Brain:", BOLD + PURPLE),
                col(str(buys), GREEN), col(str(sells), RED), col(str(holds), DIM),
                col("{:.0f}m ago".format(age_min), age_c)))
            # Show top BUY/SELL signals
            active = [(c, d) for c, d in decs.items()
                      if isinstance(d, dict) and d.get("action") in ("BUY", "SELL")]
            active.sort(key=lambda x: x[1].get("confidence", 0), reverse=True)
            if active:
                parts = []
                for coin, d in active[:6]:
                    ac = GREEN if d["action"] == "BUY" else RED
                    conf = d.get("confidence", 0)
                    parts.append("{} {} {:.0f}%".format(
                        col(coin, WHITE), col(d["action"], ac), conf * 100))
                row("  " + col("└─", DIM) + " " + "  ".join(parts))
        # MCP Accuracy
        if mcp_acc:
            # mcp_accuracy.json is a list of trade records — compute stats
            if isinstance(mcp_acc, list):
                resolved = [r for r in mcp_acc
                            if isinstance(r, dict) and r.get("resolved")
                            and r.get("outcome") != "expired"]
                recent   = resolved[-50:]
                total_a  = len(recent)
                wins_a   = sum(1 for r in recent if r.get("outcome") == "win")
                losses_a = sum(1 for r in recent if r.get("outcome") == "loss")
                flat_a   = sum(1 for r in recent if r.get("outcome") == "flat")
            elif isinstance(mcp_acc, dict):
                total_a  = mcp_acc.get("total", 0)
                wins_a   = mcp_acc.get("wins", mcp_acc.get("correct", 0))
                losses_a = mcp_acc.get("losses", 0)
                flat_a   = mcp_acc.get("flat", 0)
            else:
                total_a = wins_a = losses_a = flat_a = 0
            wr_a = (wins_a / total_a * 100) if total_a > 0 else 0
            ac = GREEN if wr_a >= 55 else (YELLOW if wr_a >= 45 else RED)
            row("  {} {}W/{}L/{}F ({})  WR:{}".format(
                col("MCP Accuracy:", WHITE), wins_a, losses_a, flat_a, total_a,
                col("{:.1f}%".format(wr_a), ac)))
        # Kelly criterion stats
        if kelly_st:
            box_mid("KELLY STATS")
            row("  {} {}  {}  {}  {}".format(
                vljust(col("Strategy", DIM), 18),
                vrjust(col("Trades", DIM), 6),
                vrjust(col("WR%",    DIM), 6),
                vrjust(col("R-Mult", DIM), 8),
                vrjust(col("Kelly%", DIM), 10)))
            row("  " + col("─" * 56, DIM))
            for strat_name, stats in sorted(kelly_st.items(),
                    key=lambda x: x[1].get("wins", 0) + x[1].get("losses", 0), reverse=True):
                if not isinstance(stats, dict):
                    continue
                w = stats.get("wins", 0); l = stats.get("losses", 0)
                total_k = w + l
                if total_k == 0:
                    continue
                wr_k = w / total_k * 100
                tw = stats.get("total_win", 0); tl = stats.get("total_loss", 0)
                avg_w = tw / max(w, 1); avg_l = tl / max(l, 1)
                r_mult = avg_w / avg_l if avg_l > 0 else 0
                p = w / total_k; q = 1 - p
                kelly_f = ((p * r_mult - q) / r_mult * 100) if r_mult > 0 else 0
                kc = GREEN if kelly_f > 0 else RED
                wrc = GREEN if wr_k >= 55 else (YELLOW if wr_k >= 45 else RED)
                row("  {:<18} {:>6}  {}  {:>8.2f}  {}".format(
                    strat_name[:18], total_k,
                    vrjust(col("{:.1f}%".format(wr_k), wrc), 6),
                    r_mult,
                    vrjust(col("{:+.1f}%".format(kelly_f), kc), 10)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  MODEL GATE  (calibrated LR+GBM ensemble)
    # ══════════════════════════════════════════════════════════════════
    try:
        import json as _json
        from pathlib import Path as _P
        try:
            from config import MODEL_GATE as _MG
        except Exception:
            _MG = {"enabled": False, "threshold_futures": 0.55,
                   "threshold_spot": 0.58, "shadow_only": False}
        rows_to_show: list[tuple[str, dict]] = []
        for mk in ("futures", "spot"):
            latest = _P(f"data/models/ensemble_{mk}_latest.json")
            if not latest.exists():
                continue
            try:
                ptr = _json.loads(latest.read_text())
                art = _P(ptr.get("artifact_path") or "")
                if not art.exists():
                    continue
                payload = _json.loads(art.read_text())
                metrics = payload.get("metrics") or {}
                rows_to_show.append((mk, {
                    "version": payload.get("model_version"),
                    "auc":     metrics.get("auc_ensemble"),
                    "wr":      metrics.get("wr_at_0_55_ensemble"),
                    "n":       metrics.get("n_oos_ensemble"),
                    "thr":     _MG.get(f"threshold_{mk}"),
                }))
            except Exception:
                continue
        if rows_to_show:
            box_top("MODEL GATE  (LR+GBM ensemble)")
            mode_lbl = "SHADOW-ONLY" if _MG.get("shadow_only") else (
                "ACTIVE" if _MG.get("enabled") else "DISABLED")
            row("  {} {}".format(
                vljust(col("State:", DIM), 12),
                col(mode_lbl, GREEN if mode_lbl == "ACTIVE" else YELLOW)
            ))
            row("  {} {} {} {} {} {}".format(
                vljust(col("Market", DIM), 8),
                vljust(col("Version",     DIM), 28),
                vrjust(col("AUC",         DIM),  6),
                vrjust(col("WR@0.55",     DIM),  8),
                vrjust(col("n_oos",       DIM),  6),
                vrjust(col("τ",           DIM),  5),
            ))
            for mk, d in rows_to_show:
                ver = (d["version"] or "")[:28]
                auc = "{:.3f}".format(d["auc"]) if d["auc"] is not None else "-"
                wr = "{:.3f}".format(d["wr"]) if d["wr"] is not None else "-"
                n = str(d["n"] or "-")
                thr = "{:.2f}".format(d["thr"]) if d["thr"] is not None else "-"
                row("  {} {} {} {} {} {}".format(
                    vljust(mk, 8), vljust(ver, 28),
                    vrjust(auc, 6), vrjust(wr, 8),
                    vrjust(n, 6), vrjust(thr, 5),
                ))
            # Drift alert if present.
            drift_p = _P("data/model_drift_alert.json")
            if drift_p.exists():
                try:
                    di = _json.loads(drift_p.read_text())
                    row("  " + col(
                        f"DRIFT: predicted_WR={di.get('predicted_wr', 0):.3f} "
                        f"vs realized={di.get('realized_wr', 0):.3f} "
                        f"(gap={di.get('gap', 0):.3f})", RED + BOLD))
                except Exception:
                    pass
            box_bot()
            print()
    except Exception:
        # Dashboard panels must not crash the renderer.
        pass

    # ══════════════════════════════════════════════════════════════════
    #  MARKET REGIME
    # ══════════════════════════════════════════════════════════════════
    regime_data = fetcher.regime_data()
    if regime_data:
        REGIME_ICON = {
            "trending_up":   (GREEN,  "TREND UP "),
            "trending_down": (RED,    "TREND DN "),
            "ranging":       (YELLOW, "RANGING  "),
            "volatile":      (RED + BOLD, "VOLATILE "),
            "unknown":       (DIM,    "UNKNOWN  "),
        }
        # "(1h)" so the gates panel's 4h BTC trend and this 1h classifier
        # aren't misread as contradicting each other (2026-06-11).
        box_top("MARKET REGIME  (1h ADX + Hurst + Volatility)")
        row("  {} {} {}  {}  {}  {}".format(
            vljust(col("Symbol",     DIM), 12),
            vljust(col("Regime",     DIM), 14),
            vrjust(col("ADX",        DIM),  5),
            vrjust(col("Hurst",      DIM),  6),
            vrjust(col("Vol",        DIM),  9),
            col("Strategies", DIM)))
        row("  " + col("─" * 66, DIM))
        for sym in sorted(regime_data.keys()):
            rd = regime_data[sym]
            regime = rd.get("regime", "unknown")
            ic_c, ic_t = REGIME_ICON.get(regime, (DIM, "?        "))
            adx_v = rd.get("adx", 0)
            hurst_v = rd.get("hurst", 0.5)
            vol_r = rd.get("volatility", "vol_normal")
            atr_p = rd.get("atr_pct", 0)
            rec = rd.get("recommendation")

            # ADX color
            adx_c = GREEN if adx_v >= 25 else (YELLOW if adx_v >= 20 else DIM)
            # Hurst color: >0.55 green (trending), <0.45 blue (MR), else dim
            hurst_c = GREEN if hurst_v > 0.55 else (BLUE if hurst_v < 0.45 else DIM)
            # Vol color
            vol_icons = {"vol_low": (CYAN, "LOW"), "vol_normal": (DIM, "NORM"),
                         "vol_high": (ORANGE, "HIGH"), "vol_extreme": (RED + BOLD, "EXTR")}
            vc, vt = vol_icons.get(vol_r, (DIM, "?"))

            # Recommended strategies: show first 3
            rec_s = ""
            if rec:
                short_names = [r.replace("_futures","(F)").replace("_spot","(S)")[:14] for r in rec[:3]]
                rec_s = col(", ".join(short_names), DIM)
            elif rec is not None and not rec:
                rec_s = col("ALL PAUSED", RED)

            row("  {:<12} {} {}  {}  {}  {}".format(
                sym[:12],
                vljust(col(ic_t, ic_c), 14),
                vrjust(col("{:.1f}".format(adx_v),  adx_c),   5),
                vrjust(col("{:.3f}".format(hurst_v), hurst_c), 6),
                vrjust(col("{:>4} {:.2f}%".format(vt, atr_p), vc), 9),
                rec_s))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  CORRELATION EXPOSURE
    # ══════════════════════════════════════════════════════════════════
    if open_pos:
        try:
            _corr_mod = _load_core_module("correlation_manager")
            if not _corr_mod:
                raise ImportError("correlation_manager not found")
            CORRELATION_GROUPS = _corr_mod.CORRELATION_GROUPS
            group_usage = {}
            for gname, gdata in CORRELATION_GROUPS.items():
                count = 0
                notional = 0.0
                for p in open_pos:
                    base = p.get("symbol", "").split("/")[0].upper()
                    if base in gdata["assets"]:
                        count += 1
                        sz = float(p.get("size", 0) or 0)
                        ep = float(p.get("entry_price", 0) or 0)
                        notional += sz * ep
                if count > 0:
                    group_usage[gname] = {
                        "count": count, "notional": notional,
                        "max_pct": gdata["max_group_pct"],
                        "assets": gdata["assets"],
                    }

            if group_usage:
                box_top("CORRELATION EXPOSURE")
                # 2026-06-22: compare group exposure to EQUITY (account
                # balance), matching the bot's cap semantics
                # (correlation_manager: current_pct = group_notional / balance,
                # capped at max_group_pct of balance). The old denominator was
                # the sum of open notionals, so Used% was a share-of-open-book
                # measured against a fraction-of-balance cap — apples to oranges
                # (e.g. one lone position always read ~100% "used").
                equity_denom = total_live
                if equity_denom <= 0:
                    equity_denom = sum(
                        float(p.get("size", 0) or 0) * float(p.get("entry_price", 0) or 0)
                        for p in open_pos) or 1.0

                row("  {} {}  {}  {}  {}  {}".format(
                    vljust(col("Group",    DIM), 14),
                    vrjust(col("Pos",      DIM),  5),
                    vrjust(col("Notional", DIM), 10),
                    vrjust(col("Used%",    DIM),  6),
                    vrjust(col("Max%",     DIM),  6),
                    col("Fill", DIM)))
                row("  " + col("─" * 62, DIM))
                for gname in sorted(group_usage.keys()):
                    gu = group_usage[gname]
                    used_pct = gu["notional"] / equity_denom * 100 if equity_denom > 0 else 0
                    max_pct = gu["max_pct"] * 100
                    fill_ratio = min(used_pct / max_pct, 1.0) if max_pct > 0 else 0
                    bar_len = int(fill_ratio * 16)
                    bar_rem = 16 - bar_len
                    if fill_ratio >= 0.9:
                        bar_c = RED
                    elif fill_ratio >= 0.6:
                        bar_c = YELLOW
                    else:
                        bar_c = GREEN
                    bar_s = col("█" * bar_len, bar_c) + col("░" * bar_rem, DIM)
                    row("  {:<14} {:>5}  {:>9.2f}$  {:>5.1f}%  {:>5.0f}%  {}".format(
                        gname, gu["count"], gu["notional"],
                        used_pct, max_pct, bar_s))
                box_bot()
                print()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  FUNDING RATES
    # ══════════════════════════════════════════════════════════════════
    funding = fetcher.funding_rates()
    if funding:
        box_top("FUNDING RATES  (per 8h)")
        # Collect all symbols across exchanges
        all_fsyms = set()
        for rates in funding.values():
            all_fsyms.update(rates.keys())
        all_fsyms = sorted(all_fsyms)

        # Header — pad text BEFORE applying ANSI colors to keep alignment
        ex_names = sorted(funding.keys())
        hdr_parts = col("{:<16}".format("Symbol"), DIM)
        for en in ex_names:
            ec = EX_COLOUR.get(en, WHITE)
            hdr_parts += " " + col("{:>10}".format(en[:8].upper()), ec)
        hdr_parts += "  {}".format(col("Signal", DIM))
        row("  " + hdr_parts)
        row("  " + col("─" * (16 + 12 * len(ex_names) + 10), DIM))

        for sym in all_fsyms[:8]:
            short_sym = sym.split("/")[0]
            parts = "{:<16}".format(short_sym)
            max_rate = 0
            min_rate = 0
            for en in ex_names:
                rate = funding.get(en, {}).get(sym)
                if rate is not None:
                    r_pct = rate * 100
                    max_rate = max(max_rate, rate)
                    min_rate = min(min_rate, rate)
                    if abs(r_pct) >= 0.1:
                        rc = RED + BOLD if r_pct > 0 else GREEN + BOLD
                    elif abs(r_pct) >= 0.05:
                        rc = RED if r_pct > 0 else GREEN
                    else:
                        rc = DIM
                    parts += " " + col("{:>10}".format("{:+.4f}%".format(r_pct)), rc)
                else:
                    parts += " " + col("{:>10}".format("--"), DIM)

            # Signal (check extreme thresholds first)
            if max_rate >= 0.001:
                sig = col("SHORT", RED + BOLD)
            elif min_rate <= -0.001:
                sig = col("LONG", GREEN + BOLD)
            elif max_rate >= 0.0005:
                sig = col("short", RED)
            elif min_rate <= -0.0005:
                sig = col("long", GREEN)
            else:
                sig = col("neutral", DIM)
            parts += "  {}".format(sig)
            row("  " + parts)
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  EQUITY CURVE  (ASCII)
    # ══════════════════════════════════════════════════════════════════
    if closed and len(closed) >= 3:
      try:
        import numpy as np
        # Build equity curve from closed trades. 2026-06-11: apply the same
        # real-trade filter as PERFORMANCE/Daily/Weekly (reconcile imports
        # otherwise pollute the curve in LIVE) and use whole-trade PnL.
        sorted_closed = sorted(_filter_real_trades(closed),
                               key=lambda x: x.get("close_time", 0))
        equity = []
        bal_curve = 0.0
        for t in sorted_closed:
            bal_curve += _whole_pnl(t)
            equity.append(bal_curve)

        # Only show last 50 trades for readability
        eq_show = equity[-50:]
        if len(eq_show) >= 3:
            box_top("EQUITY CURVE  (last {} trades)".format(len(eq_show)))

            # Compute Sharpe/Sortino from PnL series
            pnl_series = [_whole_pnl(t) for t in sorted_closed[-50:]]
            pnl_arr = np.array(pnl_series)
            mean_pnl = np.mean(pnl_arr)
            std_pnl = np.std(pnl_arr)
            trades_per_yr = 365 * 4
            sharpe = (mean_pnl / std_pnl * np.sqrt(trades_per_yr)) if std_pnl > 0 else 0
            downside = pnl_arr[pnl_arr < 0]
            ds_std = np.std(downside) if len(downside) > 0 else 0
            sortino = (mean_pnl / ds_std * np.sqrt(trades_per_yr)) if ds_std > 0 else 0

            # Max drawdown — 2026-06-22: compute over the SAME window the panel
            # actually draws (eq_show = last N) so MaxDD agrees with the curve
            # and the Sharpe/Sortino printed beside it. Previously this looped
            # the full inception-to-date `equity`, so a deep early drawdown was
            # reported under a panel titled "(last N trades)". Seed the peak
            # with eq_show[0] (not 0.0) since eq_show holds absolute cumulative
            # equity that can be negative.
            peak_eq = eq_show[0]
            max_dd = 0.0
            for e in eq_show:
                peak_eq = max(peak_eq, e)
                dd = peak_eq - e
                max_dd = max(max_dd, dd)

            # ASCII chart: 8 rows height
            chart_h = 8
            chart_w = min(len(eq_show), W - 10)
            # Resample if needed
            if len(eq_show) > chart_w:
                step = len(eq_show) / chart_w
                sampled = [eq_show[int(i * step)] for i in range(chart_w)]
            else:
                sampled = eq_show

            mn = min(sampled)
            mx = max(sampled)
            rng = mx - mn if mx != mn else 1

            for r in range(chart_h):
                threshold = mx - (r / (chart_h - 1)) * rng
                line_chars = []
                for v in sampled:
                    if v >= threshold:
                        if v >= 0:
                            line_chars.append(col("█", GREEN))
                        else:
                            line_chars.append(col("█", RED))
                    else:
                        line_chars.append(" ")
                # Y-axis label
                if r == 0:
                    label = "{:>+8.2f}".format(mx)
                elif r == chart_h - 1:
                    label = "{:>+8.2f}".format(mn)
                elif r == chart_h // 2:
                    mid_val = (mx + mn) / 2
                    label = "{:>+8.2f}".format(mid_val)
                else:
                    label = "        "
                row("{}{}{}".format(col(label, DIM), col("│", DIM), "".join(line_chars)))

            # X-axis
            row("{}{}".format(" " * 8, col("└" + "─" * len(sampled), DIM)))

            # Stats below chart
            sc = GREEN if sharpe > 1 else (YELLOW if sharpe > 0 else RED)
            soc = GREEN if sortino > 1 else (YELLOW if sortino > 0 else RED)
            dd_c = GREEN if max_dd < 5 else (YELLOW if max_dd < 15 else RED)
            row("  Sharpe: {}  Sortino: {}  MaxDD: {}  Cumul: {}".format(
                col("{:.2f}".format(sharpe), sc),
                col("{:.2f}".format(sortino), soc),
                col("{:.2f} USDT".format(max_dd), dd_c),
                pnl_str_short(equity[-1]) if equity else col("--", DIM)))
            box_bot()
            print()
      except ImportError:
        pass  # numpy not available — skip equity curve panel

    # ══════════════════════════════════════════════════════════════════
    #  RISK DASHBOARD
    # ══════════════════════════════════════════════════════════════════
    risk_data = _file_cache.load("data/risk_state.json")
    # Also derive risk from positions and config
    try:
        from config import RISK as RISK_CFG
        risk_avail = True
    except Exception:
        risk_avail = False

    if risk_avail or risk_data:
        box_top("RISK DASHBOARD")

        # Drawdown bar
        if risk_data:
            dd_pct = risk_data.get("max_drawdown_pct",
                                   risk_data.get("drawdown_pct", 0)) * 100
            peak = risk_data.get("peak_balance", 0)
            daily_pnl = risk_data.get("daily_pnl", 0)
            trades_today = risk_data.get("trades_today", 0)
        else:
            dd_pct = 0
            peak = total_live
            daily_pnl = s.get("today_pnl", 0)
            trades_today = s.get("today_n", 0)

        if risk_avail:
            max_dd_limit = RISK_CFG.get("max_drawdown_pct", 0.25) * 100
            daily_loss_limit = RISK_CFG.get("max_daily_loss_pct", 0.08) * 100
            max_positions = RISK_CFG.get("max_open_positions", 15)
            max_leverage = RISK_CFG.get("futures_max_leverage", 5)
        else:
            max_dd_limit = 25
            daily_loss_limit = 8
            max_positions = 15
            max_leverage = 5

        # Drawdown bar — :.1f on the limit so 12% renders as "12.0%" and a
        # configured 1.5%-style cap renders correctly. (Prior :.0f rounded
        # 1.5 to "2", giving the user the wrong impression of the limit.)
        dd_fill = min(dd_pct / max_dd_limit, 1.0) if max_dd_limit > 0 else 0
        dd_bar_len = int(dd_fill * 20)
        dd_bar_rem = 20 - dd_bar_len
        dd_bar_c = GREEN if dd_fill < 0.5 else (YELLOW if dd_fill < 0.8 else RED)
        dd_bar = col("█" * dd_bar_len, dd_bar_c) + col("░" * dd_bar_rem, DIM)
        row("  Drawdown:    {} {:.1f}% / {:.1f}% max".format(
            dd_bar, dd_pct, max_dd_limit))

        # Daily loss bar — base the percentage on `start_balance` (which is
        # what risk_manager actually uses for the halt trigger). Prior code
        # divided by current `total_live`, which drifts away from the halt
        # trigger as PnL moves and confuses the user about how close to halt
        # they are. Falls back to total_live when start_balance is missing
        # (older risk_state.json files).
        denom = float(risk_data.get("start_balance") or 0.0) if risk_data else 0.0
        if denom <= 0:
            denom = total_live
        if denom > 0:
            daily_used = abs(min(daily_pnl, 0)) / denom * 100
        else:
            daily_used = 0
        dl_fill = min(daily_used / daily_loss_limit, 1.0) if daily_loss_limit > 0 else 0
        dl_bar_len = int(dl_fill * 20)
        dl_bar_rem = 20 - dl_bar_len
        dl_bar_c = GREEN if dl_fill < 0.5 else (YELLOW if dl_fill < 0.8 else RED)
        dl_bar = col("█" * dl_bar_len, dl_bar_c) + col("░" * dl_bar_rem, DIM)
        row("  Daily Loss:  {} {:.2f}% / {:.1f}% max  ({})".format(
            dl_bar, daily_used, daily_loss_limit,
            pnl_str_short(daily_pnl)))

        # 2026-06-11: live Portfolio Expected Shortfall vs budget (from
        # heartbeat; EWMA-cov parametric ES of the open book, soft-cap).
        try:
            _hb_es = (_file_cache.load("data/heartbeat.json") or {}).get(
                "portfolio_es") or {}
            if _hb_es.get("es_open_usd") is not None:
                _es_v = float(_hb_es.get("es_open_usd") or 0.0)
                _es_b = _hb_es.get("budget_usd")
                _es_c = GREEN if (_es_b is None or _es_v <= float(_es_b)) else RED
                row("  Portfolio ES: {}{}  (97.5%/4h, legs:{}{})".format(
                    col("${:.2f}".format(_es_v), _es_c),
                    "" if _es_b is None else " / budget ${:.2f}".format(float(_es_b)),
                    _hb_es.get("legs", 0),
                    ", taper x{:.2f}".format(float(_hb_es.get("factor", 1.0)))
                    if float(_hb_es.get("factor", 1.0)) < 1.0 else ""))
        except Exception:
            pass

        # Position usage — count ONLY futures. The bot's
        # max_open_positions cap applies to bot-tracked positions; spot
        # holdings (manual coins like SUI/BTC etc.) are not gated by it,
        # so including them here over-reports the panel as 42/8 when the
        # actual capacity used is 3/8. Match heartbeat.json's
        # `open_positions` count.
        n_open = sum(1 for p in open_pos if p.get("market_type") == "futures")
        n_spot = sum(1 for p in open_pos if p.get("market_type") == "spot")
        pos_fill = min(n_open / max_positions, 1.0) if max_positions > 0 else 0
        pos_bar_len = int(pos_fill * 20)
        pos_bar_rem = 20 - pos_bar_len
        pos_bar_c = GREEN if pos_fill < 0.6 else (YELLOW if pos_fill < 0.9 else RED)
        pos_bar = col("█" * pos_bar_len, pos_bar_c) + col("░" * pos_bar_rem, DIM)
        # Show "+N spot" suffix when manual spot holdings exist so the user
        # can see the full picture without the gauge being polluted by them.
        spot_suffix = "  ({} spot)".format(n_spot) if n_spot else ""
        row("  Positions:   {} {} / {} futures max{}".format(
            pos_bar, n_open, max_positions, col(spot_suffix, DIM)))

        # Status + opens today. 2026-06-11: risk_state's counter is OPENS
        # since UTC midnight (note_trade_opened), not closed trades — label
        # it so it isn't compared 1:1 against PERFORMANCE's closed count.
        # 2026-06-22: derive Status from risk_state.is_halted/halt_reason
        # instead of a hardcoded green "ACTIVE" — the bot can halt (daily-loss
        # breaker / manual) and the panel must not keep claiming it's running.
        _r_halted = bool(risk_data.get("is_halted")) if risk_data else False
        _r_reason = (risk_data.get("halt_reason") or "") if risk_data else ""
        if _r_halted:
            _status_cell = col("HALTED", RED + BOLD) + (
                col("  [{}]".format(_r_reason), RED) if _r_reason else "")
        else:
            _status_cell = col("ACTIVE", GREEN + BOLD)
        row("  Status: {}  MaxLev: {}x  Opens Today (UTC): {}".format(
            _status_cell, max_leverage,
            col(str(trades_today), WHITE)))

        # Live safety nets
        if _OP_MODE == "CONTROLLED_LIVE":
            try:
                from config import MAX_LOSS_PER_TRADE_USD, LEVERAGE_TIERS
                _max_pos = RISK_CFG.get("max_position_pct", 0.01) * 100
                _tiers = "  ".join(
                    "{}:{}x".format(t, v["leverage"])
                    for t, v in LEVERAGE_TIERS.items())
                row("  {}  Size: {}%  MaxLoss: ${:.2f}  Tiers: {}  Hold: 2h min".format(
                    col("LIVE SAFETY:", CYAN + BOLD), _max_pos,
                    MAX_LOSS_PER_TRADE_USD, _tiers))
            except Exception:
                pass

        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WAREHOUSE: PER-SYMBOL EDGE  (Spec §15)
    # ══════════════════════════════════════════════════════════════════
    wh = load_warehouse_stats()
    if wh and wh.get("total_trades", 0) > 0:
        box_top("PER-SYMBOL EDGE (warehouse, {})".format(wh.get("mode", "?")))
        row("  {}  candidates: {}   closed trades: {}".format(
            col("Source:", DIM),
            col(str(wh.get("total_candidates", 0)), WHITE),
            col(str(wh.get("total_trades", 0)), WHITE)))
        _cb = wh.get("current_boot")
        if _cb and _cb.get("n", 0) > 0:
            _cb_wr = _cb["wins"] / _cb["n"] * 100
            _cb_c = GREEN if _cb["net"] > 0 else (RED if _cb["net"] < 0 else DIM)
            row("  {}  n={}  WR: {:.0f}%  net: {}".format(
                col("THIS BOOT ({}):".format(_cb.get("src", "?")), CYAN + BOLD),
                _cb["n"], _cb_wr,
                col("{:+.2f}".format(_cb["net"]), _cb_c)))
        row("  {:<14} {:>4} {:>9} {:>6} {:>7} {:>9}".format(
            "Symbol", "N", "Net", "WR", "PF", "Expect"))
        for r_ in wh["per_symbol"]:
            n = int(r_["n"] or 0)
            net = float(r_["net"] or 0.0)
            gw  = float(r_["gw"]  or 0.0)
            gl  = float(r_["gl"]  or 0.0)
            wins = int(r_["wins"] or 0)
            wr = (wins / n * 100) if n else 0.0
            pf = (gw / gl) if gl > 0 else (999.0 if gw > 0 else 0.0)
            exp_ = (net / n) if n else 0.0
            net_c = GREEN if net > 0 else (RED if net < 0 else DIM)
            pf_c  = GREEN if pf >= 1.0 else RED
            row("  {:<14} {:>4d} {} {:>5.1f}% {} {}".format(
                (r_["symbol"] or "?")[:14],
                n,
                vrjust(col("{:+.2f}".format(net), net_c), 9),
                wr,
                vrjust(col(("{:.2f}".format(pf) if pf < 99 else "inf"), pf_c), 7),
                vrjust(col("{:+.4f}".format(exp_), net_c), 9)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  LOSS-CLUSTER MONITOR  (Spec §12 pauses)
    # ══════════════════════════════════════════════════════════════════
    rstate = load_risk_state()
    if (wh and wh.get("per_family")) or rstate:
        box_top("LOSS-CLUSTER MONITOR  ({})".format(wh.get("mode", "?")))
        sym_pauses = (rstate or {}).get("symbol_pauses", {}) or {}
        fam_pauses = (rstate or {}).get("family_pauses", {}) or {}
        gstreak = (rstate or {}).get("global_streak", []) or []

        # Global streak marker (last 20)
        recent_losses = 0
        for ok in reversed(gstreak):
            if not ok:
                recent_losses += 1
            else:
                break
        glob_c = GREEN if recent_losses < 2 else (YELLOW if recent_losses < 5 else RED)
        row("  {} {}   {} {}".format(
            col("Global streak:", DIM),
            col("{} consecutive losses".format(recent_losses), glob_c),
            col("last 20:", DIM),
            "".join(col("W", GREEN) if ok else col("L", RED)
                    for ok in gstreak[-20:]) or col("(none)", DIM)))

        # Per-family rollup
        now_ts = time.time()
        if wh.get("per_family"):
            row("  {:<18} {:>4} {:>9} {:>6}   {}".format(
                "Family", "N", "Net", "WR", "Pause"))
            for r_ in wh["per_family"]:
                fam = (r_["fam"] or "unknown")[:18]
                n = int(r_["n"] or 0)
                net = float(r_["net"] or 0.0)
                wins = int(r_["wins"] or 0)
                wr = (wins / n * 100) if n else 0.0
                net_c = GREEN if net > 0 else (RED if net < 0 else DIM)
                until = float(fam_pauses.get(fam, 0.0) or 0.0)
                if until > now_ts:
                    mins = int((until - now_ts) / 60)
                    pause_txt = col("PAUSED {}m".format(mins), RED + BOLD)
                else:
                    pause_txt = col("active", DIM)
                row("  {:<18} {:>4d} {} {:>5.1f}%   {}".format(
                    fam, n,
                    vrjust(col("{:+.2f}".format(net), net_c), 9),
                    wr, pause_txt))

        # Paused symbols (if any)
        live_sym_pauses = [(k, v) for k, v in sym_pauses.items() if float(v) > now_ts]
        if live_sym_pauses:
            row("  {} {}".format(
                col("Paused symbols:", DIM),
                ", ".join(col("{} ({}m)".format(k, int((float(v)-now_ts)/60)),
                              YELLOW) for k, v in live_sym_pauses[:6])))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  SLIPPAGE vs FEE RATIO  (execution-quality decay)
    # ══════════════════════════════════════════════════════════════════
    if wh and wh.get("slippage", {}).get("n", 0):
        box_top("SLIPPAGE vs FEE RATIO  ({})".format(wh.get("mode", "?")))
        slp = wh["slippage"]   # local name (not `s`) — avoids shadowing stats `s`
        n = int(slp.get("n") or 0)
        slip = float(slp.get("avg_slip") or 0.0)
        fee  = float(slp.get("avg_fee")  or 0.0)
        ratio = (slip / fee) if fee > 0 else 0.0
        # 2026-06-22: warehouse trades.slippage is recorded as 0 for every fill
        # in this build (the sim/live execution path never populates it), so a
        # 0.00x ratio is "no data", NOT "great execution". Don't paint a
        # reassuring green "healthy" on an all-zero column — label it honestly.
        slip_recorded = slip != 0.0
        if slip_recorded:
            ratio_c = GREEN if ratio < 1.0 else (YELLOW if ratio < 2.0 else RED)
            ratio_s = col("{:.2f}x".format(ratio), ratio_c)
            hint_s = col(("healthy" if ratio < 1.0 else
                          ("watch" if ratio < 2.0 else "execution decay")), ratio_c)
        else:
            ratio_s = col("n/a", DIM)
            hint_s = col("slippage not recorded", DIM)
        row("  {} {}   {} {}   {} {}".format(
            col("Avg slippage:", DIM),
            col("{:.4f} USDT".format(slip), WHITE),
            col("Avg fee:", DIM),
            col("{:.4f} USDT".format(fee), WHITE),
            col("Ratio:", DIM), ratio_s))
        row("  {} {}   {} trades sampled".format(
            col("Status:", DIM), hint_s, col(str(n), WHITE)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  EXECUTION QUALITY
    # ══════════════════════════════════════════════════════════════════
    exec_data = _file_cache.load("data/execution_stats.json")
    if exec_data:
        box_top("EXECUTION QUALITY")
        total_orders = exec_data.get("total_orders", 0)
        limit_fills = exec_data.get("limit_fills", 0)
        market_falls = exec_data.get("market_fallbacks", 0)
        twap_used = exec_data.get("twap_orders", 0)
        avg_slippage = exec_data.get("avg_slippage_pct", 0)
        spread_rejects = exec_data.get("spread_rejects", 0)
        saved_fees = exec_data.get("estimated_fee_savings", 0)

        fill_rate = (limit_fills / total_orders * 100) if total_orders > 0 else 0
        fc = GREEN if fill_rate >= 60 else (YELLOW if fill_rate >= 30 else DIM)

        row("  Orders: {}  Limit Fills: {} ({})  Market Fallback: {}".format(
            col(str(total_orders), WHITE),
            col(str(limit_fills), GREEN),
            col("{:.0f}%".format(fill_rate), fc),
            col(str(market_falls), YELLOW)))
        row("  TWAP: {}  Spread Rejects: {}  Avg Slippage: {}".format(
            col(str(twap_used), CYAN),
            col(str(spread_rejects), RED if spread_rejects > 0 else DIM),
            col("{:.4f}%".format(avg_slippage), GREEN if avg_slippage < 0.05 else YELLOW)))
        if saved_fees > 0:
            row("  {} {}".format(
                col("Est. Fee Savings:", WHITE),
                col("{:.4f} USDT".format(saved_fees), GREEN)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  DATA FEEDS  (auxiliary harvesters — health only, NOT in trade path)
    # ══════════════════════════════════════════════════════════════════
    # 2026-06-22: the bot now runs side harvesters that write data/*_status.json
    # (TV / L2 order book / option skew / liquidations). They feed research, not
    # live entries, but an operator still wants to know when one goes dark
    # (e.g. L2 currently connected:false). Renders only feeds whose status file
    # exists; fail-safe and clearly labelled so it isn't mistaken for a gate.
    try:
        _now_f = time.time()
        _feed_cells = []
        for _label, _fp in (("TV", "data/tv_status.json"),
                            ("L2", "data/l2_status.json"),
                            ("Skew", "data/skew_status.json"),
                            ("Liq", "data/liquidations_status.json")):
            _fs = _file_cache.load(_fp)
            if not _fs:
                continue
            _upd = float(_fs.get("updated") or 0.0)
            _age = (_now_f - _upd) if _upd else None
            _stale = (_age is None) or (_age > 600)   # harvesters poll well under 10m
            if _stale:
                _dot, _txt = col("●", DIM), col("stale", DIM)
            elif _fs.get("connected"):
                _dot, _txt = col("●", GREEN), col("connected", GREEN)
            else:
                _dot, _txt = col("●", YELLOW), col("down", YELLOW)
            _age_s = "{}s".format(int(_age)) if (_age is not None and _age < 600) else "—"
            _feed_cells.append("{} {} {} {}".format(
                _dot, col(_label, WHITE), _txt, col("({})".format(_age_s), DIM)))
        if _feed_cells:
            box_top("DATA FEEDS  (aux harvesters — not in trade path)")
            row("  " + "    ".join(_feed_cells))
            box_bot()
            print()
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    print(col("─" * W, DIM))
    if state._BG_LAST_ERR:
        err_show = state._BG_LAST_ERR if len(state._BG_LAST_ERR) <= W - 8 else state._BG_LAST_ERR[: W - 12] + "..."
        print("  {} {}".format(col("Fetch warning:", RED + BOLD), col(err_show, YELLOW)))

    # DRY_RUN sim-realism note: show slippage model applied to paper fills
    # so users know the paper curve already reflects LIVE execution costs.
    if dry_run:
        try:
            from config import SLIPPAGE as _SL_CFG
            if _SL_CFG.get("enabled", True):
                bp_open  = _SL_CFG.get("pct_open",      0.0005) * 10000
                bp_close = _SL_CFG.get("pct_close",     0.0005) * 10000
                bp_stop  = _SL_CFG.get("pct_stop_loss", 0.0010) * 10000
                wick_on  = "wick" if _SL_CFG.get("wick_sl_tp", True) else "poll"
                fund_on  = "fund" if _SL_CFG.get("funding",    True) else "-"
                print("  {} open {:.0f}bp close {:.0f}bp stop {:.0f}bp  [{} {}]  {}".format(
                    col("Sim-realism:", DIM),
                    bp_open, bp_close, bp_stop,
                    col(wick_on, CYAN), col(fund_on, CYAN),
                    col("(paper fills match LIVE book costs)", DIM)))
        except Exception:
            pass

    print("  {} {} Refresh:{}s {} tick #{} {} up {}".format(
        col("Ctrl+C to exit", YELLOW), col(B_V, DIM),
        state.REFRESH_SECONDS, col(B_V, DIM),
        tick, col(B_V, DIM), _uptime_str()))
    print(col("─" * W, DIM))
