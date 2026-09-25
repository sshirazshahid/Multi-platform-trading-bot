"""The engine's daily owner-scoreboard job: writes the report, emails it, never raises."""
from __future__ import annotations

from types import SimpleNamespace

from tests.bot_engine_source import bot_engine_implementation_source


def test_engine_schedules_scoreboard_near_end_of_utc_day():
    # 23:55 so "today" covers (nearly) the whole UTC day being reported.
    src = bot_engine_implementation_source(method="run")
    assert 'schedule.every().day.at("23:55", "UTC").do(self._run_owner_scoreboard)' in src


def test_scoreboard_job_writes_and_emails(monkeypatch, tmp_path):
    import core.engine.jobs as jobs
    import core.owner_scoreboard as sb

    report = tmp_path / "owner_scoreboard_2026-09-25.md"
    report.write_text("# Your trading bot <scoreboard> & more", encoding="utf-8")
    monkeypatch.setattr(sb, "write_scoreboard", lambda *a, **k: report)
    sent = []
    engine = SimpleNamespace(notifier=SimpleNamespace(
        send=lambda subject, body: sent.append((subject, body))))
    jobs._JobsMixin._run_owner_scoreboard(engine)
    assert len(sent) == 1
    subject, body = sent[0]
    assert "scoreboard" in subject.lower()
    assert "&lt;scoreboard&gt; &amp; more" in body  # HTML-escaped


def test_scoreboard_job_never_raises(monkeypatch):
    import core.engine.jobs as jobs
    import core.owner_scoreboard as sb

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sb, "write_scoreboard", boom)
    jobs._JobsMixin._run_owner_scoreboard(SimpleNamespace(notifier=None))


def test_cli_prints_and_saves(monkeypatch, tmp_path, capsys):
    import scripts.owner_scoreboard as cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Your trading bot" in out
    assert list((tmp_path / "reports").glob("owner_scoreboard_*.md"))
