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
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message, effective_message=message)


def ctx(*args):
    return SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock()))


def texts(update) -> str:
    return "\n".join(c.args[0] for c in update.message.reply_text.call_args_list)


def run(coro):
    return asyncio.run(coro)


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
    run(bot.start(u, ctx()))
    cid = store.get_shopper(111)
    assert cid and "synthetic customer" in texts(u)
    run(bot.profile(u, ctx()))
    run(bot.offer(u, ctx()))
    for o in store.get_offers():  # anything the shopper got passed the rules
        assert o["status"] == "approved"
        assert validate_offer(o, bot.DATA, load_rules(), []).ok


def test_seller_needs_code_and_is_exclusive():
    u = msg_update(222)
    run(bot.campaigns(u, ctx()))
    assert "Seller only" in texts(u)
    run(bot.seller(u, ctx("wrong")))
    assert store.get_seller() is None
    run(bot.seller(u, ctx("secret")))
    assert store.get_seller() == 222
    other = msg_update(333)
    run(bot.seller(other, ctx("secret")))
    assert "already claimed" in texts(other)


def press(chat_id: int, data: str, html: str = "card"):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                        edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(text_html=html, reply_text=AsyncMock()))
    return SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=chat_id))


def test_campaigns_allow_publishes_and_reaches_shoppers():
    shopper = msg_update(111)
    run(bot.start(shopper, ctx()))
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
