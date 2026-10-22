"""Seller chat in plain language: Claude reads the message and either answers or picks ONE action.

The bot carries the action out with the same rule-checked code as the slash commands, so a chat message
can never publish anything by itself: drafts become Allow/Reject cards, rule changes and ending an offer need a Confirm tap.
"""
import json

import pandas as pd

from src import agent, store
from src.data_loader import TODAY
from src.rules import EDITABLE

SEGMENT_NAMES = {"champion": "top customers", "loyal": "loyal regulars", "new": "new customers", "at_risk": "going quiet (30-60 days)",
                 "lapsed": "lapsed (60+ days)", "price_sensitive": "deal hunters", "regular": "regular buyers"}

_S = {"type": "string"}
_I = {"type": "integer"}


def _tool(name: str, description: str, props: dict) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}


TOOLS = [
    _tool("suggest_for_customer", "Look up one customer by name or id and suggest their personal offer.",
          {"query": {**_S, "description": "customer name or id, e.g. Rohit or C0042"}}),
    _tool("draft_offer", "Draft a new offer the seller described. It is rule-checked and shown with Allow/Reject; it is NOT published.",
          {"title": _S, "message": {**_S, "description": "customer-facing, max 35 words"},
           "target": {**_S, "description": "ALL, a segment key, or one customer id"},
           "product_ids": {"type": "array", "items": _S}, "discount_pct": _I,
           "valid_days": {**_I, "description": "how many days from today it runs"},
           "rationale": {**_S, "description": "one sentence for the seller"}}),
    _tool("draft_group_offer", "Draft an offer for a specific group of customers, e.g. by city, favourite category, first-time buyers "
          "or open carts (combine with segments). Rule-checked and shown with Approve/Reject; NOT published.",
          {"segments": {"type": "array", "items": _S, "description": "segment keys, empty = any"},
           "cities": {"type": "array", "items": _S}, "categories": {"type": "array", "items": _S, "description": "favourite categories"},
           "first_time_only": {"type": "boolean"}, "with_cart": {"type": "boolean"},
           "product_ids": {"type": "array", "items": _S}, "discount_pct": _I, "valid_days": _I,
           "title": _S, "message": {**_S, "description": "customer-facing, max 35 words"}, "rationale": _S}),
    _tool("run_campaigns", "Analyse the business and propose a fresh set of campaigns.", {}),
    _tool("show_stats", "Show offer results: approved, pending, rejected, blocked.", {}),
    _tool("list_offers", "List offers waiting for approval or currently live.",
          {"which": {"type": "string", "enum": ["pending", "live"]}}),
    _tool("end_offer", "Take a live offer down (asks the seller to confirm).", {"offer_id": _S}),
    _tool("change_rule", "Change one business limit in rules.yaml (asks the seller to confirm).",
          {"rule": {"type": "string", "enum": sorted([*EDITABLE, "category_cap"])},
           "category": {**_S, "description": "category name for category_cap, otherwise empty"},
           "new_value": _I}),
]

SYSTEM = """You are the assistant inside Offer Copilot's seller bot. The person writing to you runs marketing for the
dry-fruit store Nishtha's Pantry and is not technical. Reply in short, plain English (use ₹), at most 80 words.
Use a tool when they ask you to DO something; answer in text when they ask a question (including "can I...?" questions
about the limits, which you answer from the rules below). Never claim an offer is live: drafts need the seller's Allow tap.
When drafting, use exactly the discount the seller asked for, even if it is above a limit: the rules engine
lowers it and shows the seller why. Plain text only, no markdown tables.
Only use product_ids, segment keys, offer ids and rule names that appear in the context."""


def context(data: dict, segs: pd.DataFrame, rules: dict) -> str:
    """Compact snapshot of the business for the assistant."""
    p = data["products"][["product_id", "product_name", "category", "price", "margin_pct", "stock_units"]]
    offers = [{k: o[k] for k in ("offer_id", "title", "target", "discount_pct", "status", "valid_to")}
              for o in store.get_offers() if o["status"] == "pending" or (o["status"] == "approved" and o["valid_to"] >= TODAY.isoformat())]
    counts = segs.segment.value_counts().to_dict()
    cities = sorted(data["customers"].city.unique().tolist())
    return json.dumps({
        "today": TODAY.isoformat(), "festivals": {"Navratri": "ended 19 Oct", "Diwali": "8 Nov 2026"},
        "segments": {k: {"meaning": SEGMENT_NAMES[k], "customers": int(v)} for k, v in counts.items()},
        "cities": cities, "rules": rules, "catalogue": p.to_dict("records"), "pending_and_live_offers": offers,
    }, default=str, ensure_ascii=False)


def interpret(text: str, data: dict, segs: pd.DataFrame, rules: dict, history: list[dict] | None = None) -> dict | None:
    """{'reply': str} or {'tool': name, 'input': {...}}; None if the AI is unavailable.
    history: earlier turns [{'role': 'seller'|'assistant', 'text': str}] so "make it 10% instead" makes sense."""
    if not agent.available():
        return None
    if history:
        lines = "\n".join(f"{'Seller' if h.get('role') == 'seller' else 'You'}: {str(h.get('text', ''))[:400]}" for h in history[-6:])
        text = f"Earlier in this conversation:\n{lines}\n\nThe seller now says: {text}"
    return agent.assistant_turn(SYSTEM + "\n\nCONTEXT:\n" + context(data, segs, rules), text, TOOLS)
