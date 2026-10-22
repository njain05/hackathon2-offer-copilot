"""Pick a group of customers for an offer: by segment, city, favourite category, first-time or open cart.

Opted-out customers and customers still in their cooldown are left out here, and rules.validate_offer
checks the final list again, so a group offer can never reach someone the rules protect.
"""
from datetime import datetime

import pandas as pd

from src import store
from src.data_loader import TODAY

SEGMENT_LABELS = {"champion": "Top customers", "loyal": "Loyal regulars", "new": "New customers", "at_risk": "Going quiet",
                  "lapsed": "Lapsed", "price_sensitive": "Deal hunters", "regular": "Regular buyers"}
EMPTY = {"segments": [], "cities": [], "categories": [], "first_time_only": False, "with_cart": False}


def clean(filters: dict | None) -> dict:
    f = {**EMPTY, **(filters or {})}
    return {"segments": [str(x) for x in f["segments"]], "cities": [str(x) for x in f["cities"]],
            "categories": [str(x) for x in f["categories"]], "first_time_only": bool(f["first_time_only"]), "with_cart": bool(f["with_cart"])}


def label(filters: dict) -> str:
    """'Lapsed · Ludhiana, Amritsar · likes Nuts' (plain words for cards and tables)."""
    f = clean(filters)
    parts = [", ".join(SEGMENT_LABELS.get(s, s) for s in f["segments"]) or ("First-time customers" if f["first_time_only"] else "All customers")]
    if f["first_time_only"] and f["segments"]:
        parts.append("first-time")
    if f["cities"]:
        parts.append(", ".join(f["cities"]))
    if f["categories"]:
        parts.append("likes " + ", ".join(f["categories"]))
    if f["with_cart"]:
        parts.append("items in cart")
    return " · ".join(parts)


def in_cooldown(cid: str, rules: dict) -> bool:
    days = rules["global"]["cooldown_days_per_customer"]
    for h in store.history_for(cid):
        if h.get("created_at") and (TODAY - datetime.fromisoformat(h["created_at"]).date()).days < days:
            return True
    return False


def select(data: dict, segs: pd.DataFrame, rules: dict, filters: dict | None) -> dict:
    f = clean(filters)
    c = data["customers"].merge(segs[["customer_id", "segment"]], on="customer_id")
    if f["segments"]:
        c = c[c.segment.isin(f["segments"])]
    if f["cities"]:
        c = c[c.city.isin(f["cities"])]
    if f["categories"]:
        c = c[c.preferred_category.isin(f["categories"])]
    if f["first_time_only"]:
        c = c[c.orders_total == 0]
    if f["with_cart"]:
        c = c[c.cart_items > 0]
    matched = len(c)
    opted_out = c[~c.marketing_opt_in] if rules["global"].get("respect_marketing_opt_in", True) else c.iloc[0:0]
    c = c[~c.customer_id.isin(opted_out.customer_id)]
    cooling = [cid for cid in c.customer_id if in_cooldown(cid, rules)]
    c = c[~c.customer_id.isin(cooling)]
    return {"filters": f, "label": label(f), "matched": matched, "customer_ids": list(c.customer_id), "count": int(len(c)),
            "excluded_opt_out": int(len(opted_out)), "excluded_cooldown": len(cooling),
            "sample": [f"{r.name} ({r.city})" for r in c.head(5).itertuples()]}


def options(data: dict) -> dict:
    """Choices for the dashboard's audience and product pickers."""
    c, p = data["customers"], data["products"]
    return {"segments": [{"key": k, "label": v} for k, v in SEGMENT_LABELS.items()],
            "cities": sorted(c.city.unique().tolist()), "categories": sorted(p.category.unique().tolist()),
            "products": p[["product_id", "product_name", "category", "price", "margin_pct", "stock_units"]].to_dict("records")}
