# CLAUDE.md — Offer Copilot (Hackathon 2)

READ THIS FIRST in every session. Then read `docs/CONTRACTS.md` and `docs/PROGRESS.md`.

## What we are building
A **seller-side offer copilot** for a fictional Indian dry-fruit e-commerce seller (dry fruits, nuts, makhana, plus fasting items for festivals).
- Analyses synthetic business data and festival calendar.
- Proposes offers (rule-based first, AI agent later).
- **Every offer is validated by the rules layer before anything is sent.** AI proposes, rules decide, seller has the final say.
- A Telegram bot sends the seller offer cards with [Allow] [Reject] buttons. Allow => offer goes live on the offers page (`web/offers.html`).
- Two-way chat: seller asks "what offer for <customer>?" and gets a reasoned, rule-checked suggestion.

Brief: `docs/Hackathon_2_Brief.pdf`. Judged on: core functionality (offers stay within rules + reach classmates via bot), data realism, usability of rules for a marketing person, message/result design, advanced cases, business relevance.
Deadline: Tue 27 Oct 2026, 12:00. Submit: GitHub repo (source + README + this CLAUDE.md) + Telegram bot username (bot kept running).

## Hard rules
1. **Synthetic data only.** Never real personal data.
2. **No secrets in Git.** Token/keys live in `.env` (see `.env.example`). Never print or commit them.
3. **Rules are the single source of truth for limits** — `rules.yaml`, validated ONLY via `src/rules.py::validate_offer`. No other module may hardcode a discount limit.
4. **Never send or publish an offer that has not passed `validate_offer`.** AI output is untrusted input.
5. **Respect the contracts** in `docs/CONTRACTS.md` (file names, function signatures, schemas). If you must change one, update CONTRACTS.md in the same commit and note it in PROGRESS.md.
6. Build **one feature slice at a time**. Each slice must run on its own, have a tiny test, and be committed (`git commit -m "vN: ..."`). Do not refactor other slices.
7. Keep code simple, typed, small functions. Python 3.11+, deps: pandas, openpyxl, pyyaml, python-telegram-bot (v21+, async), anthropic.
8. Marketing-person usability: rules readable, comments in `rules.yaml`, messages in plain English (₹, short, clear).

## Layout
```
data/dryfruit_synthetic_data.xlsx   synthetic data (sheets: customers, products, orders, order_items, events, rules)
data/state.json                    runtime offer store (git-ignored)
rules.yaml                         business limits (edited by marketing)
scripts/generate_data.py           regenerates the Excel (run from repo root)
src/                               data_loader, segments, rules, store, recommender, messages, agent, bot, chat
web/offers.html                    public offers page (reads data/state.json via generated web/offers.json)
tests/                             pytest, one file per slice
docs/                              CONTRACTS, PROGRESS, PROMPTS, Hackathon_2_Brief.pdf
```

## Workflow per session
1. Read CLAUDE.md, CONTRACTS.md, PROGRESS.md.
2. Do only the slice you are asked to do.
3. Run its test. Commit.
4. Update `docs/PROGRESS.md` (what was done, what the next slice can rely on, any deviations).

## Current date in the simulation
"Today" = 2026-10-22. Navratri 2026-10-11..19 (just ended), Diwali ~2026-11-08 (upcoming; gifting + dry fruits ramp). Use `src/data_loader.TODAY`.
