from pathlib import Path


def test_engine_clears_global_scheduler_on_start_and_shutdown():
    src = (
        Path(__file__).resolve().parents[1] / "core" / "bot_engine.py"
    ).read_text(encoding="utf-8")
    run_start = src.index("def run(self):")
    shutdown_start = src.index("def _shutdown(self):", run_start)
    run_body = src[run_start:shutdown_start]
    shutdown_body = src[shutdown_start:]
    assert "schedule.clear()" in run_body
    assert "schedule.clear()" in shutdown_body
