# CONTRACTS — shared interfaces (do not break; update here if you must change)

All slices are built in separate chats. These contracts are what keeps them compatible.

## Data (data/dryfruit_synthetic_data.xlsx)
- `customers`: customer_id, name, city, age, acquisition_channel, join_date, marketing_opt_in(bool), preferred_category, orders_total, lifetime_value, avg_order_value, last_order_date, discount_order_share(0-1), days_since_last_order, reward_points
- `products`: product_id, product_name, category, cost_price, price, stock_units, festival_tag(navratri|gifting|None), margin_pct
- `orders`: order_id, customer_id, order_date, discount_pct, order_value, coupon_used, payment
- `order_items`: order_id, product_id, quantity, unit_price
- `events`: customer_id, date, event(view|add_to_cart|checkout_started), product_id, converted_within_24h
- `rules`: starter limits (the live source of truth is `rules.yaml`)
Customer segments are NOT labelled in data; `src/segments.py` infers them.

## rules.yaml (single source of truth for limits)
```yaml
global:
  max_discount_pct: 20
  min_margin_pct_after_discount: 15
  cooldown_days_per_customer: 7
  max_offers_per_customer_per_month: 3
  min_stock_for_offer: 20
  respect_marketing_opt_in: true
category_caps_pct: {Makhana: 15, Nuts: 12, Fasting: 10, ...}   # optional per-category max discount
first_order: {welcome_discount_pct: 10, min_order_value: 499}  # welcome offer: first-order customers only, once ever
```

## src/data_loader.py
```python
TODAY: date = date(2026,10,22)
load_all() -> dict[str, pd.DataFrame]   # keys = sheet names above (dates parsed)
```

## src/segments.py
```python
SEGMENTS = ["champion","loyal","new","at_risk","lapsed","price_sensitive","regular"]
segment_customers(data) -> pd.DataFrame   # customer_id, segment, recency, frequency, monetary, reason(str)
```

## Offer schema (dict / dataclass `Offer` in src/store.py)
```python
{
 "offer_id": "OF-0001",
 "kind": "public" | "personal",         # public = shown on offers page; personal = single customer coupon
 "title": str, "message": str,          # customer-facing text
 "target": "ALL" | "<segment>" | "<customer_id>",
 "product_ids": [str], "category": str | None,
 "discount_pct": int,                   # percent off
 "code": str,                           # e.g. NAVRATRI15
 "valid_from": "YYYY-MM-DD", "valid_to": "YYYY-MM-DD",
 "rationale": str,                      # WHY (shown to seller on the card)
 "expected_impact": str,                # short plain-English estimate
 "source": "rule_engine" | "ai_agent" | "seller_chat",
 "status": "pending" | "approved" | "rejected" | "blocked" | "ended",   # ended = seller took a live offer down
 "blocked_reasons": [str],              # filled when rules reject
 "created_at": iso timestamp, "decided_at": iso timestamp | None,
 "is_welcome": bool (optional),          # first-order welcome offer (personal); checked by rules first_order section
 "min_order_value": int (optional)       # ₹ minimum basket for the offer
}
```

## src/rules.py
```python
load_rules(path="rules.yaml") -> dict
validate_offer(offer: dict, data: dict, rules: dict, history: list[dict]) -> ValidationResult
# ValidationResult(ok: bool, reasons: list[str], adjusted_offer: dict | None)
# Checks: discount<=global cap & category cap; margin after discount >= floor for EVERY product;
# stock >= min for every product; customer opt-in; cooldown + monthly cap (personal offers); dates valid; discount>0.
# If only the discount is too high, adjusted_offer = same offer with the max allowed discount.
```

## src/store.py (JSON file data/state.json)
```python
add_offer(offer) -> offer_id         # assigns offer_id, created_at
get_offers(status=None) -> list[dict]
set_status(offer_id, status, blocked_reasons=None) -> dict
export_public_offers() -> None       # writes web/offers.json with approved public offers (+ products: [{name, price}] for the page)
history_for(customer_id) -> list[dict]   # approved/sent personal offers (for cooldown)
log_event(kind, payload) -> None     # audit log incl. AI proposals blocked/adjusted (data/audit.jsonl)
```

## src/recommender.py (rule-based, no AI)
```python
generate_candidates(data, segments, today) -> list[dict]   # Offer dicts, status "pending", source "rule_engine"
# Pipeline: candidates -> validate_offer -> blocked ones logged, valid ones stored.
```

## src/messages.py
```python
seller_card(offer) -> str      # Telegram HTML text for the seller approval card
customer_message(offer) -> str # customer-facing offer text
```

## src/agent.py (AI)
```python
propose_offers(data, segments, today, rules) -> list[dict]   # LLM returns JSON offers; untrusted
# Caller MUST pass each through validate_offer. Agent never writes to store directly.
```

## src/bot.py
Telegram bot (python-telegram-bot v21). Commands: /start, /offers (generate+send pending cards), /pending, /live, /customer <id or name>, /stats, /help.
Callback data: `allow:<offer_id>` / `reject:<offer_id>`. Allow => re-validate => set_status approved => export_public_offers.
Env: TELEGRAM_BOT_TOKEN (customer bot), SELLER_BOT_TOKEN (optional separate seller bot; both run in one process), SELLER_CODE (/seller <code> unlocks seller mode).

## src/chat.py (seller chat in plain language)
```python
interpret(text, data, segs, rules) -> {"reply": str} | {"tool": name, "input": dict} | None   # one Claude call; None = no AI
TOOLS = [suggest_for_customer, draft_offer, run_campaigns, show_stats, list_offers, end_offer, change_rule]
# The bot executes the tool with the same rule-checked code as the slash commands:
# draft_offer -> run_pipeline -> Allow/Reject card; change_rule / end_offer -> Confirm button first.
```

## src/rules.py (editing)
```python
describe_change(key, value, category="", rules=None) -> (preview: str, error: str | None)
apply_change(key, value, category="", path=rules.yaml) -> preview   # rewrites one number, keeps comments; refuses unsafe values
```

## web/offers.html
Static page, fetches `offers.json` (approved public offers). No framework. Mobile friendly.
