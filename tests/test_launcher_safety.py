from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8").lower()


def test_auto_restart_is_only_a_compatibility_shim():
    src = _text("auto_restart.bat")

    assert 'call "%~dp0tradingbot.bat" --supervise' in src
    assert "main.py" not in src
    assert "start_all" not in src
    assert ":loop" not in src
    assert "__pycache__" not in src


def test_trading_bot_is_the_only_launcher_entrypoint():
    src = _text("TradingBot.bat")

    assert 'if /i "%~1"=="--supervise" goto :supervise' in src
    assert r"scripts\launcher_supervisor.py" in src
    assert 'call "%bot_dir%start_all.bat"' not in src
    assert 'launcher_supervisor.py" run --restart' in src


def test_powershell_launcher_delegates_to_same_safe_supervisor():
    src = _text("TradingBot.ps1")

    assert "operating_mode" in src
    assert "paper" in src and "observation" in src
    assert "scripts\\launcher_supervisor.py" in src
    assert "run --restart" in src
    assert 'join-path $bot_dir "main.py"' not in src
    assert "type yes to confirm live trading" not in src


def test_batch_launchers_never_put_secrets_on_python_command_lines():
    src = _text("TradingBot.bat")

    forbidden = (
        "%helper% write_env",
        "%helper% update_keys",
        "%helper% update_email",
        '"!bin_key!"',
        '"!bin_sec!"',
        '"!by_key!"',
        '"!by_sec!"',
        '"!bg_key!"',
        '"!bg_sec!"',
        '"!bg_pass!"',
        '"!gm_pass2!"',
    )
    assert not [token for token in forbidden if token in src]
    assert "secure-setup" in src
    assert "secure-keys" in src
    assert "secure-email" in src


def test_launchers_do_not_recursively_delete_python_caches():
    launchers = (
        "TradingBot.bat",
        "auto_restart.bat",
        "start_all.bat",
        # run_confluence_paper.bat deleted 2026-08-04 (Phase 2a): its runner
        # script was already absent, so the batch only printed "disabled".
        "scripts/start_all.ps1",
    )

    for relative_path in launchers:
        src = _text(relative_path)
        assert "__pycache__" not in src, relative_path
        assert "shutil.rmtree" not in src, relative_path
        assert "for /d /r" not in src, relative_path


def test_helper_startup_is_serialized_and_deduplicated():
    src = _text("scripts/start_all.ps1")

    assert "threading.mutex" in src
    assert "waitone" in src
    assert "get-ciminstance win32_process" in src
    assert "-erroraction stop" in src
    assert "refusing auxiliary startup" in src
    assert "already running" in src


def test_launcher_rejects_live_mode_activation(monkeypatch):
    from scripts import launcher_supervisor

    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        launcher_supervisor.bot_helper,
        "set_env_var",
        lambda key, value: writes.append((key, value)),
    )

    with pytest.raises(ValueError, match="PAPER or OBSERVATION"):
        launcher_supervisor.set_safe_mode("CONTROLLED_LIVE")

    assert writes == []


@pytest.mark.parametrize("mode", ["PAPER", "OBSERVATION"])
def test_launcher_preserves_safe_modes(monkeypatch, mode):
    from scripts import launcher_supervisor

    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        launcher_supervisor.bot_helper,
        "set_env_var",
        lambda key, value: writes.append((key, value)),
    )

    launcher_supervisor.set_safe_mode(mode)

    assert writes == [
        ("OPERATING_MODE", mode),
        ("CONTROLLED_LIVE_ENABLED", "false"),
    ]


def test_secure_key_prompt_updates_in_process(monkeypatch):
    from scripts import launcher_supervisor

    updates: list[tuple[str, str, str, str]] = []
    answers = iter(("api&key!%", "secret^with|metacharacters"))
    monkeypatch.setattr(
        launcher_supervisor.bot_helper,
        "update_keys",
        lambda *args: updates.append(args),
    )

    result = launcher_supervisor.secure_keys(
        "binance", secret_input=lambda _: next(answers)
    )

    assert result == 0
    assert updates == [
        ("binance", "api&key!%", "secret^with|metacharacters", "")
    ]


def test_setup_defaults_do_not_replace_existing_credentials(tmp_path, monkeypatch):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "BINANCE_API_KEY=existing-key\nBINANCE_SECRET_KEY=existing-secret\n",
        encoding="utf-8",
    )
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        launcher_supervisor.bot_helper,
        "set_env_var",
        lambda key, value: writes.append((key, value)),
    )

    launcher_supervisor._ensure_env_defaults(tmp_path)

    written_keys = {key for key, _ in writes}
    assert "BINANCE_API_KEY" not in written_keys
    assert "BINANCE_SECRET_KEY" not in written_keys
    assert "OPERATING_MODE" in written_keys


