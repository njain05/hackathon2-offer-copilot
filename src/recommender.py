"""Rule-based recommender + AI-assisted flow. Every offer is validated before it is stored or shown."""
import re
from datetime import timedelta

import pandas as pd

from src import agent, store
from src.data_loader import TODAY
from src.rules import validate_offer


FILLER = {"OFF", "THE", "FOR", "AND", "YOUR", "OUR", "ON", "A", "AN", "WE", "TO", "OF", "GET", "NEW"}


def _code_word(title: str) -> str:
    """First real word of the title for a coupon code: '15% off nuts' -> NUTS, 'Diwali Gifting Sale' -> DIWALI."""
    words = [w.upper() for w in re.findall(r"[A-Za-z]+", title)]
    return next((w for w in words if w not in FILLER), words[0] if words else "OFFER")[:8]


def _offer(kind, title, message, target, pids, disc, valid_days, rationale, impact, source="rule_engine", code=None):
    return {
        "kind": kind, "title": title, "message": message, "target": target,
        "product_ids": list(pids), "category": None, "discount_pct": int(disc),
        "code": code or (_code_word(title) + str(int(disc))),
        "valid_from": TODAY.isoformat(), "valid_to": (TODAY + timedelta(days=valid_days)).isoformat(),
        "rationale": rationale, "expected_impact": impact, "source": source,
    }


def _stock_ok(data, rules):
    p = data["products"]
    return p[p.stock_units >= rules["global"]["min_stock_for_offer"]]


def generate_candidates(data: dict, segs: pd.DataFrame, rules: dict) -> list[dict]:
    p = _stock_ok(data, rules)
    counts = segs.segment.value_counts().to_dict()
    out = []
    gift = p[p.category.isin(["Gift Box", "Nuts", "Dried Fruit"])].sort_values("margin_pct", ascending=False).head(4)
    out.append(_offer("public", "Diwali Gifting Sale", "Gift something sweet this Diwali: premium dry fruits and gift boxes, now with a festive saving.",
                      "ALL", gift.product_id, 10, 17, "Diwali is ~17 days away and over 60% of dry-fruit consumption happens in festive season; gifting items have the best margins to fund a discount.",
                      "Lift gifting sales while staying above the margin floor"))
    fast = p[p.category == "Fasting"].sort_values("stock_units", ascending=False).head(3)
    out.append(_offer("public", "Navratri Leftover Deal", "Fasting essentials still in your kitchen plans? Stock up for Ekadashi and upcoming vrats.",
                      "ALL", fast.product_id, 10, 7, "Navratri just ended and fasting items are our deepest stock; clear it before demand dips.",
                      "Convert excess fasting stock into cash"))
    n = counts.get("at_risk", 0)
    if n:
        out.append(_offer("public", "We Miss You Winback", "It's been a while! Here's a little thank-you on our best sellers.",
                          "at_risk", p[p.category.isin(["Makhana", "Dried Fruit"])].sort_values("stock_units", ascending=False).head(3).product_id,
                          10, 10, f"{n} customers went quiet 30-60 days after buying regularly: still recoverable with a modest offer.", f"Win back part of {n} at-risk customers"))
    n = counts.get("lapsed", 0)
    if n:
        out.append(_offer("public", "Come Back Special", "We saved something for you: a bigger saving to welcome you back.",
                          "lapsed", p[p.category.isin(["Seeds", "Snacks", "Mixes"])].head(3).product_id,
                          15, 10, f"{n} customers have not ordered for over 60 days; a larger offer on high-margin items is justified.", f"Reactivate some of {n} lapsed customers"))
    return out


def _same_offer_active(c: dict) -> bool:
    """An offer with this title and audience is already waiting for the seller or live."""
    for o in store.get_offers():
        if o["title"] == c["title"] and o["target"] == c["target"]:
            if o["status"] == "pending" or (o["status"] == "approved" and o["valid_to"] >= TODAY.isoformat()):
                return True
    return False


def _recode(code: str, old: int, new: int) -> str:
    """DIWALI60 -> DIWALI15 when the rules change the discount."""
    return code[: -len(str(old))] + str(new) if code.endswith(str(old)) else code


def with_discount(offer: dict, new: int) -> dict:
    """Offer at a new discount, with "15%" in its title and message changed to match (customers must see the real number)."""
    old = int(offer["discount_pct"])
    fix = lambda t: re.sub(rf"(?<!\d){old}\s?%", f"{new}%", t or "")
    return {**offer, "discount_pct": int(new), "title": fix(offer.get("title")), "message": fix(offer.get("message")),
            "code": _recode(offer.get("code", ""), old, int(new))}


