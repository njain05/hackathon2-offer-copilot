"""Infer customer segments from behaviour (segments are not labelled in the data)."""
import pandas as pd

from src.data_loader import TODAY, load_all

SEGMENTS = ["champion", "loyal", "new", "at_risk", "lapsed", "price_sensitive", "regular"]

NEW_DAYS = 45          # joined within this many days
AT_RISK_DAYS = 30      # silent longer than this => at risk
LAPSED_DAYS = 60       # silent longer than this => lapsed
PRICE_SENSITIVE_SHARE = 0.6
CHAMPION_ORDERS, CHAMPION_LTV = 8, 12000
LOYAL_ORDERS = 5


def _one(c) -> tuple[str, str]:
    age_days = (pd.Timestamp(TODAY) - c.join_date).days
    since = c.days_since_last_order
    if age_days <= NEW_DAYS:
        return "new", f"Joined {age_days} days ago, {int(c.orders_total)} order(s) so far"
    if pd.isna(since) or since > LAPSED_DAYS:
        return "lapsed", f"No order for {'a long time' if pd.isna(since) else f'{int(since)} days'}"
    if since > AT_RISK_DAYS:
        return "at_risk", f"Last order {int(since)} days ago; {int(c.orders_total)} orders before that"
    if c.discount_order_share >= PRICE_SENSITIVE_SHARE and c.orders_total >= 2:
        return "price_sensitive", f"{int(c.discount_order_share*100)}% of orders used a discount"
    if c.orders_total >= CHAMPION_ORDERS and c.lifetime_value >= CHAMPION_LTV:
        return "champion", f"{int(c.orders_total)} orders, ₹{int(c.lifetime_value):,} spent; buys without needing a discount"
    if c.orders_total >= LOYAL_ORDERS:
        return "loyal", f"{int(c.orders_total)} orders, last one {int(since)} days ago"
    return "regular", f"{int(c.orders_total)} orders, last one {int(since)} days ago"


def segment_customers(data: dict) -> pd.DataFrame:
    rows = []
    for c in data["customers"].itertuples():
        seg, reason = _one(c)
        rows.append({"customer_id": c.customer_id, "segment": seg, "reason": reason})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    d = load_all()
    s = segment_customers(d)
    print(s.segment.value_counts().to_string())
    for seg in SEGMENTS:
        print(f"\n[{seg}]")
        print(s[s.segment == seg].head(3).to_string(index=False))
