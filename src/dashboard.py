"""Marketing analytics for the dashboard page: python -m src.dashboard writes web/dashboard.json.

Reads the store's data export (synthetic for now), the offers in data/state.json and the guardrail log.
The bot rebuilds the file whenever offers change, so the page stays current.
"""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src import store
from src.data_loader import ROOT, TODAY, load_all
from src.rules import load_rules
from src.segments import SEGMENTS, segment_customers

OUT = Path(os.getenv("OFFER_DASHBOARD_FILE", ROOT / "web" / "dashboard.json"))
STORE_NAME = "Nishtha's Pantry"
DIWALI = date(2026, 11, 8)
FESTIVALS = [("Rakhi", "2026-08-20", "2026-08-28"), ("Navratri", "2026-10-11", "2026-10-19")]

SEGMENT_INFO = {  # plain name, what the engine does for them (amounts come from the engine + rules.yaml)
    "champion": ("Top customers", "No discount: they buy anyway"),
    "loyal": ("Loyal regulars", "No discount: they buy anyway"),
    "new": ("New customers", "Welcome / second-order offer"),
    "at_risk": ("Going quiet (30-60 days)", "Win-back offer on their favourite category"),
    "lapsed": ("Lapsed (60+ days)", "Stronger come-back offer"),
    "price_sensitive": ("Deal hunters", "Small discount only"),
    "regular": ("Regular buyers", "Light nudge on their favourite category"),
}
TARGET_NAMES = {"ALL": "Everyone", **{k: v[0] for k, v in SEGMENT_INFO.items()}}


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


def _revenue(orders: pd.DataFrame, start: date, end: date) -> float:
    o = orders[(orders.order_date >= _ts(start)) & (orders.order_date < _ts(end))]
    return float(o.order_value.sum())


def kpis(data: dict, segs: pd.DataFrame, offers: list[dict], audit: list[dict]) -> dict:
    c, o, p = data["customers"], data["orders"], data["products"].set_index("product_id")
    end = TODAY + timedelta(days=1)
    rev30, prev30 = _revenue(o, end - timedelta(days=30), end), _revenue(o, end - timedelta(days=60), end - timedelta(days=30))
    n30 = int(((o.order_date >= _ts(end - timedelta(days=30))) & (o.order_date < _ts(end))).sum())
    quiet_ids = set(segs[segs.segment.isin(["at_risk", "lapsed"])].customer_id)
    quiet = c[c.customer_id.isin(quiet_ids)]
    carts = c[c.cart_items > 0]
    cart_value = sum(p.loc[x, "price"] for ids in carts.cart_product_ids for x in str(ids).split(",") if x in p.index)
    status = pd.Series([x["status"] for x in offers]).value_counts().to_dict() if offers else {}
    live = [x for x in offers if x["status"] == "approved" and x["valid_to"] >= TODAY.isoformat()]
    return {
        "customers": int(len(c)), "active_30": int((c.days_since_last_order <= 30).sum()),
        "revenue_30": round(rev30), "revenue_prev_30": round(prev30),
        "revenue_delta_pct": round((rev30 - prev30) / prev30 * 100, 1) if prev30 else None,
        "orders_30": n30, "aov_30": round(rev30 / n30) if n30 else 0,
        "quiet_customers": int(len(quiet)),
        "quiet_monthly_spend": round(float(quiet.lifetime_value.sum()) / 6),  # data covers ~6 months
        "carts_open": int(len(carts)), "cart_value": round(float(cart_value)),
        "offers_live": len(live), "offers_pending": int(status.get("pending", 0)),
        "offers_rejected": int(status.get("rejected", 0)), "offers_blocked": int(status.get("blocked", 0)),
        "offers_ended": int(status.get("ended", 0)),
        "guardrail_saves": sum(e.get("kind") in ("adjusted", "blocked") for e in audit),
        "first_time_customers": int((c.orders_total == 0).sum()),
    }


