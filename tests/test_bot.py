"""Bot flow without Telegram: fake updates drive the real handlers against a temp store."""
import asyncio
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import bot, store
from src.rules import load_rules, validate_offer


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(store, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(store, "PUBLIC", tmp_path / "offers.json")
    monkeypatch.setenv("SELLER_CODE", "secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def msg_update(chat_id: int):
    message = SimpleNamespace(reply_text=AsyncMock(), delete=AsyncMock())
    chat = SimpleNamespace(id=chat_id, send_message=message.reply_text, send_action=AsyncMock())
    return SimpleNamespace(effective_chat=chat, message=message, effective_message=message)


def ctx(*args):
    return SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock()))


def texts(update) -> str:
    return "\n".join(c.args[0] for c in update.message.reply_text.call_args_list)


def run(coro):
    return asyncio.run(coro)


def join(update, kind: str = "returning"):
    """/start, then tap "I'm new here" or "I've shopped before"."""
    run(bot.start(update, ctx()))
    q = SimpleNamespace(data=f"who:{kind}", answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(reply_text=update.message.reply_text))
    run(bot.button(SimpleNamespace(callback_query=q, effective_chat=update.effective_chat), ctx()))


def test_pick_shopper_is_opted_in_and_unique():
    taken = set()
    for _ in range(20):
        cid = bot.pick_shopper(taken, random.Random(len(taken)))
        assert cid not in taken
        taken.add(cid)
    opted = bot.DATA["customers"].set_index("customer_id").marketing_opt_in
    assert all(opted[c] for c in taken)


def test_find_customers_by_id_and_name():
    assert list(bot.find_customers("c0001").customer_id) == ["C0001"]
    name = bot.DATA["customers"].name.iloc[0].split()[0].lower()
    assert len(bot.find_customers(name)) >= 1


def test_customer_flow_start_profile_offer():
    u = msg_update(111)
    join(u)
    cid = store.get_shopper(111)
    assert cid and "Welcome back" in texts(u)
    run(bot.profile(u, ctx()))
    run(bot.offer(u, ctx()))
    for o in store.get_offers():  # anything the shopper got passed the rules
        assert o["status"] == "approved"
        assert validate_offer(o, bot.DATA, load_rules(), []).ok


def test_seller_needs_code():
    u = msg_update(222)
    run(bot.campaigns(u, ctx()))
    assert "Seller only" in texts(u)
    run(bot.seller(u, ctx()))
    assert "private" in texts(u) and not store.get_sellers()
    run(bot.seller(u, ctx("wrong")))
    assert not store.get_sellers() and u.message.delete.await_count == 1     # the typed code is deleted from the chat
    run(bot.seller(u, ctx("secret")))
    assert store.get_sellers() == {222}
    teammate = msg_update(333)                                               # someone the owner gave the code to
    run(bot.seller(teammate, ctx("secret")))
    assert store.get_sellers() == {222, 333}
    stranger = msg_update(444)
    run(bot.campaigns(stranger, ctx()))
    assert "Seller only" in texts(stranger) and not store.get_offers()
    run(bot.logout(teammate, ctx()))
    assert store.get_sellers() == {222}


def test_wrong_codes_lock_the_chat():
    u = msg_update(555)
    for _ in range(bot.MAX_TRIES):
        run(bot.seller(u, ctx("guess")))
    run(bot.seller(u, ctx("secret")))                                        # even the right code waits out the lock
    assert "Too many wrong codes" in texts(u) and not store.is_seller_chat(555)
    bot.FAILED_TRIES.clear()


def press(chat_id: int, data: str, html: str = "card"):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                        edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(text_html=html, reply_text=AsyncMock()))
    return SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=chat_id))


def test_campaigns_allow_publishes_and_reaches_shoppers():
    shopper = msg_update(111)
    join(shopper)
    seller = msg_update(222)
    run(bot.seller(seller, ctx("secret")))
    run(bot.campaigns(seller, ctx()))
    pend = store.get_offers("pending")
    assert pend, "campaigns should create pending cards"
    everyone = next(o for o in pend if o["target"] == "ALL")

    # a classmate cannot approve
    up = press(111, f"allow:{everyone['offer_id']}")
    run(bot.button(up, ctx()))
    assert store.get_offer(everyone["offer_id"])["status"] == "pending"

    c = ctx()
    run(bot.button(press(222, f"allow:{everyone['offer_id']}"), c))
    assert store.get_offer(everyone["offer_id"])["status"] == "approved"
    public = json.loads(store.PUBLIC.read_text())
    assert everyone["offer_id"] in {o["offer_id"] for o in public}
    assert c.bot.send_message.await_count == 1  # pushed to the shopper chat

    run(bot.offers(shopper, ctx()))
    assert everyone["title"] in texts(shopper)