def run_pipeline(data: dict, rules: dict, candidates: list[dict], history_fn=store.history_for) -> dict:
    res = {"stored": [], "adjusted": [], "blocked": [], "skipped": []}
    for c in candidates:
        if _same_offer_active(c):
            res["skipped"].append(c["title"])
            continue
        hist = history_fn(c["target"]) if c["target"].startswith("C0") else store.group_history() if c["target"] == "GROUP" else []
        v = validate_offer(c, data, rules, hist)
        if v.ok:
            res["stored"].append(store.add_offer(c))
        elif v.adjusted_offer:
            a = {**with_discount(c, v.adjusted_offer["discount_pct"]), "rationale": c["rationale"] + f" (Rules lowered the discount from {c['discount_pct']}% to {v.adjusted_offer['discount_pct']}%.)"}
            store.log_event("adjusted", {"source": c["source"], "title": c["title"], "proposed": c["discount_pct"], "final": a["discount_pct"], "reasons": v.reasons})
            res["adjusted"].append(store.add_offer(a))
        else:
            store.log_event("blocked", {"source": c["source"], "title": c["title"], "proposed": c["discount_pct"], "reasons": v.reasons})
            res["blocked"].append(store.add_offer({**c, "status": "blocked", "blocked_reasons": v.reasons}))
    return res


def ai_campaigns(data: dict, segs: pd.DataFrame, rules: dict) -> list[dict] | None:
    p = _stock_ok(data, rules)
    ctx = {"today": TODAY.isoformat(), "festivals": {"Navratri": "ended 19 Oct", "Diwali": "8 Nov 2026"},
           "segment_counts": segs.segment.value_counts().to_dict(),
           "catalogue": p[["product_id", "product_name", "category", "price", "margin_pct", "stock_units", "festival_tag"]].to_dict("records")}
    raw = agent.propose_campaigns(ctx, rules)
    if not raw:
        return None
    out = []
    for r in raw:
        try:
            out.append(_offer("public", r["title"], r["message"], r.get("target", "ALL"), r["product_ids"], r["discount_pct"],
                              int(r.get("valid_days", 7)), r["rationale"], r.get("expected_impact", ""), source="ai_agent"))
        except Exception:
            continue
    return out


# ---------- per-customer (customer mode in the bot, seller chat) ----------
def customer_profile(data: dict, segs: pd.DataFrame, cid: str) -> dict:
    c = data["customers"].set_index("customer_id").loc[cid]
    s = segs.set_index("customer_id").loc[cid]
    return {"customer_id": cid, "segment": s.segment, "segment_reason": s.reason, "orders_total": int(c.orders_total),
            "lifetime_value": int(c.lifetime_value), "days_since_last_order": None if pd.isna(c.days_since_last_order) else int(c.days_since_last_order),
            "discount_order_share": float(c.discount_order_share), "return_rate": float(c.return_rate), "tier": c.tier,
            "reward_points": int(c.reward_points), "points_expiring": int(c.points_expiring), "preferred_category": c.preferred_category,
            "cart_items": int(c.cart_items), "marketing_opt_in": bool(c.marketing_opt_in)}


def bestsellers(data: dict, rules: dict, n: int = 3) -> list[str]:
    """Most-ordered products (by units) that have enough stock to promote."""
    ok = set(_stock_ok(data, rules).product_id)
    units = data["order_items"].groupby("product_id").quantity.sum().sort_values(ascending=False)
    return [pid for pid in units.index if pid in ok][:n]


def _welcome_candidate(data, rules, prof, cart) -> dict:
    """First order: small capped welcome discount on their cart (or our bestsellers), minimum basket from rules.yaml."""
    fo = rules["first_order"]
    ok = set(_stock_ok(data, rules).product_id)
    pids = [x for x in cart if x in ok][:3] or bestsellers(data, rules)
    disc, mov = int(fo["welcome_discount_pct"]), int(fo["min_order_value"])
    o = _offer("personal", "Welcome to Nishtha's Pantry",
               f"Your first order deserves a treat: {disc}% off on orders of ₹{mov} or more.", prof["customer_id"], pids, disc, 14,
               "First-time customer: the first order is the hardest to win, so a small one-time welcome offer with a minimum basket.",
               "Turn a new sign-up into a first order without giving away margin on tiny baskets",
               code=f"WELCOME{disc}-{prof['customer_id']}")
    return {**o, "is_welcome": True, "min_order_value": mov}


