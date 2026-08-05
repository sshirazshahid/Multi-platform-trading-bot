"""Contract tests for the Mission Deck (mission_control/static/deck.*).

The deck is a second, read-only front end served at "/" while the original
operator console moved to "/classic". Two invariants matter enough to guard:

1. Every ``/api/...`` path deck.js fetches must actually be a registered route.
   The deck has no build step and no framework, so a typo'd path fails silently
   as an empty panel rather than an error anyone notices.

2. The deck must never call ``/api/ops/*``. There is exactly one mutating
   surface in this system (the classic console), and the deck's whole safety
   argument is that it is GET-only. A stray ops call would quietly give a
   read-only status page the ability to restart the bot or arm CONTROLLED_LIVE.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mission_control.app import create_app

STATIC = Path(__file__).resolve().parents[1] / "mission_control" / "static"
DECK_JS = STATIC / "deck.js"
DECK_HTML = STATIC / "deck.html"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(root=tmp_path, token="tok", bind_host="127.0.0.1"))


def _deck_js() -> str:
    return DECK_JS.read_text(encoding="utf-8")


def _api_paths_referenced() -> set[str]:
    """Every /api/... literal in deck.js, minus query strings."""
    return {m.split("?")[0] for m in re.findall(r"[\"'`](/api/[a-zA-Z0-9_\-/]+)", _deck_js())}


def _registered_paths(app) -> set[str]:
    return {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/")}


# -- the two invariants ------------------------------------------------------

def test_every_endpoint_deck_fetches_is_registered(tmp_path):
    app = create_app(root=tmp_path, token="tok", bind_host="127.0.0.1")
    registered = _registered_paths(app)
    referenced = _api_paths_referenced()
    assert referenced, "deck.js fetches no endpoints - the regex or the file is wrong"
    missing = sorted(referenced - registered)
    assert not missing, f"deck.js fetches unregistered endpoints: {missing}"


def test_deck_never_calls_the_mutating_ops_surface():
    """The deck is read-only by construction. Operator actions live in /classic.

    Checks *referenced endpoints* rather than raw text: the file's own header
    comment says "never calls /api/ops/*", and a substring search would match
    that prose. _api_paths_referenced() is quote-anchored, so it only sees
    paths that appear as string literals — i.e. ones that could be fetched.
    """
    ops = sorted(p for p in _api_paths_referenced() if p.startswith("/api/ops"))
    assert not ops, (
        f"deck.js references mutating endpoints {ops} - the deck must stay GET-only; "
        "put operator controls in the classic console instead"
    )


# -- routing: the deck is the front door, the console is preserved -----------

def test_root_serves_the_deck(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<title>Mission Deck</title>" in res.text


def test_classic_console_is_still_reachable(client):
    """Moving "/" must not strand the only operator surface."""
    res = client.get("/classic")
    assert res.status_code == 200
    assert "<title>Mission Control</title>" in res.text


def test_deck_html_loads_its_own_assets():
    html = DECK_HTML.read_text(encoding="utf-8")
    assert "/static/deck.css" in html
    assert "/static/deck.js" in html


# -- the honesty invariant that motivated the deck's design ------------------

def test_deck_does_not_read_position_fields_the_api_never_returns():
    """A field the UI reads but the API never sends renders BLANK, not an error.

    Measured 2026-08-02: positionTile read ``current_price`` and
    ``unrealized_pnl``. Neither exists on data/positions.json open entries, and
    heartbeat.json publishes only an open-position COUNT -- so the tile showed
    "-" for PnL and a price marker frozen at an entry-derived 68.9%, looking
    live while being static. 168 passing Python tests could not see it.

    These names are pinned because no live-price source exists to feed them. If
    one is ever genuinely published, remove it from this list deliberately.
    """
    js = _deck_js()
    for field in ("current_price", "unrealized_pnl"):
        assert f"pos.{field}" not in js, (
            f"deck.js reads pos.{field}, which /api/positions never returns -- "
            "it renders blank or silently falls back to a wrong value"
        )


def test_deck_never_implies_a_live_price_it_does_not_have():
    """The position marker must be labelled as entry, not implied as current."""
    assert "entry (no live price available)" in _deck_js()


def test_deck_reads_win_rate_from_the_canonical_goal_lanes():
    """positions.closed[] is a rolling 500-row ring; deriving a windowed win
    rate from it silently truncates and disagrees with the classic console.
    The deck must take win rate / PnL from /api/goals lanes instead."""
    js = _deck_js()
    assert "paper_futures_7d" in js, "deck must name the canonical goal lane"
    assert "/api/goals" in js


# -- operator-dashboard structure (dashboard-builder) -------------------------

def test_deck_is_organized_around_operator_questions():
    """Health → performance → bottleneck → book → research; next actions deep-link
    into /classic?ops=… rather than calling mutating endpoints from the deck."""
    html = DECK_HTML.read_text(encoding="utf-8")
    js = _deck_js()
    for marker in (
        'id="health-strip"',
        'id="next-actions"',
        ">Health</p>",
        ">Performance</p>",
        ">Bottleneck</p>",
        ">Book</p>",
        "Research &amp; risk",
        'href="/classic"',
    ):
        assert marker in html, f"deck.html missing operator-structure marker: {marker}"
    assert "renderHealth" in js and "renderNextActions" in js
    assert "/classic?ops=clear-incident" in js
    assert "p-mtsi" not in js and "MTSI" not in js
    assert "mtsi" not in _api_paths_referenced()


def test_classic_console_handles_ops_deep_links():
    """Deck CTAs land on /classic?ops=<id>; classic must open the ops rail."""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'get("ops")' in app_js
    assert "$(\"ops-rail\").classList.add(\"show\")" in app_js or '$("ops-rail").classList.add("show")' in app_js
    assert '[data-op="${opsId}"]' in app_js
