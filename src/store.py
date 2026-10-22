"""Tiny JSON store: offers, shopper assignments, seller id, audit log, public offers export."""
import json
from datetime import datetime
from functools import lru_cache

import pandas as pd

from src.data_loader import DATA_FILE, ROOT

STATE = ROOT / "data" / "state.json"
AUDIT = ROOT / "data" / "audit.jsonl"
PUBLIC = ROOT / "web" / "offers.json"


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"offers": [], "shoppers": {}, "seller_chat_id": None}


def _save(s: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


def add_offer(offer: dict) -> str:
    s = _load()
    offer = {"status": "pending", "blocked_reasons": [], "decided_at": None, **offer}
    offer["offer_id"] = f"OF-{len(s['offers']) + 1:04d}"
    offer["created_at"] = datetime.now().isoformat(timespec="seconds")
    s["offers"].append(offer)
    _save(s)
    return offer["offer_id"]


def get_offers(status: str | None = None) -> list[dict]:
    return [o for o in _load()["offers"] if status is None or o["status"] == status]


def get_offer(offer_id: str) -> dict | None:
    return next((o for o in _load()["offers"] if o["offer_id"] == offer_id), None)


def update_offer(offer_id: str, **changes) -> dict:
    s = _load()
    for o in s["offers"]:
        if o["offer_id"] == offer_id:
            o.update(changes)
            _save(s)
            return o
    raise KeyError(offer_id)


def set_status(offer_id: str, status: str, blocked_reasons: list[str] | None = None) -> dict:
    o = update_offer(offer_id, status=status, decided_at=datetime.now().isoformat(timespec="seconds"),
                     blocked_reasons=blocked_reasons or [])
    export_public_offers()
    return o


@lru_cache(maxsize=1)
def _catalogue() -> dict[str, tuple[str, int]]:
    p = pd.read_excel(DATA_FILE, sheet_name="products")
    return {r.product_id: (r.product_name, int(r.price)) for r in p.itertuples()}


def for_web(offer: dict) -> dict:
    """Offer plus the product names and prices the web page shows."""
    cat = _catalogue()
    items = [{"name": cat[x][0], "price": cat[x][1]} for x in offer["product_ids"] if x in cat]
    return {**offer, "products": items}


def export_public_offers() -> None:
    """Write approved public offers (with product names + prices) to web/offers.json."""
    live = [for_web(o) for o in get_offers("approved") if o["kind"] == "public"]
    PUBLIC.parent.mkdir(exist_ok=True)
    PUBLIC.write_text(json.dumps(live, indent=2, ensure_ascii=False), encoding="utf-8")


def history_for(customer_id: str) -> list[dict]:
    return [o for o in get_offers() if o["target"] == customer_id and o["status"] in ("approved", "sent")]


def log_event(kind: str, payload: dict) -> None:
    AUDIT.parent.mkdir(exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, **payload}, ensure_ascii=False) + "\n")


def read_audit(n: int = 5) -> list[dict]:
    if not AUDIT.exists():
        return []
    return [json.loads(l) for l in AUDIT.read_text(encoding="utf-8").splitlines()][-n:]


def get_shopper(chat_id: int) -> str | None:
    return _load()["shoppers"].get(str(chat_id))


def assign_shopper(chat_id: int, customer_id: str) -> None:
    s = _load(); s["shoppers"][str(chat_id)] = customer_id; _save(s)


def remove_shopper(chat_id: int) -> None:
    s = _load(); s["shoppers"].pop(str(chat_id), None); _save(s)


def all_shoppers() -> dict[str, str]:
    """{chat_id: customer_id} for every classmate who pressed /start."""
    return dict(_load()["shoppers"])


def assigned_ids() -> set[str]:
    return set(_load()["shoppers"].values())


def get_seller() -> int | None:
    return _load().get("seller_chat_id")


def set_seller(chat_id: int) -> None:
    s = _load(); s["seller_chat_id"] = chat_id; _save(s)
