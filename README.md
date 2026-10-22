# Offer Copilot

Seller-side personalised offer engine with a Telegram bot. See CLAUDE.md and docs/. (README is completed in slice v8.)

## Run the bot

Two Telegram bots, one program: a **customer bot** (classmates use it; this is the username to share and submit) and a **seller bot** (only you). Both read and write the same `data/state.json`, so an offer you Allow in the seller bot is sent to customers by the customer bot.

1. **Create the bots**: in Telegram open **@BotFather** → `/newbot` twice (e.g. `nishthas_pantry_bot` for customers, `nishthas_pantry_seller_bot` for you). Copy both tokens.
2. **Setup** (once, from the repo root):
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
   Fill `.env`: `TELEGRAM_BOT_TOKEN` (customer bot), `SELLER_BOT_TOKEN` (seller bot), `SELLER_CODE` (a secret word), optionally `ANTHROPIC_API_KEY` for the AI agent. `.env` is git-ignored — never commit it. Leave `SELLER_BOT_TOKEN` empty to run a single bot that does both jobs.
3. **Start**: `.venv/bin/python -m src.bot` (keep it running; Ctrl+C stops both bots).

**Customer bot** — `/start` gives you a synthetic customer · `/profile` your orders, cart, points · `/offer` a personal offer chosen for you · `/offers` live deals.

**Seller bot** — private: nothing works until someone sends `/seller <SELLER_CODE>`. Give the code only to your team; anyone with it can unlock seller mode, nobody else can. The bot deletes the message with the code, locks a chat for 10 minutes after 5 wrong codes, and `/logout` removes access. Then `/campaigns` (proposes offers, each checked by `rules.yaml`, sent as cards with ✅ Allow / ❌ Reject) · `/pending` · `/live` · `/stats` · `/audit` (offers the rules lowered or blocked) · `/customer <name or id>`.

**Talk to the seller bot in plain English** (needs `ANTHROPIC_API_KEY`). Examples:
- "What should I offer Rohit?" → his profile and a suggested offer with Allow / Reject
- "Make a Diwali deal: 15% off almonds and cashews for everyone till 8 Nov" → a draft, rule-checked (Nuts are capped at 12%, so it is lowered and you are told why), with Allow / Reject
- "Can I give 25% on gift boxes?" → an answer from the rules
- "Raise the Makhana limit to 18%" → shows the change, updates `rules.yaml` only after you tap Confirm (unsafe values are refused)
- "Stop the Navratri deal" / "How are my offers doing?"

Nothing goes live from a chat message alone: drafts need Allow, rule changes and ending an offer need Confirm.

Allow re-checks the offer against the current `rules.yaml`, publishes it to `web/offers.json` and sends it, through the customer bot, to every customer it targets. You can use both bots from your own Telegram account to see both sides.

## Offers page

`web/offers.html` is the public storefront page. It shows every offer the seller has approved (from `web/offers.json`, written by the bot on Allow), with product prices before and after the discount, a copyable code and a countdown. It refreshes itself every minute.

```bash
python -m http.server -d web 8000
```
Open http://localhost:8000/offers.html. Before anything is approved, open http://localhost:8000/offers.html?sample to see it with example offers (`web/offers.sample.json`).

## Dashboard (for the sales and marketing team)

```bash
.venv/bin/python -m src.api
```
Open http://localhost:8000/dashboard.html and enter your `SELLER_CODE` (the same private code as the seller bot).

**Ask Copilot** (bottom-right, needs `ANTHROPIC_API_KEY`): chat with the assistant from the dashboard: "What should I offer Harpreet?", "15% off almonds for lapsed customers in Ludhiana", "Can I give 25% on gift boxes?", "Propose Diwali campaigns", "Raise the Makhana limit to 18%". Drafts appear as cards with Approve / Reject; rule changes and ending an offer ask you to Confirm. It remembers the last few messages, so "make it 10% instead" works. The same server also serves the store's offers page at /offers.html.

What the team can see: revenue and orders, customers going quiet and the spend at stake, open carts, the festival effect (Navratri, Rakhi, Diwali countdown), customer groups and what the engine does for each, category stock cover and discount limits, every offer and its status, the guardrail log, and the rules in force.

What the team can do:
- **Create an offer**: pick who (customer group, city, favourite category, first-time customers, open carts; the matching count updates live, with opted-out and recently-offered customers left out by the rules), products, discount and days. Or describe it in plain words ("12% off nuts for lapsed customers in Ludhiana") and Claude fills the form. **Check against rules** shows what would pass, be lowered or be blocked, and exactly what the customer will read.
- **Find a customer**: profile plus a suggested personal offer (or "no discount", e.g. for top customers).
- **Approve / Reject / End** offers. Approve re-checks the rules, then sends the offer through the customer bot to the customers it targets (group offers only reach their group).
- **Edit the rules**: change a number, press Save, read the change in words, Confirm. Unsafe values are refused.

Without the server (`python -m http.server -d web 8000`) the dashboard is read-only from `web/dashboard.json`, which the bot keeps up to date.
