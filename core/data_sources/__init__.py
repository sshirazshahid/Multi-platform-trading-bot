"""Derivatives / flow data sources (genuinely-new information classes).

Separate from core/data_feeds/* (which mcp_brain consumes). Modules here harvest
data NOT derived from price — open interest, liquidations-proxy, long/short
ratio, funding term — and persist forward snapshots so a leakage-clean
falsification can run later (the public APIs only retain ~21 days, so history
must be accumulated forward; see research/kronos_eval_2026_05_29.md for the
falsification discipline this feeds).
"""