def test_supervisor_does_not_start_workers_without_singleton_lock(tmp_path):
    from scripts import launcher_supervisor

    calls: list[str] = []

    result = launcher_supervisor.supervise(
        root=tmp_path,
        restart=True,
        acquire_lock=lambda *args, **kwargs: None,
        worker_is_running=lambda *args, **kwargs: False,
        start_helpers=lambda *args, **kwargs: calls.append("helpers") or 0,
        run_worker=lambda *args, **kwargs: calls.append("worker") or 0,
        sleep=lambda *_: calls.append("sleep"),
    )

    assert result == launcher_supervisor.ALREADY_RUNNING
    assert calls == []


def test_supervisor_does_not_duplicate_an_existing_main_worker(tmp_path):
    from scripts import launcher_supervisor

    calls: list[str] = []

    class Lock:
        def close(self):
            calls.append("close")

    result = launcher_supervisor.supervise(
        root=tmp_path,
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *args, **kwargs: True,
        start_helpers=lambda *args, **kwargs: calls.append("helpers") or 0,
        run_worker=lambda *args, **kwargs: calls.append("worker") or 0,
    )

    assert result == launcher_supervisor.ALREADY_RUNNING
    assert calls == ["close"]


def test_supervisor_fails_closed_when_worker_discovery_fails(tmp_path):
    from scripts import launcher_supervisor

    calls: list[str] = []

    class Lock:
        def close(self):
            calls.append("close")

    result = launcher_supervisor.supervise(
        root=tmp_path,
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *args, **kwargs: None,
        start_helpers=lambda *args, **kwargs: calls.append("helpers") or 0,
        run_worker=lambda *args, **kwargs: calls.append("worker") or 0,
    )

    assert result == launcher_supervisor.PROCESS_CHECK_FAILED
    assert calls == ["close"]


