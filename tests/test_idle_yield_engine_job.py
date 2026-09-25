"""The engine's idle-yield job: PAPER-latched, fail-soft, reads the paper wallet only."""
from __future__ import annotations

from types import SimpleNamespace

from tests.bot_engine_source import bot_engine_implementation_source


def test_engine_schedules_idle_yield_hourly():
    src = bot_engine_implementation_source(method="run")
    assert "schedule.every(1).hours.do(self._run_idle_yield)" in src


def test_idle_yield_job_passes_paper_wallet_cash_and_mode(monkeypatch):
    import core.engine.jobs as jobs
    import core.idle_yield as iy

    seen = {}

    def fake_tick(idle_usd, *, paper, **kw):
        seen["idle"] = idle_usd
        seen["paper"] = paper
        return {"earned_usd": 0.01}

    monkeypatch.setattr(iy, "run_idle_yield_tick", fake_tick)
    monkeypatch.setattr(jobs, "DRY_RUN", True)
    engine = SimpleNamespace(
        order_mgr=SimpleNamespace(wallet=SimpleNamespace(total_balance=lambda: 4321.5)))
    jobs._JobsMixin._run_idle_yield(engine)
    assert seen == {"idle": 4321.5, "paper": True}


def test_idle_yield_job_never_raises_into_the_scheduler(monkeypatch):
    import core.engine.jobs as jobs

    def boom():
        raise RuntimeError("wallet unavailable")

    engine = SimpleNamespace(order_mgr=SimpleNamespace(wallet=SimpleNamespace(total_balance=boom)))
    jobs._JobsMixin._run_idle_yield(engine)  # must not raise


def test_idle_yield_job_is_a_noop_outside_paper(monkeypatch, tmp_path):
    import core.engine.jobs as jobs
    import core.idle_yield as iy

    real = iy.run_idle_yield_tick
    results = []

    def spy(idle_usd, **kw):
        out = real(idle_usd, path=tmp_path / "idle_yield.json", **kw)
        results.append(out)
        return out

    monkeypatch.setattr(iy, "run_idle_yield_tick", spy)
    monkeypatch.setattr(jobs, "DRY_RUN", False)
    engine = SimpleNamespace(
        order_mgr=SimpleNamespace(wallet=SimpleNamespace(total_balance=lambda: 1000.0)))
    jobs._JobsMixin._run_idle_yield(engine)
    assert results == [{"skipped": "not_paper"}]
    assert not (tmp_path / "idle_yield.json").exists()
