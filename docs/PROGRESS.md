# PROGRESS

## Built (MVP, verified by `python demo.py`)
- data/dryfruit_synthetic_data.xlsx (+ scripts/generate_data.py): 220 customers, 1,314 orders, events; fields incl. tier, city_tier, return_rate, points_expiring, cart_items
- src/data_loader, segments (7 inferred segments), rules (validate_offer + auto-adjust), store (JSON), recommender (rule-based campaigns + per-customer offers), agent (Claude API, JSON, fallback), messages, bot (customer + seller mode), demo.py, tests/test_rules.py
- Guardrail proven: a 60% "AI" offer is cut to the Makhana cap (15%) and logged in data/audit.jsonl

## NOT yet verified / todo
- Bot rebuilt (P1) and tested offline (tests/test_bot.py); real-Telegram run pending the user's token
- Claude path never run (needs ANTHROPIC_API_KEY); fallback path works
- pytest not run in sandbox (no network); run `pytest` locally
- Replace synthetic catalogue/prices/margins with the owner's real dry-fruit business numbers (NOT customer data)
- /stats uplift, README, CLAUDE.md final polish

## Decisions
- Bot has two modes: customer (default, synthetic shopper per classmate) and seller (/seller <SELLER_CODE>)
- AI personalises discount + wording; rules.validate_offer always has the last word
- 2026-10-22: Git initialised (branch `main`), `.venv` (Python 3.14) + requirements.txt. `src/data_loader.py` restored (an accidental overwrite) and `tests/test_data_loader.py` added; 11 tests pass. **`src/bot.py` is currently a minimal /start stub** — the full customer+seller bot was lost and is rebuilt in P1.
- 2026-10-22 P1: `src/bot.py` rebuilt (customer + seller mode, Allow/Reject with re-validation, public offers pushed to targeted shopper chats, /offer re-shows a live personal offer instead of hitting cooldown, error handler). Added `store.all_shoppers()`. `tests/test_bot.py` drives the real handlers with fake updates (19 tests pass). README "Run the bot".
- 2026-10-22 P4: `web/offers.html` (no framework; Rozha One + Hind; date-brown/almond/saffron palette; discount seal, before/after prices, tear-off code stub with Copy, countdown, Diwali/Navratri ribbon, empty + load-error states, auto-refresh 60s). `store.export_public_offers` now adds `products: [{name, price}]` (CONTRACTS updated). `web/offers.json` is runtime (git-ignored); `web/offers.sample.json` + `?sample` for previewing. `.claude/launch.json` runs the page server. 20 tests pass. Note for P6: campaign codes like WE10 / COME15 are weak (recommender takes the first word of the title).
