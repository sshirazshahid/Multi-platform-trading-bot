import copy
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "validate_protection_plan.py"
SPEC = importlib.util.spec_from_file_location("validate_protection_plan", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def valid_plan():
    return {
        "instrument": {
            "price_tick": "0.1",
            "qty_step": "0.001",
            "min_qty": "0.001",
            "min_notional": "5",
            "contract_size": "1",
        },
        "position": {
            "side": "long",
            "entry_price": "60000.0",
            "filled_qty": "0.010",
        },
        "stop": {
            "trigger_price": "59400.0",
            "qty": "0.010",
            "order_type": "stop_market",
            "reduce_only": True,
            "trigger_source": "mark",
            "client_order_id": "bot-stop-1",
        },
        "targets": [
            {
                "trigger_price": "60600.0",
                "limit_price": "60599.9",
                "qty": "0.004",
                "order_type": "limit",
                "reduce_only": True,
                "trigger_source": "last",
                "client_order_id": "bot-tp-1",
            },
            {
                "trigger_price": "61200.0",
                "qty": "0.006",
                "order_type": "take_profit_market",
                "reduce_only": True,
                "trigger_source": "last",
                "client_order_id": "bot-tp-2",
            },
        ],
        "intent": {
            "persisted": True,
            "state": "pending",
            "observed_at": "2026-07-17T10:00:00Z",
            "received_at": "2026-07-17T10:00:00.020000Z",
        },
    }


def test_valid_plan_passes_with_explicit_scope():
    result = MODULE.validate_plan(valid_plan())
    assert result["valid"] is True
    assert result["errors"] == []
    assert "venue coverage not verified" in result["scope"]


def test_rejects_wrong_direction_and_quantity_gap():
    plan = valid_plan()
    plan["stop"]["trigger_price"] = "60100.0"
    plan["targets"][1]["qty"] = "0.005"
    result = MODULE.validate_plan(plan)
    assert result["valid"] is False
    assert "long stop.trigger_price must be below position.entry_price" in result["errors"]
    assert "target quantities must sum exactly to the remaining position quantity" in result["errors"]


def test_rejects_unquantized_values():
    plan = valid_plan()
    plan["targets"][0]["limit_price"] = "60599.95"
    plan["targets"][0]["qty"] = "0.0045"
    errors = MODULE.validate_plan(plan)["errors"]
    assert "targets[0].limit_price is not quantized to instrument.price_tick" in errors
    assert "targets[0].qty is not quantized to instrument.qty_step" in errors


def test_requires_reduce_only_and_unique_client_ids():
    plan = valid_plan()
    plan["targets"][0]["reduce_only"] = False
    plan["targets"][1]["client_order_id"] = "bot-tp-1"
    errors = MODULE.validate_plan(plan)["errors"]
    assert "targets[0].reduce_only must be true" in errors
    assert "client_order_id values must be unique" in errors


def test_receipt_time_cannot_make_future_observation_valid():
    plan = valid_plan()
    plan["intent"]["received_at"] = "2026-07-17T09:59:59Z"
    errors = MODULE.validate_plan(plan)["errors"]
    assert "intent.received_at must not precede intent.observed_at" in errors


def test_short_plan_direction_is_supported():
    plan = copy.deepcopy(valid_plan())
    plan["position"]["side"] = "short"
    plan["stop"]["trigger_price"] = "60600.0"
    plan["targets"][0]["trigger_price"] = "59400.0"
    plan["targets"][0]["limit_price"] = "59400.1"
    plan["targets"][1]["trigger_price"] = "58800.0"
    assert MODULE.validate_plan(plan)["valid"] is True


def test_average_fill_price_need_not_align_to_order_tick():
    plan = valid_plan()
    plan["position"]["entry_price"] = "60000.037"
    assert MODULE.validate_plan(plan)["valid"] is True


def test_stop_must_cover_remaining_quantity_or_close_position():
    plan = valid_plan()
    plan["position"]["remaining_qty"] = "0.010"
    plan["stop"]["qty"] = "0.009"
    errors = MODULE.validate_plan(plan)["errors"]
    assert "stop.qty must equal the remaining position quantity" in errors

    plan["stop"].pop("qty")
    plan["stop"]["close_position"] = True
    assert MODULE.validate_plan(plan)["valid"] is True

    plan["stop"]["qty"] = "0.010"
    assert "stop must use either close_position or qty, not both" in MODULE.validate_plan(plan)[
        "errors"
    ]


def test_verified_state_requires_query_or_private_stream_evidence():
    plan = valid_plan()
    plan["intent"]["state"] = "verified"
    assert MODULE.validate_plan(plan)["valid"] is False

    plan["intent"]["venue_evidence"] = {
        "source": "rest_query",
        "venue_order_ids": ["venue-stop-1", "venue-tp-1", "venue-tp-2"],
        "verified_at": "2026-07-17T10:00:01Z",
    }
    result = MODULE.validate_plan(plan)
    assert result["valid"] is True
    assert "venue coverage not verified" in result["scope"]
