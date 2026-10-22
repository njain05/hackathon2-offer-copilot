"""Dashboard API: drafts, group offers, approve/reject/end, one-customer offers, rule edits, auth."""
import json
import shutil
import threading
import urllib.error
import urllib.request

import pytest

from src import api, audience, bot, rules as rules_mod, store
from src.rules import load_rules


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(store, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(store, "PUBLIC", tmp_path / "offers.json")
    rules_file = tmp_path / "rules.yaml"
    shutil.copy(rules_mod.ROOT / "rules.yaml", rules_file)
    real_load = rules_mod.load_rules
    monkeypatch.setattr(api, "load_rules", lambda path=rules_file: real_load(path))
    monkeypatch.setattr(audience, "store", store)
    real_apply = rules_mod.apply_change
    monkeypatch.setattr(api, "apply_change", lambda k, v, c="": real_apply(k, v, c, rules_file))
    monkeypatch.setattr(api, "deliver", lambda o: len(bot.audience_chats(o)))
    monkeypatch.setenv("SELLER_CODE", "secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return rules_file


def ludhiana_draft(**kw):
    return {"audience_mode": "group", "filters": {"cities": ["Ludhiana"], "segments": ["lapsed", "at_risk"]},
            "product_ids": ["P004", "P005"], "discount_pct": 10, "valid_days": 10, "title": "Ludhiana comeback", "message": "Nuts for you", **kw}


def test_audience_filters_and_protects_opted_out():
    sel = audience.select(bot.DATA, bot.SEGS, load_rules(), {"cities": ["Ludhiana"]})
    c = bot.DATA["customers"].set_index("customer_id")
    assert sel["count"] > 0 and all(c.loc[x, "city"] == "Ludhiana" and c.loc[x, "marketing_opt_in"] for x in sel["customer_ids"])
    assert sel["matched"] == sel["count"] + sel["excluded_opt_out"] + sel["excluded_cooldown"]
    assert "Ludhiana" in sel["label"]


def test_check_lowers_discount_above_category_cap():
    res = api.check(ludhiana_draft(discount_pct=15))     # Nuts cap is 12%
    assert not res["ok"] and res["adjusted_discount"] == 12 and "12% off" in res["preview"]


def test_create_approve_delivers_to_group_shoppers_only():
    sel = audience.select(bot.DATA, bot.SEGS, load_rules(), ludhiana_draft()["filters"])
    store.assign_shopper(1001, sel["customer_ids"][0])                     # a classmate playing a Ludhiana customer
    outsider = bot.DATA["customers"].query("city != 'Ludhiana' and marketing_opt_in").customer_id.iloc[0]
    store.assign_shopper(1002, outsider)
    o = api.create(ludhiana_draft())["offer"]
    assert o["status"] == "pending" and o["target"] == "GROUP" and o["customer_ids"] == sel["customer_ids"]
    res = api.decide(o["offer_id"], "approve")
    assert res["offer"]["status"] == "approved" and res["sent"] == 1          # only the Ludhiana shopper
    assert o["offer_id"] not in {x["offer_id"] for x in json.loads(store.PUBLIC.read_text())}  # group offers stay private
    # the same customers are now in cooldown: a second group offer to them is left with nobody
    again = audience.select(bot.DATA, bot.SEGS, load_rules(), ludhiana_draft()["filters"])
    assert again["count"] == 0 and again["excluded_cooldown"] == sel["count"]


def test_group_offer_with_opted_out_customer_is_blocked():
    r = load_rules()
    opted_out = bot.DATA["customers"].query("not marketing_opt_in").customer_id.iloc[0]
    o, _ = api.draft_to_offer(ludhiana_draft(), r)
    v = rules_mod.validate_offer({**o, "customer_ids": o["customer_ids"] + [opted_out]}, bot.DATA, r, [])
    assert not v.ok and any("opted out" in x for x in v.reasons)


def test_reject_and_end():
    a = api.create(ludhiana_draft())["offer"]
    assert api.decide(a["offer_id"], "reject")["offer"]["status"] == "rejected"
    with pytest.raises(api.ApiError):
        api.decide(a["offer_id"], "approve")
    b = api.create({**ludhiana_draft(), "audience_mode": "everyone", "title": "Everyone deal"})["offer"]
    api.decide(b["offer_id"], "approve")
    assert api.decide(b["offer_id"], "end")["offer"]["status"] == "ended"


def test_one_customer_suggestion_is_pending_until_approved():
    cid = bot.DATA["customers"].query("orders_total == 0 and marketing_opt_in").customer_id.iloc[0]
    found = api.find_customer(cid)["customer"]
    assert found["customer_id"] == cid and found["orders_total"] == 0
    s = api.suggest_for(cid)
    assert s["offer"]["status"] == "pending" and s["offer"]["is_welcome"]
    assert api.decide(s["offer"]["offer_id"], "approve")["offer"]["status"] == "approved"


def test_rule_change_preview_then_confirm(world):
    p = api.change_rule({"rule": "category_cap", "category": "Makhana", "value": 18})
    assert not p["applied"] and "15% → 18%" in p["preview"] and load_rules(world)["category_caps_pct"]["Makhana"] == 15
    done = api.change_rule({"rule": "category_cap", "category": "Makhana", "value": 18, "confirm": True})
    assert done["applied"] and load_rules(world)["category_caps_pct"]["Makhana"] == 18
    with pytest.raises(api.ApiError):
        api.change_rule({"rule": "category_cap", "category": "Makhana", "value": 40, "confirm": True})


def test_http_needs_seller_code():
    httpd = api.serve(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/dashboard")
        assert e.value.code == 401
        req = urllib.request.Request(base + "/api/dashboard", headers={"X-Seller-Code": "secret"})
        assert json.loads(urllib.request.urlopen(req).read())["kpis"]["customers"] == 220
        page = urllib.request.urlopen(base + "/dashboard.html").read()   # static files need no code
        assert b"Offer Copilot" in page
    finally:
        httpd.shutdown()


def test_lowered_offer_text_shows_the_real_discount():
    d = ludhiana_draft(discount_pct=15, title="15% off nuts", message="Get 15% off almonds & cashews. It's Diwali!")
    res = api.check(d)
    assert "15%" not in res["preview"] and "12% off nuts" in res["preview"] and "It's Diwali" in res["preview"] and "&amp;" not in res["preview"]
    o = api.create(d)["offer"]
    assert o["discount_pct"] == 12 and o["title"] == "12% off nuts" and "Get 12% off" in o["message"] and o["code"].endswith("12")


# ---------- dashboard chat (fake Claude) ----------
def chat_as(monkeypatch, answer):
    from src import chat
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    def fake(text, data, segs, rules, history=None):
        seen.update(text=text, history=history)
        return answer
    monkeypatch.setattr(chat, "interpret", fake)
    return seen


def test_chat_group_offer_by_city_is_drafted_and_lowered(monkeypatch):
    chat_as(monkeypatch, {"tool": "draft_group_offer", "input": {
        "segments": ["lapsed"], "cities": ["Ludhiana"], "categories": [], "first_time_only": False, "with_cart": False,
        "product_ids": ["P004"], "discount_pct": 20, "valid_days": 7, "title": "20% off almonds", "message": "Almonds at 20% off", "rationale": "x"}})
    r = api.chat_turn({"text": "20% off almonds for lapsed customers in Ludhiana"})
    [o] = r["offers"]
    assert o["status"] == "pending" and o["target"] == "GROUP" and o["discount_pct"] == 12 and o["title"] == "12% off almonds"
    assert "lowered it to 12%" in r["text"]


def test_chat_rule_change_needs_confirm(monkeypatch, world):
    chat_as(monkeypatch, {"tool": "change_rule", "input": {"rule": "category_cap", "category": "Seeds", "new_value": 15}})
    r = api.chat_turn({"text": "lower seeds limit to 15"})
    assert r["confirm"] == {"kind": "rule", "rule": "category_cap", "category": "Seeds", "value": 15}
    assert load_rules(world)["category_caps_pct"]["Seeds"] == 20     # nothing changed yet


def test_chat_passes_history_and_plain_replies(monkeypatch):
    seen = chat_as(monkeypatch, {"reply": "Gift boxes are capped at 12%."})
    r = api.chat_turn({"text": "and gift boxes?", "history": [{"role": "seller", "text": "can I give 25% on nuts?"}]})
    assert r == {"text": "Gift boxes are capped at 12%."} and seen["history"][0]["text"] == "can I give 25% on nuts?"


def test_chat_without_key_explains(monkeypatch):
    with pytest.raises(api.ApiError) as e:
        api.chat_turn({"text": "hi"})
    assert e.value.status == 503
