"""Message formatting (Telegram HTML)."""
from html import escape

import pandas as pd


def _names(data, pids):
    p = data["products"].set_index("product_id")
    return ", ".join(p.loc[x, "product_name"] for x in pids if x in p.index)


def _margin_after(data, offer):
    p = data["products"].set_index("product_id")
    m = []
    for x in offer["product_ids"]:
        if x in p.index:
            price = p.loc[x, "price"] * (1 - offer["discount_pct"] / 100)
            m.append((price - p.loc[x, "cost_price"]) / price * 100)
    return f"{min(m):.0f}%" if m else "n/a"


def seller_card(offer: dict, data: dict) -> str:
    tag = {"rule_engine": "Rule engine", "ai_agent": "AI agent", "seller_chat": "You"}.get(offer["source"], offer["source"])
    return (f"💡 <b>{escape(offer['title'])}</b>  <i>({tag})</i>\n\n"
            f"🎯 <b>Who:</b> {escape(offer['target'])}\n"
            f"🛍 <b>What:</b> {escape(_names(data, offer['product_ids']))}\n"
            f"🏷 <b>{offer['discount_pct']}% off</b> · code <code>{escape(offer['code'])}</code>\n"
            f"📅 {offer['valid_from']} → {offer['valid_to']}\n"
            f"📉 Lowest margin after discount: <b>{_margin_after(data, offer)}</b> ✅ within rules\n\n"
            f"🧠 <b>Why:</b> {escape(offer['rationale'])}\n"
            f"📈 <b>Impact:</b> {escape(offer['expected_impact'])}")


def customer_message(offer: dict, data: dict) -> str:
    return (f"🎁 <b>{escape(offer['title'])}</b>\n\n{escape(offer['message'])}\n\n"
            f"🏷 <b>{offer['discount_pct']}% off</b> on {escape(_names(data, offer['product_ids']))}\n"
            f"🔖 Code: <code>{escape(offer['code'])}</code>\n⏳ Valid until {offer['valid_to']}")


def profile_card(cid: str, data: dict) -> str:
    c = data["customers"].set_index("customer_id").loc[cid]
    o = data["orders"][data["orders"].customer_id == cid].sort_values("order_date", ascending=False).head(3)
    lines = [f"• {r.order_date:%d %b %Y} — ₹{int(r.order_value):,}" + (" · returned" if r.returned else "") + (f" · {r.coupon_used}" if r.coupon_used else "")
             for r in o.itertuples()] or ["• no orders yet"]
    cart = "empty" if c.cart_items == 0 else f"{int(c.cart_items)} item(s) waiting"
    return (f"🧑 <b>{escape(c['name'])}</b> <i>(synthetic shopper)</i>\n📍 {c.city} · {c.city_tier}\n🏅 {c.tier} member since {c.join_date:%d %b %Y}\n\n"
            f"📦 <b>Orders:</b> {int(c.orders_total)} · ₹{int(c.lifetime_value):,} spent\n<b>Recent orders</b>\n" + "\n".join(lines) +
            f"\n\n🛒 <b>Cart:</b> {cart}\n⭐ <b>Points:</b> {int(c.reward_points)}" + (f" · ⚠️ {int(c.points_expiring)} expire soon" if c.points_expiring else ""))
