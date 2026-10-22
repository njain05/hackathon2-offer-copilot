# Offer Copilot

Seller-side personalised offer engine with a Telegram bot. See CLAUDE.md and docs/. (README is completed in slice v8.)

## Run the bot

1. **Create the bot**: in Telegram open **@BotFather** → `/newbot` → pick a name and a username ending in `bot`. Copy the token.
2. **Setup** (once, from the repo root):
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
   Fill `.env`: `TELEGRAM_BOT_TOKEN=<token>`, `SELLER_CODE=<a secret word only you know>`, optionally `ANTHROPIC_API_KEY=` for the AI agent. `.env` is git-ignored — never commit it.
3. **Start**: `.venv/bin/python -m src.bot` (keep it running; Ctrl+C stops it).

**Shoppers (classmates)** — `/start` gives you a synthetic customer · `/profile` your orders, cart, points · `/offer` a personal offer chosen for you · `/offers` live deals.

**Seller (you)** — `/seller <SELLER_CODE>` once, then `/campaigns` (proposes offers, each checked by `rules.yaml`, sent as cards with ✅ Allow / ❌ Reject) · `/pending` · `/live` · `/stats` · `/audit` (offers the rules lowered or blocked) · `/customer <name or id>`.

Allow re-checks the offer against the current `rules.yaml`, publishes it to `web/offers.json` and sends it to every classmate whose shopper it targets.

Note: `python demo.py` resets `data/state.json` (offers, shopper assignments, seller) — don't run it while the bot is live.