def test_supervisor_starts_helpers_once_across_worker_restarts(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\nCONTROLLED_LIVE_ENABLED=false\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    exits = iter((23, 0))

    class Lock:
        def close(self):
            calls.append("close")

    result = launcher_supervisor.supervise(
        root=tmp_path,
        restart=True,
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *args, **kwargs: False,
        start_helpers=lambda *args, **kwargs: calls.append("helpers") or 0,
        run_worker=lambda *args, **kwargs: calls.append("worker") or next(exits),
        sleep=lambda *_: calls.append("sleep"),
    )

    assert result == 0
    assert calls == ["helpers", "worker", "sleep", "worker", "close"]


def test_supervisor_refuses_controlled_live_before_helpers_start(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=CONTROLLED_LIVE\nCONTROLLED_LIVE_ENABLED=true\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    class Lock:
        def close(self):
            calls.append("close")

    result = launcher_supervisor.supervise(
        root=tmp_path,
        restart=False,
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *args, **kwargs: False,
        start_helpers=lambda *args, **kwargs: calls.append("helpers") or 0,
        run_worker=lambda *args, **kwargs: calls.append("worker") or 0,
    )

    assert result == launcher_supervisor.LIVE_MODE_REFUSED
    assert calls == ["close"]


def test_worker_environment_overrides_inherited_live_mode(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\nCONTROLLED_LIVE_ENABLED=false\n",
        encoding="utf-8",
    )

    child_env = launcher_supervisor._safe_worker_env(
        tmp_path,
        environ={
            "OPERATING_MODE": "CONTROLLED_LIVE",
            "CONTROLLED_LIVE_ENABLED": "true",
            "PRESERVE_ME": "yes",
        },
    )

    assert child_env["OPERATING_MODE"] == "PAPER"
    assert child_env["CONTROLLED_LIVE_ENABLED"] == "false"
    assert child_env["DRY_RUN"] == "true"
    assert child_env["PAPER_TRADING_PROFILE"] == "STANDARD"
    assert child_env["PRESERVE_ME"] == "yes"


def test_worker_environment_pins_research_knobs_from_dotenv(tmp_path):
    """Stale inherited values must not override .env research knobs."""
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\n"
        "PAPER_TRADING_PROFILE=MAX_FLOW_BAND\n"
        "ENTRY_POLICY=APPROVED_PAPER\n"
        "APPROVED_PAPER_STRATEGIES=F1\n"
        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback\n"
        "MCP_ENTRY_MIN_SCORE=66\n"
        "ACCURACY_TARGET_MODE=true\n"
        "BAND_REGIME_FILTER_ENABLED=true\n"
        "SCALP_MODE_ENABLED=false\n"
        "UNIVERSE_FLOW_LOOSEN_V1=true\n"
        "SHADOW_PULLBACK_PROBE_ENABLED=false\n"
        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN=5\n"
        "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX=200\n"
        "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK=true\n",
        encoding="utf-8",
    )
    child = launcher_supervisor._safe_worker_env(
        tmp_path,
        environ={
            "OPERATING_MODE": "PAPER",
            # Stale A1 drift: inherited AGGRESSIVE_RESEARCH must lose to .env.
            "PAPER_TRADING_PROFILE": "AGGRESSIVE_RESEARCH",
            "ENTRY_POLICY": "SHADOW_ONLY",
            "APPROVED_PAPER_STRATEGIES": "F1,mcp_registry,algo_det",
            "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE": "strict",
            "MCP_ENTRY_MIN_SCORE": "50",
            "ACCURACY_TARGET_MODE": "false",
            "BAND_REGIME_FILTER_ENABLED": "false",
            "SCALP_MODE_ENABLED": "true",
            "UNIVERSE_FLOW_LOOSEN_V1": "false",
            "SHADOW_PULLBACK_PROBE_ENABLED": "true",
            "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN": "0",
            "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX": "999999",
            "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK": "false",
        },
    )
    assert child["PAPER_TRADING_PROFILE"] == "MAX_FLOW_BAND"
    assert child["ENTRY_POLICY"] == "APPROVED_PAPER"
    assert child["APPROVED_PAPER_STRATEGIES"] == "F1"
    assert child["MCP_DIRECTIONAL_ECONOMIC_GATE_MODE"] == "paper_fallback"
    assert child["MCP_ENTRY_MIN_SCORE"] == "66"
    assert child["ACCURACY_TARGET_MODE"] == "true"
    assert child["BAND_REGIME_FILTER_ENABLED"] == "true"
    assert child["SCALP_MODE_ENABLED"] == "false"
    assert child["UNIVERSE_FLOW_LOOSEN_V1"] == "true"
    assert child["SHADOW_PULLBACK_PROBE_ENABLED"] == "false"
    assert child["BROAD_UNIVERSE_ABS_MOVE_USDT_MIN"] == "5"
    assert child["BROAD_UNIVERSE_ABS_MOVE_USDT_MAX"] == "200"
    assert child["BROAD_UNIVERSE_PREFER_ABS_USDT_RANK"] == "true"


def test_worker_environment_coerces_pinned_research_profile_under_observation(tmp_path):
    """Pin from .env must not leave MAX_FLOW_BAND active under OBSERVATION."""
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=OBSERVATION\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\n",
        encoding="utf-8",
    )
    obs = launcher_supervisor._safe_worker_env(
        tmp_path,
        environ={"PAPER_TRADING_PROFILE": "AGGRESSIVE_RESEARCH"},
    )
    assert obs["OPERATING_MODE"] == "OBSERVATION"
    assert obs["PAPER_TRADING_PROFILE"] == "STANDARD"