def test_allow_revalidates_against_current_rules(monkeypatch):
    seller = msg_update(222)
    run(bot.seller(seller, ctx("secret")))
    run(bot.campaigns(seller, ctx()))
    o = store.get_offers("pending")[0]
    strict = load_rules()
    strict["global"]["max_discount_pct"] = 1  # marketing tightened the rules after the card was sent
    monkeypatch.setattr(bot, "rules", lambda: strict)
    run(bot.button(press(222, f"allow:{o['offer_id']}"), ctx()))
    after = store.get_offer(o["offer_id"])
    assert after["status"] == "blocked" and after["blocked_reasons"]
    assert not json.loads(store.PUBLIC.read_text())


def test_reject():
    seller = msg_update(222)
    run(bot.seller(seller, ctx("secret")))
    run(bot.campaigns(seller, ctx()))
    o = store.get_offers("pending")[0]
    run(bot.button(press(222, f"reject:{o['offer_id']}"), ctx()))
    assert store.get_offer(o["offer_id"])["status"] == "rejected"


def test_build_app_registers_handlers():
    app = bot.build_app("123456:TEST-token-not-real")
    assert sum(len(h) for h in app.handlers.values()) >= 14


def test_export_includes_product_names_and_prices():
    seller = msg_update(222)
    run(bot.seller(seller, ctx("secret")))
    run(bot.campaigns(seller, ctx()))
    o = next(x for x in store.get_offers("pending") if x["target"] == "ALL")
    run(bot.button(press(222, f"allow:{o['offer_id']}"), ctx()))
    [pub] = json.loads(store.PUBLIC.read_text())
    assert len(pub["products"]) == len(o["product_ids"])
    assert all(p["name"] and p["price"] > 0 for p in pub["products"])


def test_no_offer_message_is_customer_friendly():
    prof = {"cart_items": 1, "points_expiring": 11, "reward_points": 114}
    text = bot.no_offer_message(prof)
    assert "1 item still in your cart" in text and "11 of your 114 points" in text
    for internal in ("margin", "segment", "loyal", "discount is not needed"):
        assert internal not in text.lower()
    assert bot.no_offer_message({"cart_items": 0, "points_expiring": 0, "reward_points": 0}).startswith("No personal discount today.")


def test_seller_chat_is_not_a_shopper():
    u = msg_update(222)
    join(u)                      # starts as a shopper...
    assert store.get_shopper(222)
    run(bot.seller(u, ctx("secret")))             # ...then becomes the seller
    assert store.get_shopper(222) is None
    run(bot.start(u, ctx()))
    assert "This chat is the seller" in texts(u)
    run(bot.campaigns(u, ctx()))
    o = next(x for x in store.get_offers("pending") if x["target"] == "ALL")
    c = ctx()
    run(bot.button(press(222, f"allow:{o['offer_id']}"), c))
    assert c.bot.send_message.await_count == 0    # no customer offer pushed into the seller chat


def test_campaigns_twice_makes_no_duplicates():
    seller = msg_update(222)
    run(bot.seller(seller, ctx("secret")))
    run(bot.campaigns(seller, ctx()))
    first = len(store.get_offers())
    run(bot.campaigns(seller, ctx()))
    assert len(store.get_offers()) == first
    assert "already pending or live" in texts(seller)


def test_adjusted_offer_code_matches_final_discount():
    from src import recommender
    bad = recommender._offer("public", "MEGA MAKHANA BLAST", "Half price!", "ALL", ["P001", "P002"], 60, 5, "x", "y", source="ai_agent")
    res = recommender.run_pipeline(bot.DATA, load_rules(), [bad])
    o = store.get_offer(res["adjusted"][0])
    assert o["discount_pct"] == 15 and o["code"] == "MEGA15"


def test_two_bots_seller_can_also_shop_and_offers_go_via_customer_bot(monkeypatch):
    customer_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(bot, "TWO_BOTS", True)
    monkeypatch.setattr(bot, "CUSTOMER_BOT", customer_bot)
    me = 222  # same Telegram user in both bots (private chat id == user id)
    join(msg_update(me))         # customer bot
    seller = msg_update(me)
    run(bot.seller(seller, ctx("secret")))        # seller bot
    assert store.get_shopper(me)                  # still a shopper in the customer bot
    run(bot.campaigns(seller, ctx()))
    o = next(x for x in store.get_offers("pending") if x["target"] == "ALL")
    seller_ctx = ctx()
    run(bot.button(press(me, f"allow:{o['offer_id']}"), seller_ctx))
    assert customer_bot.send_message.await_count == 1   # delivered by the customer bot...
    assert seller_ctx.bot.send_message.await_count == 0  # ...not by the seller bot


