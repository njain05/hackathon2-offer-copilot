# CLAUDE.md — Offer Copilot (Hackathon 2)

READ THIS FIRST in every session. Then read `docs/CONTRACTS.md` (interfaces) and `docs/PROGRESS.md` (what is done, decisions). Run `git status` before editing: other sessions have written into this folder before.

## What this is
**Offer Copilot** is an analytics and offer-decision system for an e-commerce store's sales/marketing team. The client store is *Nishtha's Pantry*, a fictional Indian dry-fruit shop (dry fruits, nuts, makhana, fasting foods); its data is a **synthetic export** in `data/dryfruit_synthetic_data.xlsx`.
- Profiles every customer from behaviour (7 segments + a plain-English reason), picks audiences, proposes offers (rule engine + Claude).
- **Every offer passes `rules.validate_offer` before anyone sees it**, and again when the seller approves it. AI proposes, rules decide, the seller has the final say.
- Delivers offers to customers through the **customer bot** (@perofferengine_Bot). The seller works in the **seller bot** (@offerengineseller_bot) and the **dashboard** (`web/dashboard.html` via `src/api.py`). `web/offers.html` is the store's public deals page.

Brief: `docs/Hackathon_2_Brief.pdf`. Judged on: offers stay within rules + reach classmates via the bot, data realism, rules usable by a marketing person, message/result design, advanced cases, business relevance.
Deadline: Tue 27 Oct 2026, 12:00. Submit: GitHub repo (source + README + this file) + the customer bot's username, bot kept running.

## Hard rules
1. **Synthetic data only.** Never real personal data.
2. **No secrets in Git.** Tokens, keys and SELLER_CODE live only in `.env` (see `.env.example`) or the host's settings. Never print or commit them; scan staged diffs before committing.
3. **Rules are the single source of truth for limits**: `rules.yaml`, enforced ONLY by `src/rules.py::validate_offer`. No other module hardcodes a discount limit.
4. **Never send or publish an offer that has not passed `validate_offer`.** AI output is untrusted input (structured JSON, still validated). Allow/Approve re-validates.
5. **Respect `docs/CONTRACTS.md`.** If you change an interface or schema, update CONTRACTS.md in the same commit and note it in PROGRESS.md.
6. One feature at a time; each with tests (`.venv/bin/python -m pytest -q`) and its own commit. **Commit freely, ask the user before every `git push`.**
7. Python 3.14 (`.venv`), small typed functions, plain-English messages (₹, short). Marketing-person usability matters: comments in `rules.yaml`, reasons a non-technical person understands.
8. Test in the browser with the `dashboard-preview` launch config (port 8001, separate `data/preview_*` files, no Telegram token) so real classmates and live offers are never touched.

## Layout
```
data/dryfruit_synthetic_data.xlsx  store export (customers, products, orders, order_items, events, rules)
data/state.json, data/audit.jsonl  runtime: offers/shoppers/sellers, guardrail log (git-ignored)
rules.yaml                         business limits, edited by marketing (or via bot/dashboard with Confirm)
scripts/generate_data.py           regenerates the Excel
src/data_loader.py                 load_all(), TODAY = 2026-10-22
src/segments.py                    7 segments with reasons (thresholds at the top)
src/audience.py                    group selection: segment/city/category/first-time/cart/top-N; opt-out + cooldown excluded
src/recommender.py                 campaigns, personal + welcome + group offers, run_pipeline (validate -> store/log)
src/agent.py                       Claude (claude-opus-5-5): campaigns, personalisation, chat turn; structured outputs
src/chat.py                        plain-language seller requests -> one tool (shared by seller bot and dashboard)
src/rules.py                       validate_offer, describe_change/apply_change (safe rules.yaml edits)
src/store.py                       JSON store + audit log + offers.json export + on_change hook
src/messages.py                    Telegram cards and customer messages
src/bot.py                         customer bot + seller bot (two-bot mode when SELLER_BOT_TOKEN is set)
src/dashboard.py, src/api.py       dashboard numbers; dashboard server + JSON API (X-Seller-Code)
src/serve.py                       hosted start: dashboard + both bots in one process ($PORT; RUN_BOTS=0 = dashboard only)
web/dashboard.html, web/offers.html
tests/                             pytest, one file per area (67 tests)
render.yaml, Dockerfile            deployment (Render Blueprint, as in hackathon 1)
docs/                              CONTRACTS, PROGRESS, PROMPTS, Hackathon_2_Brief.pdf
```

## Run
```
.venv/bin/python -m src.bot                          # both Telegram bots
DASHBOARD_PORT=8080 .venv/bin/python -m src.api      # dashboard at http://localhost:8080/dashboard.html
.venv/bin/python -m src.serve                        # both in one process (what Render runs)
.venv/bin/python -m pytest -q
```
Only one copy of a bot may run per token (stop the laptop bots before a hosted copy starts). `DEMO_SHOW_SELLER_CODE=1` shows the seller code to evaluators; set it to 0 and change SELLER_CODE after judging.

## Simulation date
"Today" = 2026-10-22 (`src/data_loader.TODAY`). Navratri 2026-10-11..19 (just ended), Diwali 2026-11-08 (gifting ramp).
