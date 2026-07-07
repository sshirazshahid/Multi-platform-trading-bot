from pathlib import Path


def test_valuation_sensitivity_headers_do_not_double_suffix(tmp_path, monkeypatch):
    import research.research_engine as research_engine

    out = tmp_path / "daily_report.html"
    monkeypatch.setattr(research_engine, "REPORT_HTML", out)

    report = {
        "generated_at": "2026-07-03T00:00:00",
        "fear_greed": {},
        "market_data": {},
        "screener": {"top15": []},
        "valuation": {
            "price": 1,
            "scenarios": {},
            "sensitivity": [{"wacc": "10%", "2x": 1, "4x": 2}],
            "base_verdict": "Fair",
        },
        "risk": {},
        "catalyst": {},
        "portfolio": {},
        "quant": {},
        "yield": {},
        "competitive": {},
    }

    research_engine.ResearchEngine()._generate_html(report)

    html = Path(out).read_text(encoding="utf-8")
    assert "<th>2x</th>" in html
    assert "<th>4x</th>" in html
    assert "2xx" not in html
    assert "4xx" not in html
