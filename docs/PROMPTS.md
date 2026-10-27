# PROMPTS — status (27 Oct 2026)

P1 bot ✅ · P2 real catalogue: not done (needs the owner's numbers; the synthetic catalogue stays) · P3 AI path ✅ · P4 offers page ✅ · P5 rules usability ✅ · P6 results and polish ✅.
Built beyond the plan: two bots, plain-language seller chat, first-time welcome offers, group/top-N targeting, the sales dashboard with Ask Copilot, private seller access, deployment files. Details in docs/PROGRESS.md.

---

# PROMPTS — one per chat, in order. The MVP already exists (see docs/PROGRESS.md).

Every prompt starts with the same hand-off so no chat works blind. Finish, test, commit, update PROGRESS.md, then open a NEW chat.

Hand-off line (start of every chat):
"Read CLAUDE.md, docs/CONTRACTS.md, docs/PROGRESS.md and list src/. Summarise in 5 lines what exists and what you'll touch. Do ONLY the slice below. Don't change a CONTRACTS signature unless required; if so, update the doc and say why."

## P1 — Run and fix the bot
Slice: make the existing src/bot.py work on real Telegram.
Create .venv, pip install -r requirements.txt, copy .env.example to .env (I will fill TELEGRAM_BOT_TOKEN, SELLER_CODE). Run `python demo.py` then `python -m src.bot`. Test on my phone: /start shows a synthetic shopper, /offer returns a rule-checked offer, /seller <code> then /campaigns shows cards with Allow/Reject, Allow publishes to web/offers.json. Fix any bug found. Run pytest. Commit "bot works".

## P2 — Make the data mine (real dry-fruit business realism)
Slice: scripts/generate_data.py only.
I own a real dry-fruit business. Replace the product list in scripts/generate_data.py with my real catalogue (names, selling price, cost price, typical stock, which items spike for which festival) and my real discount habits. NEVER use real customer data; customers stay synthetic. Keep column names identical (CONTRACTS). Regenerate the Excel, rerun demo.py and pytest, show me before/after of the top-selling categories. Commit "real catalogue".

## P3 — Claude as the strategist (verify the AI path)
Slice: src/agent.py + recommender.ai_campaigns.
ANTHROPIC_API_KEY is in .env. Run campaigns and per-customer offers with the AI on. Check: JSON parsing is robust, bad/malformed output is dropped, every AI offer goes through validate_offer, adjusted/blocked ones land in data/audit.jsonl. Add tests with a mocked LLM returning a 60% offer (must be adjusted/blocked) and malformed JSON (must fall back to rules). Improve the system prompt using my business rules. Commit "ai agent verified".

## P4 — Offers page (can run parallel with P1)
Slice: web/offers.html (+ web/style.css) only.
Mobile-friendly "Offers" section for my dry-fruit brand: cards with title, discount badge, product names, code with copy button, "ends in N days", festive ribbon. Fetch offers.json (approved public offers); friendly empty state. No frameworks. Add `python -m http.server -d web 8000` to README. Commit "offers page".

## P5 — Rules usability (marketing person can edit)
Slice: rules.yaml + src/rules.py reasons.
Make rules.yaml self-explanatory with comments and examples. Add a `python -m src.rules --check` command that prints every rule, its current value and a plain-English meaning, and a friendly error if rules.yaml is invalid. Add tests for edge cases (exactly at limit, cooldown, monthly cap, opt-out, low stock). Commit "rules usable".

## P6 — Results and polish
Slice: /stats, messages, README.
/stats: approved/rejected/blocked counts, estimated discount spend in ₹, simulated uplift from events/orders, and a "guardrail saves" line (offers adjusted or blocked). Improve seller card + customer message design. Edge cases: expired offers, stock gone after approval, duplicate offers. README: what it is, architecture (text diagram), setup, how marketing edits rules.yaml, how guardrails work, limitations. Update CLAUDE.md. Scan for secrets, confirm .env ignored. Commit "polish".