def _first_week(weeks: int) -> date:
    """Monday that starts the window of `weeks` COMPLETE weeks (this unfinished week is left out)."""
    this_monday = TODAY - timedelta(days=TODAY.weekday())
    return this_monday - timedelta(weeks=weeks)


def weekly_revenue(data: dict, weeks: int = 25) -> list[dict]:
    o = data["orders"]
    start = _first_week(weeks)
    out = []
    for i in range(weeks):
        a = start + timedelta(weeks=i)
        w = o[(o.order_date >= _ts(a)) & (o.order_date < _ts(a + timedelta(days=7)))]
        out.append({"week": a.isoformat(), "revenue": round(float(w.order_value.sum())), "orders": int(len(w))})
    return out


def weekly_festival_units(data: dict, weeks: int = 25) -> dict:
    """Units sold per week of navratri-tagged (fasting) and gifting-tagged products: the festival effect."""
    o, it, p = data["orders"], data["order_items"], data["products"]
    lines = it.merge(o[["order_id", "order_date"]], on="order_id").merge(p[["product_id", "festival_tag"]], on="product_id")
    start = _first_week(weeks)
    out = {"navratri": [], "gifting": []}
    for i in range(weeks):
        a = start + timedelta(weeks=i)
        w = lines[(lines.order_date >= _ts(a)) & (lines.order_date < _ts(a + timedelta(days=7)))]
        for tag in out:
            out[tag].append(int(w[w.festival_tag == tag].quantity.sum()))
    return out


def segment_table(data: dict, segs: pd.DataFrame) -> list[dict]:
    c = data["customers"].merge(segs[["customer_id", "segment"]], on="customer_id")
    rows = []
    for s in SEGMENTS:
        g = c[c.segment == s]
        if g.empty:
            continue
        name, action = SEGMENT_INFO[s]
        rows.append({"key": s, "name": name, "action": action, "customers": int(len(g)),
                     "share_pct": round(len(g) / len(c) * 100), "avg_spend": round(float(g.lifetime_value.mean())),
                     "avg_days_since_order": None if g.days_since_last_order.isna().all() else round(float(g.days_since_last_order.mean()))})
    return rows


def category_table(data: dict, rules: dict) -> list[dict]:
    o, it, p = data["orders"], data["order_items"], data["products"]
    recent = o[o.order_date >= _ts(TODAY - timedelta(days=29))][["order_id", "discount_pct"]]
    lines = it.merge(recent, on="order_id").merge(p[["product_id", "category"]], on="product_id")
    lines["value"] = lines.quantity * lines.unit_price * (1 - lines.discount_pct / 100)
    sold = lines.groupby("category").agg(revenue=("value", "sum"), units=("quantity", "sum"))
    stock = p.groupby("category").agg(stock=("stock_units", "sum"), margin=("margin_pct", "mean"))
    t = stock.join(sold, how="left").fillna(0)
    caps = rules.get("category_caps_pct", {})
    rows = []
    for cat, r in t.sort_values("revenue", ascending=False).iterrows():
        weekly = r.units / (30 / 7)
        rows.append({"category": cat, "revenue_30": round(float(r.revenue)), "units_30": int(r.units), "stock": int(r.stock),
                     "weeks_of_stock": round(float(r.stock / weekly), 1) if weekly else None,
                     "avg_margin_pct": round(float(r.margin), 1), "discount_cap_pct": caps.get(cat, rules["global"]["max_discount_pct"])})
    return rows


