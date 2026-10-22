"""Load the synthetic Excel into DataFrames."""
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TODAY: date = date(2026, 10, 22)  # "today" in the simulation
DATA_FILE = ROOT / "data" / "dryfruit_synthetic_data.xlsx"
SHEETS = ["customers", "products", "orders", "order_items", "events", "rules"]


def load_all(path: Path | str = DATA_FILE) -> dict[str, pd.DataFrame]:
    """Return {sheet_name: DataFrame} for every contract sheet, with date columns parsed."""
    data = pd.read_excel(path, sheet_name=SHEETS)
    c = data["customers"]
    c["join_date"] = pd.to_datetime(c["join_date"])
    c["last_order_date"] = pd.to_datetime(c["last_order_date"])
    c["cart_product_ids"] = c["cart_product_ids"].fillna("")
    o = data["orders"]
    o["order_date"] = pd.to_datetime(o["order_date"])
    o["coupon_used"] = o["coupon_used"].fillna("")
    data["events"]["date"] = pd.to_datetime(data["events"]["date"])
    return data


if __name__ == "__main__":
    for name, df in load_all().items():
        print(f"{name:12s} {len(df):5d} rows")
