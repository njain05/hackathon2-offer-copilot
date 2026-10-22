# PROGRESS

## Built (MVP, verified by `python demo.py`)
- data/dryfruit_synthetic_data.xlsx (+ scripts/generate_data.py): 220 customers, 1,314 orders, events; fields incl. tier, city_tier, return_rate, points_expiring, cart_items
- src/data_loader, segments (7 inferred segments), rules (validate_offer + auto-adjust), store (JSON), recommender (rule-based campaigns + per-customer offers), agent (Claude API, JSON, fallback), messages, bot (customer + seller mode), demo.py, tests/test_rules.py
- Guardrail proven: a 60% "AI" offer is cut to the Makhana cap (15%) and logged in data/audit.jsonl

## NOT yet verified / todo
- Bot never run against real Telegram (needs TELEGRAM_BOT_TOKEN in .env)
- Claude path never run (needs ANTHROPIC_API_KEY); fallback path works
- web/offers.html (offers page) not built
- pytest not run in sandbox (no network); run `pytest` locally
- Replace synthetic catalogue/prices/margins with the owner's real dry-fruit business numbers (NOT customer data)
- /stats uplift, README, CLAUDE.md final polish

## Decisions
- Bot has two modes: customer (default, synthetic shopper per classmate) and seller (/seller <SELLER_CODE>)
- AI personalises discount + wording; rules.validate_offer always has the last word
- 2026-10-22: Git initialised (branch `main`), `.venv` (Python 3.14) + requirements.txt. `src/data_loader.py` restored (an accidental overwrite) and `tests/test_data_loader.py` added; 11 tests pass. **`src/bot.py` is currently a minimal /start stub** — the full customer+seller bot was lost and is rebuilt in P1.
