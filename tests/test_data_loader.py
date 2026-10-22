import pandas as pd

from src.data_loader import SHEETS, TODAY, load_all


def test_sheets_exist():
    data = load_all()
    assert set(SHEETS) <= set(data)


def test_customers():
    customers = load_all()["customers"]
    assert len(customers) == 220
    assert customers["customer_id"].notna().all()
    assert customers["customer_id"].is_unique


def test_dates_parsed():
    data = load_all()
    assert pd.api.types.is_datetime64_any_dtype(data["orders"]["order_date"])
    assert data["orders"]["order_date"].max().date() <= TODAY