def test_two_bot_apps_split_commands():
    def commands(app):
        return {c for h in app.handlers[0] if hasattr(h, "commands") for c in h.commands}
    cust = commands(bot.build_customer_app("123456:TEST-customer"))
    sell = commands(bot.build_seller_app("654321:TEST-seller"))
    assert {"start", "offer", "offers", "profile"} <= cust and "campaigns" not in cust and "seller" not in cust
    assert {"seller", "campaigns", "pending", "customer"} <= sell and "offer" not in sell



# ---------- first-time customers ----------
def welcome_offer(cid):
    from src import recommender
    prof = recommender.customer_profile(bot.DATA, bot.SEGS, cid)
    return recommender._welcome_candidate(bot.DATA, load_rules(), prof, [])


def first_timer():
    c = bot.DATA["customers"]
    return c[(c.orders_total == 0) & c.marketing_opt_in].customer_id.iloc[0]


def test_new_here_gets_first_time_customer_and_welcome_offer():
    u = msg_update(444)
    join(u, "new")
    cid = store.get_shopper(444)
    assert int(bot.DATA["customers"].set_index("customer_id").loc[cid, "orders_total"]) == 0
    assert "first time" in texts(u)
    run(bot.offer(u, ctx()))
    [o] = store.get_offers("approved")
    assert o["is_welcome"] and o["min_order_value"] == 499 and o["code"].startswith("WELCOME")
    assert "On orders of ₹499 or more" in texts(u)


def test_returning_gets_customer_with_history():
    u = msg_update(555)
    join(u, "returning")
    cid = store.get_shopper(555)
    assert int(bot.DATA["customers"].set_index("customer_id").loc[cid, "orders_total"]) > 0


def test_welcome_rules():
    r = load_rules()
    cid = first_timer()
    w = welcome_offer(cid)
    assert validate_offer(w, bot.DATA, r, []).ok
    # too generous -> lowered to the welcome cap
    v = validate_offer({**w, "discount_pct": 25}, bot.DATA, r, [])
    assert not v.ok and v.adjusted_offer["discount_pct"] == r["first_order"]["welcome_discount_pct"]
    # no minimum basket -> blocked
    assert not validate_offer({**w, "min_order_value": 0}, bot.DATA, r, []).ok
    # second welcome offer -> blocked
    given = {**w, "status": "approved", "created_at": "2026-09-01T10:00:00"}
    v = validate_offer(w, bot.DATA, r, [given])
    assert not v.ok and any("already received a welcome" in x for x in v.reasons)
    # customer who has ordered before -> blocked
    regular = bot.DATA["customers"].query("orders_total > 0").customer_id.iloc[0]
    v = validate_offer({**w, "target": regular}, bot.DATA, r, [])
    assert not v.ok and any("only for first orders" in x for x in v.reasons)


def test_main_block_is_last():
    """`python -m src.bot` runs main() at the __main__ line, so every handler must be defined above it."""
    lines = [l for l in open(bot.__file__, encoding="utf-8").read().splitlines() if l.strip()]
    assert lines[-2:] == ['if __name__ == "__main__":', "    main()"]


def test_menu_buttons_open_the_right_feature():
    u = msg_update(666)
    join(u, "returning")
    for label, expect in [(bot.MENU_PROFILE, "Orders"), (bot.MENU_DEALS, "No live deals"), (bot.MENU_HELP, "Shopper commands")]:
        u.message.reply_text.reset_mock()
        u.message.text = label
        run(bot.other(u, ctx()))
        assert expect in texts(u), label
    u.message.reply_text.reset_mock()
    u.message.text = "hello?"
    run(bot.other(u, ctx()))
    assert u.message.reply_text.call_args.kwargs["reply_markup"] is bot.MENU


def test_switch_is_seller_only_and_plays_the_chosen_customer():
    classmate = msg_update(777)
    run(bot.switch(classmate, ctx("Amit", "Arora")))
    assert "only for the shop owner" in texts(classmate) and store.get_shopper(777) is None
    me = msg_update(888)
    store.set_seller(888)
    run(bot.switch(me, ctx("Amit", "Arora")))
    assert store.get_shopper(888) == "C0084" and "Amit Arora" in texts(me)
    run(bot.switch(me, ctx("Amit")))                      # several Amits -> a list to pick from
    assert "/switch C" in texts(me)
