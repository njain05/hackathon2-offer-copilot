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
