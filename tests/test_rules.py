import pytest

from src.data_loader import load_all
from src.rules import load_rules, validate_offer

data, rules = load_all(), load_rules()


def offer(**kw):
    base = dict(kind="public", title="t", message="m", target="ALL", product_ids=["P001"], discount_pct=10,
                valid_from="2026-10-22", valid_to="2026-10-30", code="X", rationale="r", expected_impact="i", source="rule_engine")
    return {**base, **kw}


def test_ok():
    assert validate_offer(offer(), data, rules).ok


def test_60_percent_blocked_but_adjustable():
    v = validate_offer(offer(discount_pct=60), data, rules)
    assert not v.ok and v.adjusted_offer and v.adjusted_offer["discount_pct"] <= 15


def test_category_cap():  # Nuts capped at 12
    assert not validate_offer(offer(product_ids=["P004"], discount_pct=13), data, rules).ok


def test_margin_floor():  # Peri-Peri makhana margin ~41%: allowed up to 15 by category
    v = validate_offer(offer(product_ids=["P002"], discount_pct=16), data, rules)
    assert not v.ok


def test_low_stock_blocked():
    r2 = {**rules, "global": {**rules["global"], "min_stock_for_offer": 30}}
    assert not validate_offer(offer(product_ids=["P020"]), data, r2).ok


def test_unknown_and_empty_and_zero():
    assert not validate_offer(offer(product_ids=["PXXX"]), data, rules).ok
    assert not validate_offer(offer(product_ids=[]), data, rules).ok
    assert not validate_offer(offer(discount_pct=0), data, rules).ok


def test_expired_and_bad_dates():
    assert not validate_offer(offer(valid_to="2026-10-01", valid_from="2026-09-20"), data, rules).ok
    assert not validate_offer(offer(valid_from="2026-10-30", valid_to="2026-10-25"), data, rules).ok


def test_opted_out_customer():
    cid = data["customers"][~data["customers"].marketing_opt_in].customer_id.iloc[0]
    assert not validate_offer(offer(kind="personal", target=cid), data, rules).ok


# ---------- edge cases (P5) ----------
def test_exactly_at_the_limit_passes_one_above_is_lowered():
    assert validate_offer(offer(discount_pct=15), data, rules).ok          # P001 is Makhana: cap 15%
    v = validate_offer(offer(discount_pct=16), data, rules)
    assert not v.ok and v.adjusted_offer["discount_pct"] == 15


def personal(**kw):
    cid = data["customers"].query("marketing_opt_in").customer_id.iloc[0]
    return offer(kind="personal", target=cid, **kw), cid


def test_cooldown_blocks_a_second_offer_within_7_days():
    o, cid = personal()
    two_days_ago = [{"target": cid, "status": "approved", "created_at": "2026-10-20T10:00:00"}]
    v = validate_offer(o, data, rules, two_days_ago)
    assert not v.ok and any("cooldown" in r for r in v.reasons)
    eight_days_ago = [{"target": cid, "status": "approved", "created_at": "2026-10-14T10:00:00"}]
    assert validate_offer(o, data, rules, eight_days_ago).ok


def test_monthly_cap_blocks_the_fourth_offer():
    o, cid = personal()
    three = [{"target": cid, "status": "approved", "created_at": d} for d in ("2026-09-26T10:00:00", "2026-10-04T10:00:00", "2026-10-12T10:00:00")]
    v = validate_offer(o, data, rules, three)
    assert not v.ok and any("this month" in r for r in v.reasons)


def test_rejected_offers_do_not_count_towards_cooldown():
    o, cid = personal()
    assert validate_offer(o, data, rules, [{"target": cid, "status": "rejected", "created_at": "2026-10-21T10:00:00"}]).ok


def test_check_command_explains_and_catches_broken_files(tmp_path):
    from src.rules import check_file
    lines = check_file()
    assert any("Nuts: at most 12% off" in l for l in lines)
    bad = tmp_path / "rules.yaml"
    bad.write_text("global:\n  max_discount_pct: 20\n   min_margin_pct_after_discount: [15\n")
    with pytest.raises(ValueError, match="can't be read"):
        check_file(bad)
    too_high = tmp_path / "high.yaml"
    too_high.write_text(open(rules_path()).read().replace("Nuts: 12", "Nuts: 30"))
    with pytest.raises(ValueError, match="above the overall limit"):
        check_file(too_high)


def rules_path():
    from src.data_loader import ROOT
    return ROOT / "rules.yaml"


# ---------- AI output is untrusted (P3) ----------
def fake_claude(monkeypatch, text, stop="end_turn"):
    import anthropic
    from types import SimpleNamespace
    reply = SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply)))
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")


def test_malformed_ai_reply_falls_back_to_the_rules(monkeypatch, tmp_path):
    from src import agent, recommender, store
    from src.segments import segment_customers
    monkeypatch.setattr(store, "STATE", tmp_path / "s.json"); monkeypatch.setattr(store, "AUDIT", tmp_path / "a.jsonl")
    fake_claude(monkeypatch, "Sure! Here's a great offer: {discount_pct: lots")       # not valid JSON
    assert agent.personalise({}, {}, rules) is None
    segs = segment_customers(data)
    cid = segs[segs.segment == "at_risk"].customer_id.iloc[0]
    res = recommender.offer_for_customer(data, segs, rules, cid)
    assert res["used_ai"] is False and res["offer"] and validate_offer(res["offer"], data, rules).ok
    assert recommender.ai_campaigns(data, segs, rules) is None                         # campaigns fall back too


def test_cut_off_ai_reply_is_dropped(monkeypatch):
    from src import agent
    fake_claude(monkeypatch, '{"discount_pct": 10, "title": "Hi"', stop="max_tokens")
    assert agent.personalise({}, {}, rules) is None


def test_ai_asking_for_60_percent_is_lowered(monkeypatch, tmp_path):
    from src import recommender, store
    from src.segments import segment_customers
    monkeypatch.setattr(store, "STATE", tmp_path / "s.json"); monkeypatch.setattr(store, "AUDIT", tmp_path / "a.jsonl")
    fake_claude(monkeypatch, '{"discount_pct": 60, "title": "60% off for you", "message": "Take 60% off!", "rationale": "go big"}')
    segs = segment_customers(data)
    cid = segs[segs.segment == "lapsed"].customer_id.iloc[0]
    res = recommender.offer_for_customer(data, segs, rules, cid)
    assert res["used_ai"] and res["offer"]["discount_pct"] < 60 and "60%" not in res["offer"]["message"]
    assert validate_offer(res["offer"], data, rules, []).ok