def test_worker_environment_accepts_aggressive_profile_only_in_paper(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    child = launcher_supervisor._safe_worker_env(
        tmp_path, environ={"PAPER_TRADING_PROFILE": "aggressive-research"}
    )
    assert child["PAPER_TRADING_PROFILE"] == "AGGRESSIVE_RESEARCH"
    assert child["OPERATING_MODE"] == "PAPER"
    assert child["CONTROLLED_LIVE_ENABLED"] == "false"

    (tmp_path / ".env").write_text("OPERATING_MODE=OBSERVATION\n", encoding="utf-8")
    # Coerce (don't raise): leftover research profile must not brick OBSERVATION spawn.
    obs = launcher_supervisor._safe_worker_env(
        tmp_path, environ={"PAPER_TRADING_PROFILE": "aggressive-research"}
    )
    assert obs["OPERATING_MODE"] == "OBSERVATION"
    assert obs["PAPER_TRADING_PROFILE"] == "STANDARD"


def test_supervisor_pins_and_restores_aggressive_profile_environment(tmp_path, monkeypatch):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    monkeypatch.setenv("PAPER_TRADING_PROFILE", "STANDARD")
    monkeypatch.delenv("PAPER_PROFILE_STARTED_AT", raising=False)
    observed = []

    class Lock:
        def close(self):
            pass

    result = launcher_supervisor.supervise(
        root=tmp_path,
        paper_profile="aggressive-research",
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *_: False,
        start_helpers=lambda *_: 0,
        run_worker=lambda *_: observed.append((
            os.environ["PAPER_TRADING_PROFILE"],
            float(os.environ["PAPER_PROFILE_STARTED_AT"]),
        )) or 0,
    )
    assert result == 0
    assert observed[0][0] == "AGGRESSIVE_RESEARCH"
    assert observed[0][1] > 0
    assert os.environ["PAPER_TRADING_PROFILE"] == "STANDARD"
    assert "PAPER_PROFILE_STARTED_AT" not in os.environ


def test_worker_environment_rechecks_file_mode_before_spawn(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text(
        "OPERATING_MODE=CONTROLLED_LIVE\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="unsafe mode"):
        launcher_supervisor._safe_worker_env(tmp_path, environ={})


def test_external_heartbeat_monitor_has_startup_grace_and_detects_stalls(tmp_path):
    from scripts import launcher_supervisor

    started_at = 1_000.0
    assert not launcher_supervisor._heartbeat_is_stale(
        tmp_path,
        now=started_at + 599,
        worker_started_at=started_at,
    )
    assert launcher_supervisor._heartbeat_is_stale(
        tmp_path,
        now=started_at + 601,
        worker_started_at=started_at,
    )

    heartbeat = tmp_path / "data" / "heartbeat.json"
    heartbeat.parent.mkdir()
    heartbeat.write_text("{}", encoding="utf-8")
    os.utime(heartbeat, (started_at + 500, started_at + 500))

    assert not launcher_supervisor._heartbeat_is_stale(
        tmp_path,
        now=started_at + 650,
        worker_started_at=started_at,
    )
    assert launcher_supervisor._heartbeat_is_stale(
        tmp_path,
        now=started_at + 1500,
        worker_started_at=started_at,
    )
    assert launcher_supervisor.HEARTBEAT_MAX_AGE_SECONDS >= 900
    assert launcher_supervisor.HEARTBEAT_FAILURE_STRIKES == 3


def test_monitored_worker_stops_only_its_owned_child_on_stale_heartbeat(tmp_path):
    from scripts import launcher_supervisor

    calls: list[object] = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, *, timeout):
            calls.append(("wait", timeout))
            return 0

        def kill(self):
            calls.append("kill")

    def popen(args, *, cwd, env):
        calls.append(("popen", args, cwd, env))
        return Process()

    timestamps = iter((1_000.0, 1_601.0, 1_606.0, 1_611.0))
    result = launcher_supervisor._run_worker(
        tmp_path,
        popen=popen,
        ensure_helpers=lambda _: calls.append("helpers") or 0,
        clock=lambda: next(timestamps),
        sleep=lambda _: calls.append("sleep"),
    )

    assert result == launcher_supervisor.WORKER_HUNG_EXIT_CODE
    assert calls[0][0] == "popen"
    assert calls[1:] == [
        "sleep",
        "sleep",
        "terminate",
        ("wait", launcher_supervisor.WORKER_STOP_GRACE_SECONDS),
    ]


def test_monitored_worker_rechecks_auxiliary_collectors_while_main_is_healthy(
    tmp_path,
):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    calls = []

    class Process:
        def __init__(self):
            self.polls = iter((None, None, 0))

        def poll(self):
            return next(self.polls)

    def popen(args, *, cwd, env):
        calls.append(("popen", env["OPERATING_MODE"]))
        return Process()

    ticks = iter((0.0, 100.0, 301.0))
    result = launcher_supervisor._run_worker(
        tmp_path,
        popen=popen,
        ensure_helpers=lambda _: calls.append("helpers") or 0,
        clock=lambda: next(ticks),
        sleep=lambda _: calls.append("sleep"),
    )

    assert result == 0
    assert calls == [("popen", "PAPER"), "sleep", "helpers", "sleep"]


def test_supervisor_uses_bounded_exponential_backoff_for_crash_loops(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    exits = iter((11, 12, 0))
    sleeps: list[float] = []
    ticks = iter((0.0, 1.0, 1.0, 2.0, 2.0, 3.0))

    class Lock:
        def close(self):
            pass

    result = launcher_supervisor.supervise(
        root=tmp_path,
        restart=True,
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *args, **kwargs: False,
        start_helpers=lambda *args, **kwargs: 0,
        run_worker=lambda *args, **kwargs: next(exits),
        sleep=sleeps.append,
        monotonic=lambda: next(ticks),
    )

    assert result == 0
    assert sleeps == [
        launcher_supervisor.RESTART_DELAY_SECONDS,
        launcher_supervisor.RESTART_DELAY_SECONDS * 2,
    ]
