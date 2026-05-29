"""Adversarial probe extending scalp_replay_backtest.py.

Goal: try HARD to REFUTE the NO_EDGE finding by attacking the cost model
(maker fees) and the bracket geometry, with HONEST accounting and per-side
out-of-sample (OOS) fold robustness.

Key discipline (per advisor):
  - HONEST MAKER model: stops are MARKET orders -> the loss leg keeps taker
    fee + stop slippage; maker only saves entry/close on the win leg.
      win cost  cw = maker_entry + maker_close                (~0.0002)
      loss cost cl = maker_entry + taker_stop + stop_slip      (~0.0017)
    Asymmetric breakeven WR = (SL+cl)/(TP-cw+SL+cl).
  - Do NOT stack two optimisms: report each cohort against ITS OWN breakeven.
  - Per-side OOS folds (the refutation candidate is a SIDE = shorts).
  - A cohort REFUTES no-edge only if it clears its realistic breakeven in
    EVERY OOS fold (not a pooled best-case number).

Reuses signal + bracket machinery from scalp_replay_backtest.py.
"""

from __future__ import annotations

import numpy as np
import scalp_replay_backtest as base

SL = base.SL_PCT  # 0.008
TP = base.TP_PCT  # 0.013


# ----------------------------------------------------------------------------
# Cost models. bracket_outcome() in base applies TAKER fees + slippage inline.
# To test alternative cost models we recompute net return from the RAW
# (pre-cost) bracket outcome. We get that by re-running the scan with all
# slippage/fee zeroed, capturing the gross leg, then re-applying the chosen
# cost model based on outcome type.
# ----------------------------------------------------------------------------


def gross_outcome(df, entry_idx, side, max_hold, tiebreak="sl", start_offset=0):
    """Return (gross_return_frac, outcome_str, bars_held) with NO costs.
    Mirrors base.bracket_outcome but strips all fees/slippage so we can
    re-apply alternative cost models per outcome."""
    o = df["open"].iloc[entry_idx]
    if side == "buy":
        fill = o
        sl = fill * (1 - SL)
        tp = fill * (1 + TP)
    else:
        fill = o
        sl = fill * (1 + SL)
        tp = fill * (1 - TP)
    end = min(entry_idx + max_hold, len(df) - 1)
    for j in range(entry_idx + start_offset, end + 1):
        hi, lo = df["high"].iloc[j], df["low"].iloc[j]
        hit_sl = (lo <= sl) if side == "buy" else (hi >= sl)
        hit_tp = (hi >= tp) if side == "buy" else (lo <= tp)
        if hit_sl and hit_tp:
            first = tiebreak
        elif hit_sl:
            first = "sl"
        elif hit_tp:
            first = "tp"
        else:
            continue
        if first == "sl":
            return base._ret(side, fill, sl), "stop_loss", j - entry_idx
        return base._ret(side, fill, tp), "take_profit", j - entry_idx
    c = df["close"].iloc[end]
    return base._ret(side, fill, c), "markout", end - entry_idx


# cost models: return (win_cost, loss_cost, markout_cost) applied to gross ret
COST_MODELS = {
    # taker round trip + open/close slip on win, + stop slip on loss
    "taker_full": dict(cw=0.0012 + 0.0010, cl=0.0012 + 0.0005 + 0.0010, cm=0.0012 + 0.0010),
    # taker fees only, no slippage
    "taker_feesonly": dict(cw=0.0012, cl=0.0012, cm=0.0012),
    # HONEST maker (bybit 0.0001/leg): win=maker both legs; loss=maker entry +
    # taker stop + stop slip; markout=maker entry + taker close + close slip
    "maker_honest": dict(cw=0.0002, cl=0.0001 + 0.0006 + 0.0010, cm=0.0001 + 0.0005 + 0.0005),
    # maker but keep a close-slip tick on the TP leg (conservative maker)
    "maker_conservative": dict(
        cw=0.0002 + 0.0005, cl=0.0001 + 0.0006 + 0.0010, cm=0.0001 + 0.0005 + 0.0005
    ),
}


def be_wr(sl, tp, cw, cl):
    return (sl + cl) / (tp - cw + sl + cl)


def apply_cost(gross, outcome, model):
    m = COST_MODELS[model]
    if outcome == "take_profit":
        return gross - m["cw"]
    if outcome == "stop_loss":
        return gross - m["cl"]
    return gross - m["cm"]


def fold_split(records):
    """records: list of (ts, side, gross, outcome). Return time-thirds."""
    if len(records) < 30:
        return {}
    ts = [r[0] for r in records]
    q1, q2 = np.quantile(ts, [1 / 3, 2 / 3])
    folds = {"f1": [], "f2": [], "f3": []}
    for rec in records:
        k = "f1" if rec[0] <= q1 else ("f2" if rec[0] <= q2 else "f3")
        folds[k].append(rec)
    return folds


