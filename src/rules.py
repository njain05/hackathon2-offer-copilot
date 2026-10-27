"""Rules layer: the ONLY place discount limits are enforced. AI output is untrusted; everything passes here."""
from dataclasses import dataclass, field
from datetime import date, datetime
from math import floor
from pathlib import Path

import yaml

from src.data_loader import ROOT, TODAY


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    adjusted_offer: dict | None = None


def load_rules(path: str | Path = ROOT / "rules.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def max_discount(product, rules: dict) -> tuple[int, str]:
    """Highest discount allowed on one product, and which limit binds."""
    g = rules["global"]
    limits = [(int(g["max_discount_pct"]), f"global cap of {g['max_discount_pct']}%")]
    cat = rules.get("category_caps_pct", {}).get(product.category)
    if cat is not None:
        limits.append((int(cat), f"{product.category} cap of {cat}%"))
    floor_pct = g["min_margin_pct_after_discount"]
    m = floor(100 * (1 - product.cost_price / ((1 - floor_pct / 100) * product.price)))
    limits.append((max(m, 0), f"margin floor of {floor_pct}%"))
    return min(limits, key=lambda x: x[0])


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _check_welcome(offer, d, customers, history, rules, disc_reasons, other) -> None:
    """First-order welcome offer: new customers only, once ever, capped discount, minimum basket."""
    fo = rules.get("first_order")
    if not fo:
        other.append("Welcome offers are switched off (no first_order section in rules.yaml)")
        return
    target = offer.get("target", "")
    if target not in customers.index:
        other.append("A welcome offer must be for one customer")
        return
    n = int(customers.loc[target, "orders_total"])
    if n > 0:
        other.append(f"Welcome offers are only for first orders; this customer has ordered {n} time(s)")
    if any(h.get("is_welcome") and h.get("target") == target and h.get("status") in ("approved", "sent") for h in history):
        other.append("Customer already received a welcome offer (one per customer)")
    if d > fo["welcome_discount_pct"]:
        disc_reasons.append(f"Welcome offer {d}% is above the welcome limit of {fo['welcome_discount_pct']}%")
    if int(offer.get("min_order_value") or 0) < fo["min_order_value"]:
        other.append(f"Welcome offer must require a basket of at least ₹{fo['min_order_value']}")


def _check_group(offer, customers, history, g, other) -> None:
    """Group offer (picked on the dashboard): every listed customer must exist, be opted in and be out of cooldown."""
    ids = offer.get("customer_ids") or []
    if not ids:
        other.append("No customers in this group")
        return
    unknown = [c for c in ids if c not in customers.index]
    if unknown:
        other.append(f"{len(unknown)} unknown customer id(s) in the group")
    known = [c for c in ids if c in customers.index]
    if g.get("respect_marketing_opt_in", True):
        out = [c for c in known if not bool(customers.loc[c, "marketing_opt_in"])]
        if out:
            other.append(f"{len(out)} customer(s) in the group opted out of marketing")
    cool = g["cooldown_days_per_customer"]
    recent = {c for h in history if h.get("status") in ("approved", "sent") and h.get("created_at")
              and (TODAY - _d(h["created_at"][:10])).days < cool
              for c in ([h["target"]] if h.get("target") in customers.index else h.get("customer_ids") or [])}
    hit = [c for c in known if c in recent]
    if hit:
        other.append(f"{len(hit)} customer(s) in the group got an offer in the last {cool} days")


def validate_offer(offer: dict, data: dict, rules: dict, history: list[dict] | None = None) -> ValidationResult:
    history = history or []
    g = rules["global"]
    disc_reasons, other = [], []
    d = offer.get("discount_pct", 0) or 0
    if d <= 0:
        other.append("Discount must be above 0%")

    products = data["products"].set_index("product_id")
    pids = offer.get("product_ids") or []
    if not pids:
        other.append("Offer has no products")
    caps = []
    for pid in pids:
        if pid not in products.index:
            other.append(f"Unknown product {pid}")
            continue
        p = products.loc[pid]
        row = type("P", (), {"category": p.category, "cost_price": p.cost_price, "price": p.price})
        cap, why = max_discount(row, rules)
        caps.append(cap)
        if d > cap:
            disc_reasons.append(f"{p.product_name}: {d}% is above the allowed {cap}% ({why})")
        if p.stock_units < g["min_stock_for_offer"]:
            other.append(f"{p.product_name}: only {int(p.stock_units)} in stock (minimum {g['min_stock_for_offer']})")

    try:
        vf, vt = _d(offer["valid_from"]), _d(offer["valid_to"])
        if vt < vf:
            other.append("Offer ends before it starts")
        if vt < TODAY:
            other.append("Offer has already expired")
    except Exception:
        other.append("Offer dates are missing or invalid")

    target = offer.get("target", "")
    customers = data["customers"].set_index("customer_id")
    if target in customers.index:
        if g.get("respect_marketing_opt_in", True) and not bool(customers.loc[target, "marketing_opt_in"]):
            other.append("Customer has opted out of marketing")
        mine = [h for h in history if h.get("target") == target and h.get("status") in ("approved", "sent")]
        dates = sorted(_d(h["created_at"][:10]) for h in mine if h.get("created_at"))
        if dates and (TODAY - dates[-1]).days < g["cooldown_days_per_customer"]:
            other.append(f"Customer got an offer {(TODAY - dates[-1]).days} day(s) ago (cooldown {g['cooldown_days_per_customer']} days)")
        recent = [x for x in dates if (TODAY - x).days < 30]
        if len(recent) >= g["max_offers_per_customer_per_month"]:
            other.append(f"Customer already received {len(recent)} offers this month (cap {g['max_offers_per_customer_per_month']})")

    if offer.get("is_welcome"):
        _check_welcome(offer, d, customers, history, rules, disc_reasons, other)
    if offer.get("target") == "GROUP":
        _check_group(offer, customers, history, g, other)

    adjusted = None
    if offer.get("is_welcome") and rules.get("first_order"):
        caps.append(int(rules["first_order"]["welcome_discount_pct"]))
    if disc_reasons and not other and caps:
        new_d = min(caps)
        if new_d >= 1:
            adjusted = {**offer, "discount_pct": new_d}
    reasons = disc_reasons + other
    return ValidationResult(ok=not reasons, reasons=reasons, adjusted_offer=adjusted)


# ---------- editing rules.yaml from the seller chat (keeps every comment) ----------
EDITABLE = {  # key: (section, plain-English name, lowest, highest)
    "max_discount_pct": ("global", "Highest discount on any offer (%)", 1, 50),
    "min_margin_pct_after_discount": ("global", "Lowest profit margin after discount (%)", 0, 60),
    "cooldown_days_per_customer": ("global", "Days between personal offers to one customer", 0, 60),
    "max_offers_per_customer_per_month": ("global", "Personal offers per customer per month", 1, 10),
    "min_stock_for_offer": ("global", "Minimum stock to promote a product (units)", 0, 1000),
    "welcome_discount_pct": ("first_order", "First-order welcome discount (%)", 1, 50),
    "min_order_value": ("first_order", "Minimum basket for the welcome offer (₹)", 0, 10000),
}


def describe_change(key: str, value: int, category: str = "", rules: dict | None = None) -> tuple[str, str | None]:
    """Plain-English preview of a rule change, and an error if it is not allowed."""
    rules = rules or load_rules()
    value = int(value)
    if key == "category_cap":
        caps = rules.get("category_caps_pct", {})
        if category not in caps:
            return "", f"Unknown category '{category}'. Categories: {', '.join(caps)}"
        top = int(rules["global"]["max_discount_pct"])
        if not 0 <= value <= top:
            return "", f"A category limit must be between 0% and the overall limit of {top}%"
        return f"{category} discount limit: {caps[category]}% → {value}%", None
    if key not in EDITABLE:
        return "", f"'{key}' can't be changed from chat"
    section, name, lo, hi = EDITABLE[key]
    if not lo <= value <= hi:
        return "", f"{name} must be between {lo} and {hi}"
    if key == "max_discount_pct":
        over = [c for c, v in rules.get("category_caps_pct", {}).items() if v > value]
        if over:
            return "", f"{', '.join(over)} have higher category limits than {value}%; lower those first"
    return f"{name}: {rules[section][key]} → {value}", None


def apply_change(key: str, value: int, category: str = "", path: str | Path = ROOT / "rules.yaml") -> str:
    """Write one number into rules.yaml, keeping layout and comments. Returns the preview text."""
    preview, err = describe_change(key, value, category, load_rules(path))
    if err:
        raise ValueError(err)
    import re
    name = category if key == "category_cap" else key
    text = Path(path).read_text(encoding="utf-8")
    pattern = re.compile(rf"^(\s+{re.escape(name)}:\s*)(\S+)", re.M)
    if len(pattern.findall(text)) != 1:
        raise ValueError(f"Could not find exactly one '{name}' in rules.yaml")
    new_text = pattern.sub(lambda m: m.group(1) + str(int(value)), text)
    yaml.safe_load(new_text)  # refuse to write a broken file
    Path(path).write_text(new_text, encoding="utf-8")
    return preview


# ---------- python -m src.rules --check : read the rules back in plain English ----------
def explain(rules: dict) -> list[str]:
    """Every rule, its value and what it means, for a marketing person."""
    g = rules["global"]
    lines = [
        f"Highest discount on any offer: {g['max_discount_pct']}%",
        f"Lowest profit margin after the discount, on every product: {g['min_margin_pct_after_discount']}%",
        f"Days between two personal offers to the same customer: {g['cooldown_days_per_customer']}",
        f"Most personal offers one customer can get in a month: {g['max_offers_per_customer_per_month']}",
        f"Products need at least {g['min_stock_for_offer']} units in stock to be promoted",
        "Customers who opted out of marketing never get offers" if g.get("respect_marketing_opt_in", True) else "WARNING: opted-out customers can get offers",
    ]
    for cat, cap in (rules.get("category_caps_pct") or {}).items():
        lines.append(f"{cat}: at most {cap}% off")
    if fo := rules.get("first_order"):
        lines.append(f"First-order welcome offer: at most {fo['welcome_discount_pct']}%, on baskets of ₹{fo['min_order_value']} or more, once per customer")
    return lines


def check_file(path: str | Path = ROOT / "rules.yaml") -> list[str]:
    """Load and sanity-check rules.yaml. Raises ValueError with a plain-English message if it is broken."""
    try:
        rules = load_rules(path)
    except FileNotFoundError:
        raise ValueError(f"{path} is missing")
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        raise ValueError(f"rules.yaml can't be read{where}. Check the spacing (2 spaces before each rule) and that every rule looks like 'name: number'.")
    if not isinstance(rules, dict) or "global" not in rules:
        raise ValueError("rules.yaml needs a 'global:' section")
    need = ["max_discount_pct", "min_margin_pct_after_discount", "cooldown_days_per_customer", "max_offers_per_customer_per_month", "min_stock_for_offer"]
    missing = [k for k in need if k not in rules["global"]]
    if missing:
        raise ValueError(f"rules.yaml is missing: {', '.join(missing)}")
    problems = []
    for k in need:
        v = rules["global"][k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            problems.append(f"{k} must be a number of 0 or more (it is {v!r})")
    top = rules["global"].get("max_discount_pct", 0)
    for cat, cap in (rules.get("category_caps_pct") or {}).items():
        if not isinstance(cap, (int, float)) or isinstance(cap, bool):
            problems.append(f"{cat} limit must be a number (it is {cap!r})")
        elif cap > top:
            problems.append(f"{cat} limit {cap}% is above the overall limit of {top}% (the overall limit wins)")
    if problems:
        raise ValueError("; ".join(problems))
    return explain(rules)


if __name__ == "__main__":
    import sys
    target = sys.argv[sys.argv.index("--check") + 1] if "--check" in sys.argv and len(sys.argv) > sys.argv.index("--check") + 1 else ROOT / "rules.yaml"
    try:
        lines = check_file(target)
        print("✅ rules.yaml is valid. Every offer is checked against these rules:\n")
        for line in lines:
            print("  •", line)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