def offer_rows(offers: list[dict]) -> list[dict]:
    keep = [o for o in offers if o["status"] in ("pending", "approved", "blocked", "rejected", "ended")]
    rows = []
    for o in sorted(keep, key=lambda x: x.get("created_at", ""), reverse=True)[:30]:
        live = o["status"] == "approved" and o["valid_to"] >= TODAY.isoformat()
        rows.append({"offer_id": o["offer_id"], "title": o["title"], "kind": o["kind"],
                     "audience": o.get("audience_label") or TARGET_NAMES.get(o["target"], o["target"]), "discount_pct": o["discount_pct"],
                     "group_size": len(o.get("customer_ids") or []) or None,
                     "status": "live" if live else ("expired" if o["status"] == "approved" else o["status"]),
                     "source": {"rule_engine": "Rule engine", "ai_agent": "AI agent", "seller_chat": "Seller chat", "dashboard": "Dashboard"}.get(o["source"], o["source"]),
                     "valid_to": o["valid_to"], "used": bool(o.get("used")), "reason": (o.get("blocked_reasons") or [""])[0]})
    return rows


def audit_rows(audit: list[dict]) -> list[dict]:
    rows = []
    for e in reversed(audit[-12:]):
        rows.append({"at": e.get("at", ""), "kind": e.get("kind"), "what": e.get("title") or e.get("customer") or e.get("change", ""),
                     "source": {"rule_engine": "Rule engine", "ai_agent": "AI agent", "seller_chat": "Seller chat", "dashboard": "Dashboard"}.get(e.get("source"), e.get("source", "")),
                     "proposed": e.get("proposed"), "final": e.get("final"), "reason": (e.get("reasons") or [""])[0]})
    return rows


def results(data: dict, offers: list[dict], audit: list[dict]) -> dict:
    """How the offers are doing: reach, use, estimated sales and discount, against the store's no-offer baseline."""
    aov = data["customers"].set_index("customer_id").avg_order_value
    out = [o for o in offers if o["status"] in ("approved", "ended")]
    sent = sum(int(o.get("sent_to") or 0) for o in out)
    uses = [(o, cid) for o in out for cid in (o.get("used_by") or [])]
    if not uses:  # older offers only stored a used flag
        uses = [(o, o["target"]) for o in out if o.get("used") and o["target"] in aov.index]
    sales = sum(float(aov.get(cid, 0) or 0) for _, cid in uses)
    discount = sum(float(aov.get(cid, 0) or 0) * o["discount_pct"] / 100 for o, cid in uses)
    ev = data["events"]
    carts = ev[ev.event.isin(["add_to_cart", "checkout_started"])]
    baseline = float(carts.converted_within_24h.mean() * 100) if len(carts) else None
    return {"offers_live_or_done": len(out), "sent": sent, "used": len(uses),
            "use_rate_pct": round(len(uses) / sent * 100, 1) if sent else None,
            "est_sales": round(sales), "est_discount": round(discount),
            "baseline_cart_conversion_pct": round(baseline, 1) if baseline is not None else None,
            "guardrail_saves": sum(e.get("kind") in ("adjusted", "blocked") for e in audit),
            "rule_changes": sum(e.get("kind") == "rule_change" for e in audit)}


def build(data: dict, segs: pd.DataFrame, rules: dict) -> dict:
    offers, audit = store.get_offers(), store.read_audit(10_000)
    return {
        "store": STORE_NAME, "today": TODAY.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days_to_diwali": (DIWALI - TODAY).days, "festivals": [{"name": n, "start": a, "end": b} for n, a, b in FESTIVALS],
        "kpis": kpis(data, segs, offers, audit), "results": results(data, offers, audit), "weekly": weekly_revenue(data), "festival_units": weekly_festival_units(data), "segments": segment_table(data, segs),
        "categories": category_table(data, rules), "offers": offer_rows(offers), "audit": audit_rows(audit),
        "rules": rules,
    }


def export(data: dict | None = None, segs: pd.DataFrame | None = None) -> None:
    data = data if data is not None else load_all()
    segs = segs if segs is not None else segment_customers(data)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(build(data, segs, load_rules()), indent=1, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    export()
    d = json.loads(OUT.read_text())
    print(f"Wrote {OUT.relative_to(ROOT)}:", json.dumps(d["kpis"], indent=1))
