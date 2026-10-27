import json

import pytest

from src import dashboard, store
from src.data_loader import load_all
from src.rules import load_rules
from src.segments import segment_customers


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(store, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(store, "PUBLIC", tmp_path / "offers.json")
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "dashboard.json")


def test_dashboard_numbers_add_up():
    data = load_all(); segs = segment_customers(data)
    d = dashboard.build(data, segs, load_rules())
    assert d["kpis"]["customers"] == 220
    assert sum(s["customers"] for s in d["segments"]) == 220
    assert len(d["weekly"]) == 25 and d["weekly"][0]["week"] >= "2026-04-25"   # complete weeks inside the data
    assert all(w["week"] < "2026-10-19" for w in d["weekly"])                  # unfinished week left out
    assert max(d["festival_units"]["navratri"]) == d["festival_units"]["navratri"][-1]  # Navratri week is the peak
    assert {c["category"] for c in d["categories"]} == set(data["products"].category)


def test_dashboard_rebuilds_when_offers_change():
    data = load_all(); segs = segment_customers(data)
    store.on_change(lambda: dashboard.export(data, segs))
    try:
        store.log_event("blocked", {"source": "ai_agent", "title": "Too generous", "proposed": 60, "reasons": ["60% is above the cap"]})
        d = json.loads(dashboard.OUT.read_text())
        assert d["kpis"]["guardrail_saves"] == 1 and d["audit"][0]["what"] == "Too generous"
    finally:
        store._listeners.clear()


def test_results_count_reach_uses_and_estimates():
    data = load_all()
    aov = data["customers"].set_index("customer_id").avg_order_value
    a, b = list(aov.index[:2])
    offers = [{"offer_id": "OF-1", "status": "approved", "target": "ALL", "discount_pct": 10, "sent_to": 4, "used_by": [a, b]},
              {"offer_id": "OF-2", "status": "pending", "target": "ALL", "discount_pct": 50, "sent_to": 9, "used_by": [a]}]  # not live: ignored
    r = dashboard.results(data, offers, [{"kind": "adjusted"}, {"kind": "blocked"}, {"kind": "rule_change"}])
    assert r["sent"] == 4 and r["used"] == 2 and r["use_rate_pct"] == 50.0
    assert r["est_sales"] == round(aov[a] + aov[b]) and r["est_discount"] == round((aov[a] + aov[b]) * 0.10)
    assert r["guardrail_saves"] == 2 and r["rule_changes"] == 1 and 0 < r["baseline_cart_conversion_pct"] < 100
