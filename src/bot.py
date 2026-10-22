"""Telegram bot. Customer mode (default): /start /profile /offer /offers /help.  Seller mode: /seller /campaigns /pending /live /stats /audit /customer.

Run from repo root:  .venv/bin/python -m src.bot
Every offer shown or published here has passed rules.validate_offer (recommender does it; Allow re-checks).
"""
import asyncio
import logging
import os
import random
from html import escape

import pandas as pd
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from src import agent, messages, recommender, store
from src.data_loader import ROOT, TODAY, load_all
from src.rules import load_rules, validate_offer
from src.segments import segment_customers

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs request URLs, which contain the token
log = logging.getLogger("offer_copilot")

HTML = ParseMode.HTML
DATA = load_all()
SEGS = segment_customers(DATA)


def rules() -> dict:
    """Re-read rules.yaml on every use so marketing edits apply without a restart."""
    return load_rules()


# ---------- pure helpers (tested in tests/test_bot.py) ----------
def pick_shopper(taken: set[str], rng: random.Random = random.Random()) -> str:
    """A synthetic customer (opted in, not yet given to another classmate) for a new chat."""
    c = DATA["customers"]
    pool = c[c.marketing_opt_in & ~c.customer_id.isin(taken)]
    if pool.empty:
        pool = c[c.marketing_opt_in]
    return pool.customer_id.iloc[rng.randrange(len(pool))]


def find_customers(query: str) -> pd.DataFrame:
    """Match by exact id (C0042) or case-insensitive name fragment (Rohit)."""
    q = query.strip()
    c = DATA["customers"]
    if q.upper() in set(c.customer_id):
        return c[c.customer_id == q.upper()]
    return c[c.name.str.contains(q, case=False, regex=False)]


def is_live(offer: dict) -> bool:
    return offer["status"] == "approved" and offer["valid_to"] >= TODAY.isoformat()


def active_personal_offer(cid: str) -> dict | None:
    """Most recent live personal offer for this customer (re-shown instead of a new one during cooldown)."""
    mine = [o for o in store.get_offers("approved") if o["kind"] == "personal" and o["target"] == cid and is_live(o)]
    return mine[-1] if mine else None


def audience_chats(offer: dict) -> list[int]:
    """Classmate chats whose synthetic shopper is targeted by a public offer and opted in to marketing."""
    shoppers = store.all_shoppers()
    seg = SEGS.set_index("customer_id").segment
    opted = DATA["customers"].set_index("customer_id").marketing_opt_in
    out = []
    for chat_id, cid in shoppers.items():
        if not bool(opted.get(cid, False)):
            continue
        if offer["target"] in ("ALL", cid) or seg.get(cid) == offer["target"]:
            out.append(int(chat_id))
    return out


def no_offer_message(profile: dict) -> str:
    """What a shopper sees when no personal discount is given: a useful nudge, never the seller's reasoning."""
    lines = ["No personal discount today, but here's what's waiting for you:"]
    if profile["cart_items"]:
        n = profile["cart_items"]
        lines.append(f"🛒 {n} item{'s' if n > 1 else ''} still in your cart.")
    if profile["points_expiring"]:
        lines.append(f"⭐ {profile['points_expiring']} of your {profile['reward_points']} points expire soon. Use them on your next order.")
    if len(lines) == 1:
        lines = ["No personal discount today."]
    lines.append("\nSee deals for everyone with /offers.")
    return "\n".join(lines)


def decision_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Allow", callback_data=f"allow:{offer_id}"),
                                  InlineKeyboardButton("❌ Reject", callback_data=f"reject:{offer_id}")]])


def customer_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Use this offer", callback_data=f"use:{offer_id}"),
                                  InlineKeyboardButton("🙂 Maybe later", callback_data=f"later:{offer_id}")]])


