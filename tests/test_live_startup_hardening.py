"""Offline regression tests for the CONTROLLED_LIVE startup boundary."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

import core.bot_engine as bot_engine
import config
import core.live_gate as live_gate
import exchanges.bitget_client as bitget_client
import main


def _model_config(
    *,
    mode="CONTROLLED_LIVE",
    signal="mcp",
    enabled=True,
    shadow=False,
):
    return SimpleNamespace(
        OPERATING_MODE=mode,
        SIGNAL_SOURCE=signal,
        MODEL_GATE={"enabled": enabled, "shadow_only": shadow},
    )


@pytest.mark.parametrize(
    "runtime_config",
    [
        _model_config(mode="PAPER"),
        _model_config(shadow=True),
        _model_config(enabled=False),
        _model_config(signal="machine"),
    ],
)
def test_model_readiness_gate_is_dormant_when_not_active(
    runtime_config, tmp_path
):
    live_gate.enforce_model_gate_readiness(
        runtime_config.OPERATING_MODE,
        config_module=runtime_config,
        pointer_path=tmp_path / "missing.json",
    )


def test_active_live_model_gate_rejects_missing_pointer(tmp_path):
    with pytest.raises(SystemExit, match="pointer is missing"):
        live_gate.enforce_model_gate_readiness(
            "CONTROLLED_LIVE",
            config_module=_model_config(),
            pointer_path=tmp_path / "missing.json",
        )


def test_active_live_model_gate_rejects_invalid_pointer_json(tmp_path):
    pointer = tmp_path / "latest.json"
    pointer.write_text("not-json", encoding="utf-8")
    with pytest.raises(SystemExit, match="pointer is unreadable"):
        live_gate.enforce_model_gate_readiness(
            "CONTROLLED_LIVE",
            config_module=_model_config(),
            pointer_path=pointer,
        )


def test_active_live_model_gate_rejects_missing_manifest(tmp_path):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}", encoding="utf-8")
    pointer = tmp_path / "latest.json"
    pointer.write_text(
        json.dumps(
            {
                "diag": {},
                "artifact_path": str(artifact),
                "promoted_at": 1,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="ModelManifest"):
        live_gate.enforce_model_gate_readiness(
            "CONTROLLED_LIVE",
            config_module=_model_config(),
            pointer_path=pointer,
        )


def test_active_live_model_gate_propagates_validation_reason(
    tmp_path, monkeypatch
):
    pointer = tmp_path / "latest.json"
    pointer.write_text(
        json.dumps({"diag": {}, "artifact_path": "model.json"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.promotion_gate.validate_model_pointer",
        lambda *_args, **_kwargs: (
            False,
            "latest pointer stale: 9.0d > 7d",
            {},
        ),
    )
    with pytest.raises(SystemExit, match="stale"):
        live_gate.enforce_model_gate_readiness(
            "CONTROLLED_LIVE",
            config_module=_model_config(),
            pointer_path=pointer,
        )


def test_active_live_model_gate_accepts_validated_bundle(tmp_path, monkeypatch):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}", encoding="utf-8")
    pointer = tmp_path / "latest.json"
    pointer.write_text(
        json.dumps({"diag": {}, "artifact_path": "model.json"}),
        encoding="utf-8",
    )
    observed = {}

    def validate(payload, *, market_type):
        observed.update(payload)
        assert market_type == "futures"
        return True, "OK", {}

    monkeypatch.setattr("core.promotion_gate.validate_model_pointer", validate)
    live_gate.enforce_model_gate_readiness(
        "CONTROLLED_LIVE",
        config_module=_model_config(),
        pointer_path=pointer,
    )
    assert observed["artifact_path"] == str(artifact)


def test_preconstruction_refusal_never_constructs_or_retries_engine(monkeypatch):
    import config

    constructed = []

    class MustNotConstruct:
        def __init__(self):
            constructed.append(True)
            raise AssertionError("BotEngine constructed before authorization")

    def refuse(*_args, **_kwargs):
        raise SystemExit("unsigned")

    def must_not_sleep(_seconds):
        raise AssertionError("watchdog retried a permanent gate refusal")

    monkeypatch.setattr(config, "OPERATING_MODE", "CONTROLLED_LIVE")
    monkeypatch.setattr(config, "CONTROLLED_LIVE_ENABLED", True)
    monkeypatch.setattr(bot_engine, "BotEngine", MustNotConstruct)
    monkeypatch.setattr(live_gate, "enforce_controlled_live_gate", refuse)
    monkeypatch.setattr(main.time, "sleep", must_not_sleep)

    with pytest.raises(SystemExit, match="unsigned"):
        main.run_with_watchdog()
    assert constructed == []


def test_every_authorization_gate_precedes_engine_construction(monkeypatch):
    import config

    events = []

    class Engine:
        def __init__(self):
            events.append("construct")

        def run(self):
            events.append("run")

    monkeypatch.setattr(config, "OPERATING_MODE", "CONTROLLED_LIVE")
    monkeypatch.setattr(config, "CONTROLLED_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "SIGNAL_SOURCE", "mcp")
    monkeypatch.setattr(bot_engine, "BotEngine", Engine)
    monkeypatch.setattr(
        live_gate,
        "enforce_controlled_live_gate",
        lambda *_args, **_kwargs: events.append("signed"),
    )
    monkeypatch.setattr(
        live_gate,
        "enforce_live_runtime_invariants",
        lambda *_args, **_kwargs: events.append("runtime"),
    )
    monkeypatch.setattr(
        live_gate,
        "enforce_strategy_readiness_gate",
        lambda *_args, **_kwargs: events.append("strategy"),
    )
    monkeypatch.setattr(
        live_gate,
        "enforce_model_gate_readiness",
        lambda *_args, **_kwargs: events.append("model"),
    )

    main.run_with_watchdog()

    assert events == ["signed", "runtime", "strategy", "model", "construct", "run"]


def test_live_startup_reconciliation_is_post_preflight_and_idempotent(
    monkeypatch,
):
    events = []
    engine = object.__new__(bot_engine.BotEngine)
    engine.active_exchanges = {"bybit": object()}
    engine._live_startup_reconciliation_pending = True
    engine.tracker = SimpleNamespace(
        sync_with_exchanges=lambda exchanges: (
            events.append(("sync", exchanges)) or ["pos"]
        )
    )
    engine._protect_imported_positions = lambda positions: events.append(
        ("protect", positions)
    )
    monkeypatch.setattr(config, "DRY_RUN", False)

    engine._complete_authorized_live_startup_reconciliation("PAPER")
    assert events == []
    engine._complete_authorized_live_startup_reconciliation("CONTROLLED_LIVE")
    engine._complete_authorized_live_startup_reconciliation("CONTROLLED_LIVE")
    assert events == [
        ("sync", engine.active_exchanges),
        ("protect", ["pos"]),
    ]

    init_source = inspect.getsource(bot_engine.BotEngine.__init__)
    run_source = inspect.getsource(bot_engine.BotEngine.run)
    assert ".sync_with_exchanges(" not in init_source
    assert run_source.index("enforce_live_preflight_gate(") < run_source.index(
        "_complete_authorized_live_startup_reconciliation("
    )


def test_bitget_initialization_does_not_write_position_mode(monkeypatch):
    calls = []

    class RawBitget:
        def __init__(self):
            self.options = {}

        def load_time_difference(self):
            return None

        def load_markets(self, reload=False):
            return {}

        def fetch_balance(self):
            return {}

        def set_position_mode(self, *args, **kwargs):
            calls.append((args, kwargs))

    raw = RawBitget()
    monkeypatch.setattr(bitget_client.ccxt, "bitget", lambda _config: raw)
    client = object.__new__(bitget_client.BitgetClient)
    client.api_key = "key"
    client.secret = "secret"
    client._passphrase = "passphrase"
    client._connected = False

    client._init_exchange()

    assert client._connected is True
    assert calls == []
