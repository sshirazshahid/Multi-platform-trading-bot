"""scripts/decision_reconciliation.py — provenance consumer (test-plan specs 18 + 20).

Synthetic tmp_path fixtures only: decisions JSONL (active + archive) and a tiny
sqlite warehouse built in-test. Never touches data/.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import decision_reconciliation as dr  # noqa: E402

NOW = time.time()
T0 = NOW - 3 * 86400  # first decision row in the ACTIVE file
ARCH_TS = T0 - 3600  # archive row predates it (true first instrumented ts)
OLD = NOW - 30 * 86400  # trade entries that predate all decision rows


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _decision_files(tmp_path):
    """Active file + one archive (mcp_decisions.<YYYYMMDD-HHMMSS>.jsonl)."""
    _write_jsonl(
        tmp_path / "mcp_decisions.jsonl",
        [
            {
                "ts": T0,
                "type": "portfolio",
                "decisions": {
                    "actions": [
                        {
                            "type": "OPEN",
                            "symbol": "BTC/USDT:USDT",
                            "decision_id": "d-claude-1",
                            "source": "claude",
                            "sl_pct_raw": 2.0,
                            "leverage_raw": 3,
                        },
                        {
                            "type": "OPEN",
                            "symbol": "ETH/USDT:USDT",
                            "decision_id": "d-algo-1",
                            "source": "algo",
                        },
                        {
                            "type": "OPEN",
                            "symbol": "SOL/USDT:USDT",
                            "decision_id": "d-algo-2",
                            "source": "algo",
                        },
                        {
                            "type": "HOLD",
                            "symbol": "XRP/USDT:USDT",
                            "decision_id": "d-hold-1",
                            "source": "claude",
                        },
                        {
                            "type": "OPEN",
                            "symbol": "ADA/USDT:USDT",
                            "decision_id": "d-rej-1",
                            "source": "claude",
                        },
                        {
                            "type": "OPEN",
                            "symbol": "DOGE/USDT:USDT",
                            "decision_id": "d-orphan-1",
                            "source": "claude",
                        },
                        {
                            "type": "OPEN",
                            "symbol": "LTC/USDT:USDT",
                        },  # pre-provenance: no decision_id
                    ]
                },
            },
            {
                "ts": T0 + 600,
                "type": "monitor",
                "decisions": {
                    "actions": [
                        {
                            "type": "TIGHTEN_SL",
                            "symbol": "BTC/USDT:USDT",
                            "decision_id": "d-tighten-1",
                            "source": "algo",
                        },
                    ]
                },
            },
            {
                "ts": T0 + 700,
                "type": "rejection",
                "decision_id": "d-rej-1",
                "symbol": "ADA/USDT:USDT",
                "reason": "cycle_cap",
                "stage": "bot_engine",
            },
        ],
    )
    _write_jsonl(
        tmp_path / "mcp_decisions.20260609-120000.jsonl",
        [
            {
                "ts": ARCH_TS,
                "type": "portfolio",
                "decisions": {
                    "actions": [
                        {
                            "type": "OPEN",
                            "symbol": "BNB/USDT:USDT",
                            "decision_id": "d-arch-1",
                            "source": "claude",
                        },
                    ]
                },
            },
        ],
    )
    return str(tmp_path / "mcp_decisions*.jsonl")


_SCHEMA = (
    "CREATE TABLE trades ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts_entry REAL, ts_exit REAL,"
    " symbol TEXT, side TEXT, strategy_family TEXT, status TEXT,"
    " realized_pnl REAL, r_multiple REAL, decision_id TEXT, exit_decision_id TEXT)"
)


def _wh(dirpath, rows, name="wh.sqlite"):
    db = dirpath / name
    conn = sqlite3.connect(str(db))
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO trades (ts_entry, ts_exit, symbol, side, strategy_family,"
        " status, realized_pnl, r_multiple, decision_id, exit_decision_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _taxonomy_wh(dirpath):
    return _wh(
        dirpath,
        [
            # joined trades (per-source stats)
            (
                T0 + 100,
                T0 + 200,
                "BTC/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                1.0,
                1.5,
                "d-claude-1",
                None,
            ),
            (
                T0 - 3000,
                T0 - 2000,
                "BNB/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                1.0,
                1.0,
                "d-arch-1",
                None,
            ),  # joined via ARCHIVE file
            (
                T0 + 100,
                T0 + 300,
                "ETH/USDT:USDT",
                "sell",
                "claude_portfolio",
                "CLOSED",
                -1.0,
                -1.0,
                "d-algo-1",
                None,
            ),
            (
                T0 + 100,
                T0 + 400,
                "SOL/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                2.0,
                2.0,
                "d-algo-2",
                "d-tighten-1",
            ),  # also exit-joined
            # exempt order-side rows
            (
                OLD,
                OLD + 600,
                "AVAX/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                -0.5,
                -1.0,
                None,
                None,
            ),  # predates first decision row
            (
                T0 + 500,
                T0 + 900,
                "ATOM/USDT:USDT",
                "buy",
                "reconciled_exchange",
                "CLOSED",
                0.3,
                0.4,
                None,
                None,
            ),
            (T0 + 500, T0 + 900, "ARB/USDT:USDT", "buy", "manual", "CLOSED", 0.1, 0.1, None, None),
            (T0 + 500, T0 + 900, "OP/USDT:USDT", "buy", "dca", "CLOSED", 0.1, 0.1, None, None),
            (
                T0 + 500,
                T0 + 900,
                "INJ/USDT:USDT",
                "buy",
                "rebalance",
                "CLOSED",
                0.1,
                0.1,
                None,
                None,
            ),
            # TRUE orphans (order without decision)
            (
                T0 + 800,
                T0 + 1000,
                "XLM/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                -0.2,
                -0.4,
                None,
                None,
            ),  # NULL id, post-provenance
            (
                T0 + 900,
                T0 + 1100,
                "NEAR/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                0.1,
                0.2,
                "d-missing-1",
                None,
            ),  # id absent from decision logs
        ],
    )


def _reconcile(tmp_path):
    pattern = _decision_files(tmp_path)
    dec = dr.load_decisions(pattern)
    trades = dr.load_trades(_taxonomy_wh(tmp_path))
    return dec, dr.reconcile(dec, trades, days=7, now=NOW)


# ── loading: active + archive files ──────────────────────────────────


def test_loads_active_and_archive_files(tmp_path):
    dec = dr.load_decisions(_decision_files(tmp_path))
    assert len(dec["files"]) == 2
    assert "d-arch-1" in dec["id_source"]  # archive rows are read
    assert dec["first_decision_ts"] == ARCH_TS  # earliest instrumented row wins
    assert dec["instrumented"] is True


# ── per-source stats math (spec 18/19) ───────────────────────────────


def test_per_source_stats_math(tmp_path):
    _, res = _reconcile(tmp_path)
    claude = res["per_source"]["claude"]
    algo = res["per_source"]["algo"]
    assert claude["n"] == 2 and claude["wins"] == 2
    assert claude["wr_pct"] == 100.0 and claude["mean_pnl"] == 1.0
    assert algo["n"] == 2 and algo["wins"] == 1
    assert algo["wr_pct"] == 50.0 and algo["mean_pnl"] == 0.5
    assert res["joined_trades"] == 4
    assert res["exit_joined_trades"] == 1


# ── orphan taxonomy: decision without order (spec 18) ────────────────


def test_decision_without_order_taxonomy(tmp_path):
    _, res = _reconcile(tmp_path)
    d = res["orphans"]["decision_without_order"]
    # exemptions, each class
    assert d["exempt"]["non_open_actions"] == 2  # HOLD + TIGHTEN_SL (monitor advice)
    assert d["exempt"]["rejection_rows"] == 1  # type=rejection rows themselves
    assert d["exempt"]["rejection_covered"] == 1  # d-rej-1 covered by rejection row
    assert d["exempt"]["no_decision_id"] == 1  # pre-provenance OPEN action
    # true orphan detected
    assert d["true_count"] == 1
    assert d["true"][0]["decision_id"] == "d-orphan-1"
    assert d["open_candidates"] == 6  # OPEN actions carrying an id


# ── orphan taxonomy: order without decision (spec 18) ────────────────


def test_order_without_decision_taxonomy(tmp_path):
    _, res = _reconcile(tmp_path)
    w = res["orphans"]["order_without_decision"]
    assert w["exempt"]["family_whitelist"] == 4  # reconciled_exchange/manual/dca/rebalance
    assert w["exempt"]["pre_provenance"] == 1  # ts_entry predates first decision row
    assert w["true_count"] == 2
    ids = {(t["symbol"], t["decision_id"]) for t in w["true"]}
    assert ("XLM/USDT:USDT", None) in ids  # NULL id, non-exempt family, post-cutover
    assert ("NEAR/USDT:USDT", "d-missing-1") in ids  # id missing from decision logs
    assert res["orphans"]["true_total"] == 3  # 1 decision-side + 2 order-side


# ── label quality: NULL r_multiple acceptance check (spec 20) ────────


def test_label_quality_pass(tmp_path):
    rows = [
        (
            NOW - 86400,
            NOW - 86400 + 60,
            f"S{i}/USDT:USDT",
            "buy",
            "claude_portfolio",
            "CLOSED",
            0.1,
            0.5,
            None,
            None,
        )
        for i in range(10)
    ]
    # NULL r_multiple OUTSIDE the window must not count
    rows.append(
        (OLD, OLD + 60, "OLD/USDT:USDT", "buy", "claude_portfolio", "CLOSED", 0.1, None, None, None)
    )
    lq = dr.label_quality(dr.load_trades(_wh(tmp_path, rows)), days=7, now=NOW)
    assert lq["status"] == "PASS"
    assert lq["closed_n"] == 10 and lq["null_r_multiple"] == 0
    assert lq["null_pct"] == 0.0


def test_label_quality_fail_at_threshold(tmp_path):
    rows = [
        (
            NOW - 3600 - i,
            NOW - 3500 - i,
            f"S{i}/USDT:USDT",
            "buy",
            "claude_portfolio",
            "CLOSED",
            0.1,
            0.5,
            None,
            None,
        )
        for i in range(49)
    ]
    rows.append(
        (
            NOW - 7200,
            NOW - 7100,
            "NUL/USDT:USDT",
            "buy",
            "claude_portfolio",
            "CLOSED",
            0.1,
            None,
            None,
            None,
        )
    )
    lq = dr.label_quality(dr.load_trades(_wh(tmp_path, rows)), days=7, now=NOW)
    assert lq["null_pct"] == 2.0
    assert lq["status"] == "FAIL"  # spec: PASS only when < 2%


def test_label_quality_no_data(tmp_path):
    rows = [
        (OLD, OLD + 60, "OLD/USDT:USDT", "buy", "claude_portfolio", "CLOSED", 0.1, 0.5, None, None)
    ]
    lq = dr.label_quality(dr.load_trades(_wh(tmp_path, rows)), days=7, now=NOW)
    assert lq["status"] == "NO_DATA" and lq["closed_n"] == 0


# ── CLI ───────────────────────────────────────────────────────────────


def test_cli_json_output(tmp_path, capsys):
    pattern = _decision_files(tmp_path)
    db = _taxonomy_wh(tmp_path)
    af = tmp_path / "claude_audit_failures.json"
    af.write_text(
        json.dumps({"count": 2, "last_ts": NOW, "last_err": "disk full"}), encoding="utf-8"
    )
    rc = dr.main(
        [
            "--decisions-glob",
            pattern,
            "--warehouse",
            str(db),
            "--audit-failures",
            str(af),
            "--days",
            "7",
            "--as-of",
            str(NOW),
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["audit_write_failures"] == 2
    assert out["orphans"]["true_total"] == 3
    assert out["per_source"]["claude"]["n"] == 2
    assert out["label_quality"]["status"] in ("PASS", "FAIL")


def test_cli_table_output(tmp_path, capsys):
    pattern = _decision_files(tmp_path)
    db = _taxonomy_wh(tmp_path)
    rc = dr.main(
        [
            "--decisions-glob",
            pattern,
            "--warehouse",
            str(db),
            "--audit-failures",
            str(tmp_path / "missing.json"),
            "--as-of",
            str(NOW),
        ]
    )
    assert rc == 0
    text = capsys.readouterr().out
    assert "claude" in text and "algo" in text
    assert "d-orphan-1" in text  # true orphans are LISTED
    assert "Label quality" in text
    assert "n/a" in text  # missing audit-failures file


def test_cli_graceful_when_inputs_missing(tmp_path, capsys):
    rc = dr.main(
        [
            "--decisions-glob",
            str(tmp_path / "none*.jsonl"),
            "--warehouse",
            str(tmp_path / "missing.sqlite"),
            "--audit-failures",
            str(tmp_path / "missing.json"),
        ]
    )
    assert rc == 0
    assert "0 TRUE orphan" in capsys.readouterr().out