def cohort_stats(records, model, sl=SL, tp=TP):
    if not records:
        return None
    rets = np.array([apply_cost(g, oc, model) for _, _, g, oc in records])
    wr = (rets > 0).mean()
    ev = rets.mean()
    return dict(n=len(rets), wr=wr, ev=ev, sum=rets.sum())


def report_geometry(
    label,
    sig_fn,
    sl,
    tp,
    tiebreak="tp",
    start_offset=1,
    max_hold=base.MAX_HOLD_BARS_15M,
    hod_filter=None,
    models=("taker_full", "maker_honest"),
):
    """Run signals -> gross brackets at (sl,tp) geometry, then evaluate under
    each cost model with per-SIDE OOS folds. tiebreak/start_offset default to
    OPTIMISTIC (the most charitable resolution) so we give edge its best shot.
    hod_filter: optional set of UTC hours to keep (time-of-day filter)."""
    global SL, TP
    SL, TP = sl, tp  # gross_outcome reads module-level SL/TP
    recs = []  # (ts, side, gross, outcome)
    for sym in base.SYMS:
        df15 = base.load(sym, "15m")
        if df15 is None or len(df15) < 60:
            continue
        df1h, df4h = base.load(sym, "1h"), base.load(sym, "4h")
        for ei, side in sig_fn(df15, df1h, df4h):
            if ei >= len(df15):
                continue
            ts = df15["ts"].iloc[ei]
            if hod_filter is not None:
                hr = int((ts // 3600) % 24)
                if hr not in hod_filter:
                    continue
            g, oc, _ = gross_outcome(
                df15, ei, side, max_hold, tiebreak=tiebreak, start_offset=start_offset
            )
            recs.append((ts, side, g, oc))
    SL, TP = base.SL_PCT, base.TP_PCT  # restore
    recs.sort()
    print(f"\n{'=' * 78}\n{label}")
    print(
        f"  geometry: SL={sl * 100:.2f}% TP={tp * 100:.2f}%  R:R={tp / sl:.2f}  "
        f"max_hold={max_hold}bars  resolution=OPTIMISTIC(tp-first,skip-entry)"
    )
    if hod_filter is not None:
        print(f"  ToD filter: keep hours {sorted(hod_filter)}")
    print(f"  no-edge geom WR = {sl / (sl + tp) * 100:.1f}%  signals={len(recs)}")
    for model in models:
        m = COST_MODELS[model]
        bw = be_wr(sl, tp, m["cw"], m["cl"])
        print(f"\n  --- cost model: {model}  (breakeven WR = {bw * 100:.1f}%) ---")
        for side_label, side_key in [("ALL", None), ("LONGS", "buy"), ("SHORTS", "sell")]:
            sub = [r for r in recs if side_key is None or r[1] == side_key]
            st = cohort_stats(sub, model)
            if not st:
                continue
            pooled_clear = "PASS" if st["wr"] > bw else "fail"
            print(
                f"    {side_label:7s} pooled: n={st['n']:5d} WR={st['wr'] * 100:5.1f}% "
                f"EV={st['ev'] * 100:+.3f}% sum={st['sum'] * 100:+7.1f}%  [{pooled_clear}]"
            )
            # per-side OOS folds
            folds = fold_split(sub)
            fold_pass = []
            for fk, fv in folds.items():
                fst = cohort_stats(fv, model)
                if not fst:
                    continue
                p = fst["wr"] > bw
                fold_pass.append(p)
                print(
                    f"        {fk}: n={fst['n']:5d} WR={fst['wr'] * 100:5.1f}% "
                    f"EV={fst['ev'] * 100:+.3f}%  {'PASS' if p else 'fail'}"
                )
            if fold_pass:
                robust = all(fold_pass)
                print(f"        >> ROBUST (clears BE in ALL folds)? {'YES' if robust else 'NO'}")


def main():
    global SL, TP
    print("ADVERSARIAL SCALP EDGE PROBE")
    print("Trying to REFUTE: no exploitable edge in mechanical scalp after costs")
    print(f"symbols: {len(base.SYMS)}")

    # Reference breakevens for the base 0.8/1.3 geometry under each model
    print(f"\n{'=' * 78}\nBREAKEVEN WR by cost model @ SL=0.8% TP=1.3%")
    for k, m in COST_MODELS.items():
        print(
            f"  {k:20s}: BE WR = {be_wr(SL, TP, m['cw'], m['cl']) * 100:5.2f}%  "
            f"(cw={m['cw']:.4f} cl={m['cl']:.4f})"
        )

    # PROBE 0: base geometry, OPTIMISTIC resolution, maker vs taker, per-side OOS
    report_geometry(
        "PROBE 0  meanrev @ 0.8/1.3 (refutation candidate)",
        base.meanrev_signals,
        0.008,
        0.013,
        models=("taker_full", "taker_feesonly", "maker_honest", "maker_conservative"),
    )
    report_geometry(
        "PROBE 0b momentum @ 0.8/1.3", base.scalp_signals, 0.008, 0.013, models=("maker_honest",)
    )

    # PROBE 1: TIGHTER SL, lower R:R -> raises WR & breakeven together.
    #   0.5% SL / 1.0% TP (R:R 2.0). no-edge geom WR = 33.3%.
    report_geometry(
        "PROBE 1  meanrev TIGHTER SL @ 0.5/1.0",
        base.meanrev_signals,
        0.005,
        0.010,
        models=("taker_full", "maker_honest"),
    )

    # PROBE 2: SYMMETRIC tight scalp 0.6/0.6 (R:R 1.0). no-edge geom WR=50%.
    #   Needs WR well above 50% — pure direction bet.
    report_geometry(
        "PROBE 2  meanrev SYMMETRIC @ 0.6/0.6",
        base.meanrev_signals,
        0.006,
        0.006,
        models=("taker_full", "maker_honest"),
    )

    # PROBE 3: WIDER TP (let winners run) 0.8/2.0 (R:R 2.5). geom WR=28.6%.
    report_geometry(
        "PROBE 3  meanrev WIDE TP @ 0.8/2.0",
        base.meanrev_signals,
        0.008,
        0.020,
        models=("taker_full", "maker_honest"),
    )

    # PROBE 4: HOLD-CAP tightening — meanrev shorts, 0.8/1.3, 8-bar (2h) cap.
    report_geometry(
        "PROBE 4  meanrev SHORT HOLD-CAP 2h @ 0.8/1.3",
        base.meanrev_signals,
        0.008,
        0.013,
        max_hold=8,
        models=("taker_full", "maker_honest"),
    )

    # PROBE 5: TIME-OF-DAY filter. First find best hours empirically (in-sample
    # exploration to MAXIMIZE chance of a false positive we must then OOS-test).
    print(f"\n{'=' * 78}\nPROBE 5  HOUR-OF-DAY scan (meanrev shorts, maker_honest)")
    # gather all meanrev short records once
    SLs, TPs = 0.008, 0.013
    SL, TP = SLs, TPs
    short_recs = []
    for sym in base.SYMS:
        df15 = base.load(sym, "15m")
        if df15 is None or len(df15) < 60:
            continue
        df1h, df4h = base.load(sym, "1h"), base.load(sym, "4h")
        for ei, side in base.meanrev_signals(df15, df1h, df4h):
            if side != "sell" or ei >= len(df15):
                continue
            ts = df15["ts"].iloc[ei]
            g, oc, _ = gross_outcome(
                df15, ei, side, base.MAX_HOLD_BARS_15M, tiebreak="tp", start_offset=1
            )
            short_recs.append((ts, side, g, oc, int((ts // 3600) % 24)))
    SL, TP = base.SL_PCT, base.TP_PCT
    bw = be_wr(SLs, TPs, COST_MODELS["maker_honest"]["cw"], COST_MODELS["maker_honest"]["cl"])
    by_hour = {}
    for ts, side, g, oc, hr in short_recs:
        by_hour.setdefault(hr, []).append((ts, side, g, oc))
    good_hours = set()
    for hr in sorted(by_hour):
        st = cohort_stats(by_hour[hr], "maker_honest")
        if st and st["wr"] > bw and st["n"] >= 20:
            good_hours.add(hr)
        if st:
            mark = " <-- pos EV" if st["ev"] > 0 else ""
            print(
                f"    hour {hr:2d}: n={st['n']:4d} WR={st['wr'] * 100:5.1f}% "
                f"EV={st['ev'] * 100:+.3f}%{mark}"
            )
    print(f"  in-sample 'good' hours (WR>{bw * 100:.1f}%, n>=20): {sorted(good_hours)}")
    # Now OOS-test those cherry-picked hours per fold — the honest test
    if good_hours:
        report_geometry(
            "PROBE 5b meanrev SHORTS @ cherry-picked GOOD HOURS",
            base.meanrev_signals,
            0.008,
            0.013,
            hod_filter=good_hours,
            models=("maker_honest",),
        )


if __name__ == "__main__":
    main()