def _base_candidate(data, rules, prof) -> dict | None:
    """Rule-based starting offer for a customer; None = no discount needed."""
    p = _stock_ok(data, rules)
    seg = prof["segment"]
    cart = [x for x in data["customers"].set_index("customer_id").loc[prof["customer_id"]].cart_product_ids.split(",") if x]
    if prof["orders_total"] == 0 and rules.get("first_order"):
        return _welcome_candidate(data, rules, prof, cart)
    if prof["return_rate"] >= 0.3:
        return None
    if cart:
        pids, disc, title = cart[:3], 8, "Your cart is waiting"
        why = "Has items in cart; a small nudge is enough to close the sale."
    elif seg in ("champion", "loyal"):
        return None
    elif seg == "at_risk":
        pids = p[p.category == prof["preferred_category"]].head(2).product_id.tolist(); disc, title = 10, "We saved this for you"
        why = f"At risk of leaving ({prof['segment_reason']}); offer on their favourite category."
    elif seg == "lapsed":
        pids = p[p.category == prof["preferred_category"]].head(2).product_id.tolist(); disc, title = 15, "Come back for more"
        why = f"Lapsed ({prof['segment_reason']}); a stronger offer is justified."
    elif seg == "price_sensitive":
        pids = p[p.category == prof["preferred_category"]].head(2).product_id.tolist(); disc, title = 5, "Little something extra"
        why = "Buys mostly on discount; keep it small to protect margin."
    elif seg == "new":
        pids = p[p.category == prof["preferred_category"]].head(2).product_id.tolist(); disc, title = 10, "Welcome offer"
        why = "New customer; a welcome offer encourages a second order."
    else:
        pids = p[p.category == prof["preferred_category"]].head(1).product_id.tolist(); disc, title = 5, "Just for you"
        why = "Regular buyer; a light nudge on their favourite category."
    if not pids:
        return None
    return _offer("personal", title, f"{disc}% off on your picks.", prof["customer_id"], pids, disc, 7, why,
                  "Expected to convert a warm customer without over-discounting", code=f"{prof['customer_id']}-{disc}")


def offer_for_customer(data: dict, segs: pd.DataFrame, rules: dict, cid: str, use_ai: bool = True) -> dict:
    """Return {profile, offer|None, explanation, validation_notes, used_ai}. Never returns an unvalidated offer."""
    prof = customer_profile(data, segs, cid)
    base = _base_candidate(data, rules, prof)
    if base is None:
        why = ("High return rate: discounts here would likely be returned." if prof["return_rate"] >= 0.3
               else "This customer buys without needing a discount; keep margin.")
        return {"profile": prof, "offer": None, "explanation": why, "notes": [], "used_ai": False}
    used_ai = False
    cand = dict(base)
    if use_ai and agent.available():
        names = data["products"].set_index("product_id").product_name
        ai = agent.personalise(prof, {**base, "product_names": [names.get(x, x) for x in base["product_ids"]]}, rules)
        if ai and "discount_pct" in ai:
            used_ai = True
            cand.update(discount_pct=int(ai["discount_pct"]), title=ai.get("title", cand["title"]), message=ai.get("message", cand["message"]),
                        rationale=ai.get("rationale", cand["rationale"]), source="ai_agent")
            if cand["discount_pct"] <= 0:
                return {"profile": prof, "offer": None, "explanation": cand["rationale"], "notes": [], "used_ai": True}
            cand["code"] = (f"WELCOME{cand['discount_pct']}-{cid}" if cand.get("is_welcome") else f"{cid}-{cand['discount_pct']}")
    v = validate_offer(cand, data, rules, store.history_for(cid))
    notes = []
    if not v.ok and v.adjusted_offer:
        notes.append(f"Proposed {cand['discount_pct']}%, rules lowered it to {v.adjusted_offer['discount_pct']}%.")
        store.log_event("adjusted", {"source": cand["source"], "customer": cid, "proposed": cand["discount_pct"], "final": v.adjusted_offer["discount_pct"], "reasons": v.reasons})
        cand = with_discount(cand, v.adjusted_offer["discount_pct"])
        cand["code"] = f"WELCOME{cand['discount_pct']}-{cid}" if cand.get("is_welcome") else f"{cid}-{cand['discount_pct']}"
    elif not v.ok:
        store.log_event("blocked", {"source": cand["source"], "customer": cid, "reasons": v.reasons})
        return {"profile": prof, "offer": None, "explanation": "Offer blocked by rules: " + "; ".join(v.reasons), "notes": v.reasons, "used_ai": used_ai}
    return {"profile": prof, "offer": cand, "explanation": cand["rationale"], "notes": notes, "used_ai": used_ai}
