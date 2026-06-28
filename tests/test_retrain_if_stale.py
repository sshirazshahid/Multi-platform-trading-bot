from __future__ import annotations

import sys


def test_retrain_build_commands_both_markets():
    from scripts.retrain_if_stale import build_commands

    commands = build_commands(
        ["futures", "spot"],
        tag="unit",
        quick=True,
        skip_build=False,
    )

    assert commands[0] == [sys.executable, "scripts/build_features_dataset.py", "--print-every", "500"]
    assert any(cmd[:4] == [sys.executable, "scripts/build_labels.py", "--market", "futures"] for cmd in commands)
    assert any(cmd[:4] == [sys.executable, "scripts/build_labels.py", "--market", "spot"] for cmd in commands)
    assert commands[-1][:5] == [
        sys.executable,
        "scripts/train_models.py",
        "--market",
        "both",
        "--tag",
    ]
    assert "--quick" in commands[-1]


def test_retrain_build_commands_single_market_skip_build():
    from scripts.retrain_if_stale import build_commands

    commands = build_commands(
        ["futures"],
        tag="unit",
        quick=False,
        skip_build=True,
    )

    assert commands == [
        [
            sys.executable,
            "scripts/train_models.py",
            "--market",
            "futures",
            "--tag",
            "unit",
            "--auto-promote",
        ]
    ]
