"""Seller chat in plain language, with a fake Claude: every action still goes through the rules and a button."""
import asyncio
import functools
import json
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import bot, chat, rules as rules_mod, store
from src.rules import load_rules


@pytest.fixture(autouse=True)
def tmp_world(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(store, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(store, "PUBLIC", tmp_path / "offers.json")
    rules_file = tmp_path / "rules.yaml"
    shutil.copy(rules_mod.ROOT / "rules.yaml", rules_file)
    monkeypatch.setattr(bot, "rules", lambda: load_rules(rules_file))
    monkeypatch.setattr(bot, "apply_change", functools.partial(rules_mod.apply_change, path=rules_file))
    monkeypatch.setenv("SELLER_CODE", "secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store.set_seller(222)
    return rules_file


def say(chat_id: int, text: str, ai_answer: dict | None, monkeypatch):
    monkeypatch.setattr(chat, "interpret", lambda *a, **k: ai_answer)
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    u = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, send_action=AsyncMock()), message=message, effective_message=message)
    asyncio.run(bot.seller_text(u, SimpleNamespace(args=[], bot=SimpleNamespace(send_message=AsyncMock()))))
    return u


def said(u) -> str:
    return "\n".join(c.args[0] for c in u.message.reply_text.call_args_list)


def buttons(u) -> list[str]:
    kb = u.message.reply_text.call_args_list[-1].kwargs.get("reply_markup")
    return [b.callback_data for row in kb.inline_keyboard for b in row] if kb else []


def tap(chat_id: int, data: str):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(text_html="card", reply_text=AsyncMock()))
    asyncio.run(bot.button(SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=chat_id)), SimpleNamespace(bot=None)))
    return q


def test_question_gets_plain_answer_with_bold_converted(monkeypatch):
    u = say(222, "Can I give 25% on gift boxes?", {"reply": "No: **Gift Box is capped at 12%**."}, monkeypatch)
    assert "<b>Gift Box is capped at 12%</b>" in said(u)


def test_draft_above_limit_is_lowered_and_needs_allow(monkeypatch):
    ask = {"tool": "draft_offer", "input": {"title": "Diwali Nuts Deal", "message": "Nuts for Diwali", "target": "ALL",
                                            "product_ids": ["P004", "P005"], "discount_pct": 15, "valid_days": 17, "rationale": "Diwali"}}
    u = say(222, "15% off almonds and cashews for everyone", ask, monkeypatch)
    [o] = store.get_offers()
    assert o["status"] == "pending" and o["discount_pct"] == 12 and o["source"] == "seller_chat"  # Nuts cap
    assert "lowered it" in said(u) and f"allow:{o['offer_id']}" in buttons(u)
    assert not store.PUBLIC.exists() or json.loads(store.PUBLIC.read_text()) == []  # nothing published yet


def test_rule_change_only_after_confirm(monkeypatch, tmp_world):
    u = say(222, "raise the makhana limit to 18%", {"tool": "change_rule", "input": {"rule": "category_cap", "category": "Makhana", "new_value": 18}}, monkeypatch)
    assert "15% → 18%" in said(u)
    assert load_rules(tmp_world)["category_caps_pct"]["Makhana"] == 15      # not yet
    token = buttons(u)[0].split(":")[1]
    tap(333, f"ruleok:{token}")                                             # a stranger can't confirm
    assert load_rules(tmp_world)["category_caps_pct"]["Makhana"] == 15
    tap(222, f"ruleok:{token}")
    assert load_rules(tmp_world)["category_caps_pct"]["Makhana"] == 18
    assert "# no offer may exceed this" in tmp_world.read_text()            # comments kept


def test_unsafe_rule_change_refused(monkeypatch, tmp_world):
    u = say(222, "makhana limit 35%", {"tool": "change_rule", "input": {"rule": "category_cap", "category": "Makhana", "new_value": 35}}, monkeypatch)
    assert "Can't change that" in said(u) and buttons(u) == []
    assert load_rules(tmp_world)["category_caps_pct"]["Makhana"] == 15


def test_end_offer_after_confirm(monkeypatch):
    from src import recommender
    res = recommender.run_pipeline(bot.DATA, load_rules(), recommender.generate_candidates(bot.DATA, bot.SEGS, load_rules()))
    oid = res["stored"][0]
    store.set_status(oid, "approved")
    u = say(222, "stop that deal", {"tool": "end_offer", "input": {"offer_id": oid}}, monkeypatch)
    assert store.get_offer(oid)["status"] == "approved"
    tap(222, buttons(u)[0])
    assert store.get_offer(oid)["status"] == "ended"
    assert oid not in {o["offer_id"] for o in json.loads(store.PUBLIC.read_text())}


def test_strangers_cannot_use_seller_chat(monkeypatch):
    monkeypatch.setattr(bot, "TWO_BOTS", True)
    called = []
    monkeypatch.setattr(chat, "interpret", lambda *a, **k: called.append(1))
    message = SimpleNamespace(text="give everyone 50% off", reply_text=AsyncMock())
    u = SimpleNamespace(effective_chat=SimpleNamespace(id=999, send_action=AsyncMock()), message=message)
    asyncio.run(bot.seller_text(u, SimpleNamespace(args=[])))
    assert not called and "unlock" in said(u)


def test_no_ai_key_falls_back_to_help(monkeypatch):
    u = say(222, "hello", None, monkeypatch)
    assert "/help" in said(u)