def is_seller(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.id == store.get_seller()


# ---------- customer mode ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cid = store.get_shopper(chat_id)
    if cid is None:
        cid = pick_shopper(store.assigned_ids())
        store.assign_shopper(chat_id, cid)
        log.info("New shopper chat -> %s", cid)
    name = DATA["customers"].set_index("customer_id").loc[cid, "name"]
    await update.message.reply_text(
        f"🌰 Welcome to <b>Nishtha's Pantry</b>!\n\nYou're shopping as the synthetic customer <b>{escape(name)}</b>. "
        "Send /offer for a personal offer, /offers for live deals, /profile for your history.\n\n"
        "<i>Hackathon demo: all shoppers are synthetic.</i>", parse_mode=HTML)


async def _shopper_or_hint(update: Update) -> str | None:
    cid = store.get_shopper(update.effective_chat.id)
    if cid is None:
        await update.message.reply_text("Send /start first.")
    return cid


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if cid := await _shopper_or_hint(update):
        await update.message.reply_text(messages.profile_card(cid, DATA), parse_mode=HTML)


async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = await _shopper_or_hint(update)
    if cid is None:
        return
    existing = active_personal_offer(cid)
    if existing:
        await update.message.reply_text("You already have a personal offer waiting 👇")
        await update.message.reply_text(messages.customer_message(existing, DATA), parse_mode=HTML,
                                        reply_markup=customer_keyboard(existing["offer_id"]))
        return
    await update.message.reply_text("🔍 Looking at your history…")
    res = await asyncio.to_thread(recommender.offer_for_customer, DATA, SEGS, rules(), cid)
    if res["offer"] is None:
        log.info("No offer for %s: %s", cid, res["explanation"])  # seller-side reasoning stays out of the customer chat
        await update.message.reply_text(no_offer_message(res["profile"]), parse_mode=HTML)
        return
    oid = store.add_offer({**res["offer"], "status": "approved"})  # already validated inside offer_for_customer
    stored = store.get_offer(oid)
    await update.message.reply_text(messages.customer_message(stored, DATA), parse_mode=HTML, reply_markup=customer_keyboard(oid))


async def offers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    live = [o for o in store.get_offers("approved") if o["kind"] == "public" and is_live(o)]
    if not live:
        await update.message.reply_text("No live deals right now. Check back soon!")
        return
    for o in live:
        await update.message.reply_text(messages.customer_message(o, DATA), parse_mode=HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = ("<b>Shopper commands</b>\n/start · your synthetic shopper\n/profile · your orders, cart and points\n"
            "/offer · a personal offer picked for you\n/offers · live deals for everyone\n")
    if is_seller(update):
        text += ("\n<b>Seller commands</b>\n/campaigns · analyse and propose offers\n/pending · offers waiting for you\n"
                 "/live · offers customers can see\n/stats · results\n/audit · offers the rules changed or blocked\n"
                 "/customer &lt;name or id&gt; · suggest an offer for one customer")
    else:
        text += "\nSeller? /seller &lt;code&gt;"
    await update.message.reply_text(text, parse_mode=HTML)


# ---------- seller mode ----------
async def seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = os.getenv("SELLER_CODE", "")
    current = store.get_seller()
    chat_id = update.effective_chat.id
    if current is not None and current != chat_id:
        await update.message.reply_text("Seller mode is already claimed.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /seller <code>")
        return
    if not code or context.args[0] != code:
        await update.message.reply_text("Wrong seller code.")
        return
    store.set_seller(chat_id)
    log.info("Seller chat registered")
    await update.message.reply_text(
        "🧑‍💼 Seller mode on.\n/campaigns · /pending · /live · /stats · /audit · /customer &lt;name or id&gt;", parse_mode=HTML)


async def _seller_only(update: Update) -> bool:
    if not is_seller(update):
        await update.message.reply_text("Seller only. Use /seller <code> first.")
        return False
    return True


async def _send_cards(update: Update, pending: list[dict]) -> None:
    for o in pending:
        await update.message.reply_text(messages.seller_card(o, DATA), parse_mode=HTML, reply_markup=decision_keyboard(o["offer_id"]))


async def campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    await update.message.reply_text("🔍 Analysing sales, stock and festivals…")
    r = rules()
    ai = await asyncio.to_thread(recommender.ai_campaigns, DATA, SEGS, r) if agent.available() else None
    cands = ai or recommender.generate_candidates(DATA, SEGS, r)
    res = recommender.run_pipeline(DATA, r, cands)
    src = "🤖 AI agent" if ai else "📏 rule engine"
    await update.message.reply_text(
        f"{src}: {len(res['stored'])} ready · {len(res['adjusted'])} adjusted by rules · {len(res['blocked'])} blocked\n"
        "Tap Allow to publish, Reject to drop. /audit shows what the rules changed.")
    new_ids = set(res["stored"] + res["adjusted"])
    await _send_cards(update, [o for o in store.get_offers("pending") if o["offer_id"] in new_ids])


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    p = store.get_offers("pending")
    if not p:
        await update.message.reply_text("Nothing pending. Try /campaigns.")
        return
    await _send_cards(update, p)


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    items = [o for o in store.get_offers("approved") if is_live(o)]
    if not items:
        await update.message.reply_text("Nothing live yet.")
        return
    lines = [f"• <b>{escape(o['title'])}</b> · {o['discount_pct']}% · {escape(o['target'])} · <code>{escape(o['code'])}</code> · until {o['valid_to']}"
             for o in items]
    await update.message.reply_text("🟢 <b>Live offers</b>\n" + "\n".join(lines), parse_mode=HTML)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    allo = store.get_offers()
    n = lambda s: sum(o["status"] == s for o in allo)
    await update.message.reply_text(
        f"📊 <b>Offer results</b>\n✅ Approved {n('approved')}\n⏳ Pending {n('pending')}\n❌ Rejected {n('rejected')}\n"
        f"🛡 Blocked by rules {n('blocked')}\n🛒 Used by shoppers {sum(bool(o.get('used')) for o in allo)}\n\n"
        f"Shoppers on the bot: {len(store.all_shoppers())}", parse_mode=HTML)


async def audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    evs = store.read_audit(5)
    if not evs:
        await update.message.reply_text("🛡 Guardrail log: nothing yet")
        return
    lines = []
    for e in evs:
        who = escape(str(e.get("title") or e.get("customer")))
        result = f"→ {e['final']}%" if e["kind"] == "adjusted" else "→ BLOCKED"
        lines.append(f"• <b>{who}</b> ({e.get('source')}): proposed {e.get('proposed', '?')}% {result}\n  <i>{escape(e['reasons'][0])}</i>")
    await update.message.reply_text("🛡 <b>Guardrail log</b>\n" + "\n".join(lines), parse_mode=HTML)


async def customer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _seller_only(update):
        return
    q = " ".join(context.args)
    if not q:
        await update.message.reply_text("Usage: /customer Rohit  or  /customer C0042")
        return
    m = find_customers(q)
    if m.empty:
        await update.message.reply_text("No match.")
        return
    if len(m) > 1:
        lines = "\n".join(f"• {r.customer_id} {escape(r.name)} ({r.city})" for r in m.head(8).itertuples())
        await update.message.reply_text(f"Several matches:\n{lines}\n\nSend /customer &lt;id&gt;", parse_mode=HTML)
        return
    cid = m.customer_id.iloc[0]
    res = await asyncio.to_thread(recommender.offer_for_customer, DATA, SEGS, rules(), cid)
    p = res["profile"]
    text = (messages.profile_card(cid, DATA) +
            f"\n\n🧩 <b>Segment:</b> {p['segment']} · {escape(p['segment_reason'])}\n"
            f"🏷 Discount habit: {int(p['discount_order_share'] * 100)}% of orders · returns {int(p['return_rate'] * 100)}%\n")
    if res["offer"] is None:
        await update.message.reply_text(text + f"\n💡 <b>Suggested:</b> no discount\n<i>{escape(res['explanation'])}</i>", parse_mode=HTML)
        return
    oid = store.add_offer({**res["offer"], "source": res["offer"].get("source", "rule_engine")})
    o = store.get_offer(oid)
    notes = "".join(f"\n⚖️ {escape(x)}" for x in res["notes"])
    await update.message.reply_text(
        text + f"\n💡 <b>Suggested:</b> {o['discount_pct']}% off · code <code>{escape(o['code'])}</code>{notes}\n"
        f"<i>{escape(res['explanation'])}</i>", parse_mode=HTML, reply_markup=decision_keyboard(oid))


# ---------- buttons ----------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    act, _, oid = (q.data or "").partition(":")
    o = store.get_offer(oid)
    if o is None:
        await q.answer("Offer not found", show_alert=True)
        return
    if act == "use":
        store.update_offer(oid, used=True)
        await q.answer()
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(f"🛒 Code <code>{escape(o['code'])}</code> applied to your cart. Happy shopping!", parse_mode=HTML)
        return
    if act == "later":
        await q.answer()
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("No problem, maybe later. 🙂")
        return
    if update.effective_chat.id != store.get_seller():
        await q.answer("Seller only", show_alert=True)
        return
    if o["status"] != "pending":
        await q.answer(f"Already {o['status']}", show_alert=True)
        return
    await q.answer()
    if act == "reject":
        store.set_status(oid, "rejected")
        await q.edit_message_text(q.message.text_html + "\n\n❌ <b>Rejected</b>", parse_mode=HTML)
        return
    if act != "allow":
        return
    hist = store.history_for(o["target"]) if o["kind"] == "personal" else []
    v = validate_offer(o, DATA, rules(), hist)  # rules may have changed since the card was made
    if not v.ok:
        store.set_status(oid, "blocked", v.reasons)
        store.log_event("blocked", {"source": o["source"], "title": o["title"], "proposed": o["discount_pct"], "reasons": v.reasons})
        reasons = "\n".join(f"• {escape(r)}" for r in v.reasons)
        await q.edit_message_text(q.message.text_html + f"\n\n🛡 <b>Blocked by rules:</b>\n{reasons}", parse_mode=HTML)
        return
    store.set_status(oid, "approved")
    sent = await _deliver(context, store.get_offer(oid))
    await q.edit_message_text(q.message.text_html + f"\n\n✅ <b>LIVE</b>: now visible to shoppers · sent to {sent} chat(s)", parse_mode=HTML)


async def _deliver(context: ContextTypes.DEFAULT_TYPE, o: dict) -> int:
    """Push an approved offer to every classmate whose shopper it targets. Returns chats reached."""
    sent = 0
    for chat_id in audience_chats(o):
        try:
            await context.bot.send_message(chat_id, "🎉 New offer for you!\n\n" + messages.customer_message(o, DATA),
                                           parse_mode=HTML, reply_markup=customer_keyboard(o["offer_id"]))
            sent += 1
        except Exception as e:  # user blocked the bot, etc.
            log.warning("Could not deliver %s to a chat: %s", o["offer_id"], type(e).__name__)
    return sent


async def other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("I only understand commands. Try /offer, /offers or /profile (or /help).")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Handler error: %r", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    for name, fn in [("start", start), ("profile", profile), ("offer", offer), ("offers", offers), ("help", help_cmd),
                     ("seller", seller), ("campaigns", campaigns), ("pending", pending), ("live", live),
                     ("stats", stats), ("audit", audit), ("customer", customer)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, other))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    load_dotenv(ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (copy .env.example to .env).")
    store.export_public_offers()
    log.info("Offer Copilot running (AI agent %s). Ctrl+C to stop.", "on" if agent.available() else "off: rule engine")
    build_app(token).run_polling()


if __name__ == "__main__":
    main()
