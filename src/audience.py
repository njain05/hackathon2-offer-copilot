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
EMPTY = {"segments": [], "cities": [], "categories": [], "first_time_only": False, "with_cart": False, "rank_by": "none", "limit": 0}
RANKS = {  # rank_by -> (column, highest first?, plain words)
    "spend": ("lifetime_value", True, "by spend"), "orders": ("orders_total", True, "by number of orders"),
    "recent": ("days_since_last_order", False, "by most recent order"), "cart": ("cart_items", True, "by items in cart"),
}


def clean(filters: dict | None) -> dict:
    f = {**EMPTY, **(filters or {})}
    rank = f["rank_by"] if f["rank_by"] in RANKS else "none"
    return {"segments": [str(x) for x in f["segments"]], "cities": [str(x) for x in f["cities"]],
            "categories": [str(x) for x in f["categories"]], "first_time_only": bool(f["first_time_only"]), "with_cart": bool(f["with_cart"]),
            "rank_by": rank, "limit": max(0, int(f["limit"] or 0))}


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
    if f["limit"]:
        parts.insert(0, f"Top {f['limit']} {RANKS.get(f['rank_by'], RANKS['spend'])[2]}")
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
    c = _ranked(c, f)
    if f["limit"]:
        c = c.head(f["limit"])  # top N among the customers the rules allow
    return {"filters": f, "label": label(f), "matched": matched, "customer_ids": list(c.customer_id), "count": int(len(c)),
            "excluded_opt_out": int(len(opted_out)), "excluded_cooldown": len(cooling),
            "sample": [f"{r.name} ({r.city})" for r in c.head(5).itertuples()]}


def _ranked(c: pd.DataFrame, f: dict) -> pd.DataFrame:
    rank = f["rank_by"] if f["rank_by"] in RANKS else ("spend" if f["limit"] else "none")
    if rank == "none":
        return c
    col, desc, _ = RANKS[rank]
    return c.sort_values(col, ascending=not desc, na_position="last")


def people(data: dict, segs: pd.DataFrame, ids: list[str], rank_by: str = "spend", limit: int = 50) -> list[dict]:
    """Rows to show the seller: who these customers are (synthetic data)."""
    c = data["customers"].merge(segs[["customer_id", "segment"]], on="customer_id")
    c = c[c.customer_id.isin(ids)]
    c = _ranked(c, {"rank_by": rank_by if rank_by in RANKS else "spend", "limit": 1})
    return [{"customer_id": r.customer_id, "name": r.name, "city": r.city, "group": SEGMENT_LABELS.get(r.segment, r.segment),
             "orders": int(r.orders_total), "spend": int(r.lifetime_value),
             "last_order_days": None if pd.isna(r.days_since_last_order) else int(r.days_since_last_order),
             "cart_items": int(r.cart_items), "opted_in": bool(r.marketing_opt_in)} for r in c.head(limit).itertuples()]


def find(data: dict, segs: pd.DataFrame, filters: dict | None, limit: int = 20) -> dict:
    """Customers matching filters, ranked; opted-out customers included but flagged (this is a list, not an offer)."""
    f = clean(filters)
    c = data["customers"].merge(segs[["customer_id", "segment"]], on="customer_id")
    for key, col in (("segments", "segment"), ("cities", "city"), ("categories", "preferred_category")):
        if f[key]:
            c = c[c[col].isin(f[key])]
    if f["first_time_only"]:
        c = c[c.orders_total == 0]
    if f["with_cart"]:
        c = c[c.cart_items > 0]
    rank = f["rank_by"] if f["rank_by"] in RANKS else "spend"
    ranked = _ranked(c, {"rank_by": rank, "limit": 1})
    n = f["limit"] or limit
    return {"label": label({**f, "limit": 0}), "total": int(len(c)), "rank_by": RANKS[rank][2],
            "customers": people(data, segs, list(ranked.customer_id.head(n)), rank, n)}


def options(data: dict) -> dict:
    """Choices for the dashboard's audience and product pickers."""
    c, p = data["customers"], data["products"]
    return {"segments": [{"key": k, "label": v} for k, v in SEGMENT_LABELS.items()],
            "cities": sorted(c.city.unique().tolist()), "categories": sorted(p.category.unique().tolist()),
            "products": p[["product_id", "product_name", "category", "price", "margin_pct", "stock_units"]].to_dict("records")}
