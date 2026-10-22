"""Telegram bots. Customer bot: /start /profile /offer /offers /help.  Seller bot: /seller /campaigns /pending /live /stats /audit /customer.

Two-bot mode (SELLER_BOT_TOKEN set): customers use the TELEGRAM_BOT_TOKEN bot, the seller uses the SELLER_BOT_TOKEN bot;
both run in one process and share data/state.json. Without SELLER_BOT_TOKEN one bot does both jobs.
Run from repo root:  .venv/bin/python -m src.bot
Every offer shown or published here has passed rules.validate_offer (recommender does it; Allow re-checks).
"""
import asyncio
import hmac
import logging
import os
import random
import time
from html import escape

import pandas as pd
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import re
import secrets

from src import agent, chat, messages, recommender, store
from src.data_loader import ROOT, TODAY, load_all
from src.rules import apply_change, describe_change, load_rules, validate_offer
from src.segments import segment_customers

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs request URLs, which contain the token
log = logging.getLogger("offer_copilot")

HTML = ParseMode.HTML
TWO_BOTS = False      # set in main(): separate seller bot
CUSTOMER_BOT = None   # the bot that messages shoppers (used when the seller bot approves an offer)
PENDING_RULES: dict[str, tuple[str, int, str]] = {}  # confirm-button token -> (rule, value, category)
DATA = load_all()
SEGS = segment_customers(DATA)


def rules() -> dict:
    """Re-read rules.yaml on every use so marketing edits apply without a restart."""
    return load_rules()


# ---------- pure helpers (tested in tests/test_bot.py) ----------
def pick_shopper(taken: set[str], rng: random.Random = random.Random(), first_time: bool | None = None) -> str:
    """A synthetic customer (opted in, not yet given to another classmate) for a new chat.
    first_time=True: someone who has never ordered; False: someone with order history; None: anyone."""
    c = DATA["customers"]
    c = c[c.marketing_opt_in]
    if first_time is not None:
        c = c[(c.orders_total == 0) == first_time]
    pool = c[~c.customer_id.isin(taken)]
    if pool.empty:
        pool = c
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
    sellers = store.get_sellers()
    seg = SEGS.set_index("customer_id").segment
    opted = DATA["customers"].set_index("customer_id").marketing_opt_in
    out = []
    for chat_id, cid in shoppers.items():
        if (int(chat_id) in sellers and not TWO_BOTS) or not bool(opted.get(cid, False)):
            continue
        if offer["target"] in ("ALL", cid) or seg.get(cid) == offer["target"] or cid in (offer.get("customer_ids") or []):
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


def approval_history(o: dict) -> list[dict]:
    """History the rules need when (re)checking an offer before it goes live."""
    if o["kind"] == "personal":
        return store.history_for(o["target"])
    if o["target"] == "GROUP":
        return [h for h in store.group_history() if h["offer_id"] != o["offer_id"]]
    return []


def decision_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Allow", callback_data=f"allow:{offer_id}"),
                                  InlineKeyboardButton("❌ Reject", callback_data=f"reject:{offer_id}")]])


def customer_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Use this offer", callback_data=f"use:{offer_id}"),
                                  InlineKeyboardButton("🙂 Maybe later", callback_data=f"later:{offer_id}")]])


def is_seller(update: Update) -> bool:
    return update.effective_chat is not None and store.is_seller_chat(update.effective_chat.id)


# ---------- customer mode ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if is_seller(update) and not TWO_BOTS:
        await update.message.reply_text("🧑‍💼 This chat is the seller. Shopper features are for your customers. Send /help for seller commands.")
        return
    cid = store.get_shopper(chat_id)
    if cid is None:
        await update.message.reply_text(
            "🌰 Welcome to <b>Nishtha's Pantry</b>! Dry fruits, nuts and makhana, delivered.\n\nHave you shopped with us before?",
            parse_mode=HTML, reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🆕 I'm new here", callback_data="who:new"),
                InlineKeyboardButton("🛍 I've shopped before", callback_data="who:returning")]]))
        return
    await update.message.reply_text(welcome_text(cid), parse_mode=HTML, reply_markup=MENU)


def welcome_text(cid: str) -> str:
    c = DATA["customers"].set_index("customer_id").loc[cid]
    if c.orders_total == 0:
        intro = (f"Welcome, <b>{escape(c['name'])}</b>! This is your first time here, so your first order has a welcome treat. "
                 "Send /offer to see it, or /offers for deals everyone can use.")
    else:
        intro = (f"Welcome back, <b>{escape(c['name'])}</b>! Send /offer for a personal offer, /offers for live deals, "
                 "/profile for your order history.")
    return intro + "\n\n<i>Hackathon demo: you're playing a synthetic customer.</i>"


async def choose_identity(update: Update, kind: str) -> None:
    """Button under /start: become a first-time or a returning synthetic customer."""
    q = update.callback_query
    chat_id = update.effective_chat.id
    await q.answer()
    cid = store.get_shopper(chat_id)
    if cid is None:
        cid = pick_shopper(store.assigned_ids(), first_time=(kind == "new"))
        store.assign_shopper(chat_id, cid)
        log.info("New shopper chat -> %s (%s)", cid, kind)
    await q.edit_message_reply_markup(None)
    await q.message.reply_text(welcome_text(cid), parse_mode=HTML, reply_markup=MENU)


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


SHOPPER_HELP = ("<b>Shopper commands</b>\n/start · your synthetic shopper\n/profile · your orders, cart and points\n"
                "/offer · a personal offer picked for you\n/offers · live deals for everyone\n")
SELLER_HELP = ("<b>Seller commands</b>\n/campaigns · analyse and propose offers\n/pending · offers waiting for you\n"
               "/live · offers customers can see\n/stats · results\n/audit · offers the rules changed or blocked\n"
               "/customer &lt;name or id&gt; · suggest an offer for one customer\n/logout · leave seller mode")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single-bot mode: shopper help, plus seller help for the seller."""
    text = SHOPPER_HELP + ("\n" + SELLER_HELP if is_seller(update) else "\nSeller? /seller &lt;code&gt;")
    await update.message.reply_text(text, parse_mode=HTML)


async def help_shopper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(SHOPPER_HELP, parse_mode=HTML)


async def help_seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = SELLER_HELP if is_seller(update) else "🧑‍💼 Seller console for Nishtha's Pantry. Send /seller &lt;code&gt; to unlock it."
    await update.message.reply_text(text, parse_mode=HTML)


# ---------- seller mode ----------
FAILED_TRIES: dict[int, list[float]] = {}  # chat id -> times of recent wrong codes
MAX_TRIES, LOCK_SECONDS = 5, 600


async def seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/seller <code>: anyone the owner gave the code to can unlock seller mode; nobody else."""
    code = os.getenv("SELLER_CODE", "")
    chat_id = update.effective_chat.id
    if context.args:
        try:
            await update.message.delete()  # don't leave the code sitting in the chat
        except Exception:
            pass
    if store.is_seller_chat(chat_id):
        await update.effective_chat.send_message("🧑‍💼 You're already in seller mode. /help shows the commands, /logout leaves.")
        return
    now = time.time()
    tries = [t for t in FAILED_TRIES.get(chat_id, []) if now - t < LOCK_SECONDS]
    if len(tries) >= MAX_TRIES:
        await update.effective_chat.send_message("🔒 Too many wrong codes. Try again in 10 minutes.")
        return
    if not context.args:
        await update.effective_chat.send_message("🔒 The seller console is private. Send /seller followed by the code the owner gave you.")
        return
    if not code or not hmac.compare_digest(context.args[0].encode(), code.encode()):
        FAILED_TRIES[chat_id] = tries + [now]
        log.warning("Wrong seller code from a chat (%d recent tries)", len(tries) + 1)
        await update.effective_chat.send_message(f"Wrong seller code. {MAX_TRIES - len(tries) - 1} tries left before a 10-minute lock.")
        return
    FAILED_TRIES.pop(chat_id, None)
    store.set_seller(chat_id)
    log.info("Seller chat registered")
    if TWO_BOTS:
        await update.effective_chat.send_message("🧑‍💼 Seller mode on. (I deleted your message so the code isn't left in the chat.)\n\n" + SELLER_HELP, parse_mode=HTML)
        return
    store.remove_shopper(chat_id)  # single bot: the seller's chat is not a customer, so no customer offers here
    await update.effective_chat.send_message(
        "🧑‍💼 Seller mode on. This chat now only gets seller cards, not customer offers.\n"
        "/campaigns · /pending · /live · /stats · /audit · /customer &lt;name or id&gt;\n\n"
        "<i>To see the customer side, open the bot from another Telegram account or ask a classmate to /start it.</i>", parse_mode=HTML)


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store.remove_seller(update.effective_chat.id)
    await update.message.reply_text("🔒 Logged out of seller mode. Send /seller <code> to come back.")


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
        f"{src}: {len(res['stored'])} ready · {len(res['adjusted'])} adjusted by rules · {len(res['blocked'])} blocked"
        + (f" · {len(res['skipped'])} already pending or live" if res["skipped"] else "") + "\n"
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
    await customer_report(update, q)


async def customer_report(update: Update, q: str) -> None:
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
    if act == "who":
        await choose_identity(update, oid)
        return
    if act in ("ruleok", "endok", "cancel"):
        await confirm_button(update, act, oid)
        return
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
    if not store.is_seller_chat(update.effective_chat.id):
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
    v = validate_offer(o, DATA, rules(), approval_history(o))  # rules may have changed since the card was made
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
    bot = CUSTOMER_BOT or context.bot  # two-bot mode: customers only ever hear from the customer bot
    for chat_id in audience_chats(o):
        try:
            await bot.send_message(chat_id, "🎉 New offer for you!\n\n" + messages.customer_message(o, DATA),
                                           parse_mode=HTML, reply_markup=customer_keyboard(o["offer_id"]))
            sent += 1
        except Exception as e:  # user blocked the bot, etc.
            log.warning("Could not deliver %s to a chat: %s", o["offer_id"], type(e).__name__)
    return sent


MENU_OFFER, MENU_DEALS, MENU_PROFILE, MENU_HELP = "🎁 My offer", "🔥 Today's deals", "👤 My profile", "❓ Help"
MENU = ReplyKeyboardMarkup([[MENU_OFFER, MENU_DEALS], [MENU_PROFILE, MENU_HELP]], resize_keyboard=True, is_persistent=True,
                           input_field_placeholder="Tap a button below")


async def other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shopper typed text: the menu buttons send their label as text."""
    action = {MENU_OFFER: offer, MENU_DEALS: offers, MENU_PROFILE: profile, MENU_HELP: help_shopper}.get((update.message.text or "").strip())
    if action:
        await action(update, context)
        return
    await update.message.reply_text("Tap a button below to see your offer, today's deals or your profile.", reply_markup=MENU)



async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Handler error: %r", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass


SHOPPER_COMMANDS = [("start", start), ("profile", profile), ("offer", offer), ("offers", offers)]
SELLER_COMMANDS = [("seller", seller), ("logout", logout), ("campaigns", campaigns), ("pending", pending), ("live", live),
                   ("stats", stats), ("audit", audit), ("customer", customer)]


SHOPPER_MENU = [BotCommand("start", "Start shopping"), BotCommand("offer", "My personal offer"),
                BotCommand("offers", "Today's deals"), BotCommand("profile", "My orders and points"), BotCommand("help", "Help")]
SELLER_MENU = [BotCommand("campaigns", "Propose offers"), BotCommand("pending", "Offers waiting for you"), BotCommand("live", "Live offers"),
               BotCommand("stats", "Results"), BotCommand("audit", "What the rules changed"), BotCommand("customer", "Offer for one customer"),
               BotCommand("seller", "Unlock seller mode"), BotCommand("logout", "Leave seller mode"), BotCommand("help", "Help")]


def _app(token: str, commands: list, fallback, menu: list | None = None) -> Application:
    async def post_init(app: Application) -> None:
        if menu:
            try:
                await app.bot.set_my_commands(menu)  # Telegram's "/" menu button
            except Exception as e:
                log.warning("Could not set the command menu: %s", type(e).__name__)
    app = Application.builder().token(token).post_init(post_init).build()
    for name, fn in commands:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    app.add_error_handler(on_error)
    return app


def build_app(token: str) -> Application:
    """Single bot doing both jobs."""
    return _app(token, SHOPPER_COMMANDS + SELLER_COMMANDS + [("help", help_cmd)], seller_text, SHOPPER_MENU)


def build_customer_app(token: str) -> Application:
    return _app(token, SHOPPER_COMMANDS + [("help", help_shopper)], other, SHOPPER_MENU)


def build_seller_app(token: str) -> Application:
    return _app(token, SELLER_COMMANDS + [("start", help_seller), ("help", help_seller)], seller_text, SELLER_MENU)


async def _run_all(apps: list[Application]) -> None:
    """Poll several bots in one event loop until Ctrl+C."""
    for app in apps:
        await app.initialize()
        if app.post_init:
            await app.post_init(app)
        await app.start()
        await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        for app in apps:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def main() -> None:
    global TWO_BOTS, CUSTOMER_BOT
    load_dotenv(ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    seller_token = os.getenv("SELLER_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (copy .env.example to .env).")
    store.export_public_offers()
    from src import dashboard
    store.on_change(lambda: dashboard.export(DATA, SEGS))  # dashboard.json follows every offer change
    dashboard.export(DATA, SEGS)
    ai = "on" if agent.available() else "off: rule engine"
    if not seller_token:
        log.info("Offer Copilot running, one bot (AI agent %s). Ctrl+C to stop.", ai)
        build_app(token).run_polling()
        return
    TWO_BOTS = True
    customer_app, seller_app = build_customer_app(token), build_seller_app(seller_token)
    CUSTOMER_BOT = customer_app.bot
    log.info("Offer Copilot running, customer bot + seller bot (AI agent %s). Ctrl+C to stop.", ai)
    try:
        asyncio.run(_run_all([customer_app, seller_app]))
    except KeyboardInterrupt:
        pass



# ---------- seller chat in plain language ----------
def md_to_html(text: str) -> str:
    """Claude may answer with **bold**; Telegram HTML wants <b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text, quote=False))


def people_text(title: str, rows: list[dict]) -> str:
    lines = [f"{i}. <b>{escape(r['name'])}</b> ({escape(r['city'])}, {r['customer_id']}) · {escape(r['group'])} · {r['orders']} orders · ₹{r['spend']:,}"
             + ("" if r["opted_in"] else " · opted out") for i, r in enumerate(rows, 1)]
    return f"👥 {escape(title)}\n" + "\n".join(lines) if rows else "No customers match."


def confirm_keyboard(act: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Confirm", callback_data=f"{act}:{token}"),
                                  InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel:{token}")]])


async def seller_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain-language message from the seller: Claude answers, or picks one action that we carry out here."""
    if not is_seller(update):
        if TWO_BOTS:
            await update.message.reply_text("🧑‍💼 Seller console. Send /seller <code> to unlock it.")
        else:
            await other(update, context)
        return
    await update.effective_chat.send_action("typing")
    res = await asyncio.to_thread(chat.interpret, update.message.text, DATA, SEGS, rules())
    if res is None:
        await update.message.reply_text("I can't read free text right now (AI unavailable). Send /help to see the commands.")
        return
    if "reply" in res:
        await update.message.reply_text(md_to_html(res["reply"]), parse_mode=HTML)
        return
    await run_seller_action(update, context, res["tool"], res["input"])


async def run_seller_action(update: Update, context: ContextTypes.DEFAULT_TYPE, tool: str, args: dict) -> None:
    log.info("Seller chat action: %s", tool)
    if tool == "suggest_for_customer":
        await customer_report(update, args.get("query", ""))
    elif tool == "draft_offer":
        await draft_from_chat(update, args)
    elif tool == "draft_group_offer":
        f = {k: args.get(k) for k in ("segments", "cities", "categories", "first_time_only", "with_cart", "rank_by", "limit")}
        o, sel = recommender.group_offer(DATA, SEGS, rules(), f, args.get("product_ids"), args.get("discount_pct", 0), args.get("valid_days", 7),
                                         args.get("title", ""), args.get("message", ""), args.get("rationale", ""), "seller_chat")
        await _pipeline_reply(update, o)
    elif tool == "list_customers":
        from src import audience
        f = {k: args.get(k) for k in ("segments", "cities", "categories", "first_time_only", "with_cart", "rank_by", "limit")}
        res = audience.find(DATA, SEGS, f, limit=min(int(f.get("limit") or 10), 25))
        await update.message.reply_text(people_text(f"{res['label']}: {res['total']} customer(s), top {len(res['customers'])} {res['rank_by']}", res["customers"]), parse_mode=HTML)
    elif tool == "offer_customers":
        from src import audience
        o = store.get_offer(args.get("offer_id", ""))
        if o is None or not o.get("customer_ids"):
            await update.message.reply_text("That offer isn't for a fixed list of customers (or I couldn't find it).")
        else:
            await update.message.reply_text(people_text(f"“{o['title']}” reaches {len(o['customer_ids'])} customers", audience.people(DATA, SEGS, o["customer_ids"], "spend", 25)), parse_mode=HTML)
    elif tool == "run_campaigns":
        await campaigns(update, context)
    elif tool == "show_stats":
        await stats(update, context)
    elif tool == "list_offers":
        await (live if args.get("which") == "live" else pending)(update, context)
    elif tool == "end_offer":
        o = store.get_offer(args.get("offer_id", ""))
        if o is None or not is_live(o):
            await update.message.reply_text("I couldn't find that live offer. /live lists them.")
            return
        await update.message.reply_text(f"Take <b>{escape(o['title'])}</b> ({o['discount_pct']}% off) down? Customers will stop seeing it.",
                                        parse_mode=HTML, reply_markup=confirm_keyboard("endok", o["offer_id"]))
    elif tool == "change_rule":
        key, value, cat = args.get("rule", ""), int(args.get("new_value", 0)), args.get("category", "")
        preview, err = describe_change(key, value, cat, rules())
        if err:
            await update.message.reply_text(f"🛡 Can't change that: {escape(err)}", parse_mode=HTML)
            return
        token = secrets.token_hex(4)
        PENDING_RULES[token] = (key, value, cat)
        await update.message.reply_text(f"📝 Change the rules?\n<b>{escape(preview)}</b>\n\nNew offers are checked against the new limit.",
                                        parse_mode=HTML, reply_markup=confirm_keyboard("ruleok", token))
    else:
        await update.message.reply_text("I'm not sure how to do that. Send /help to see what I can do.")


async def draft_from_chat(update: Update, a: dict) -> None:
    """Seller described an offer: rule-check it like any other proposal and show an Allow/Reject card."""
    target = a.get("target", "ALL")
    kind = "personal" if target in set(DATA["customers"].customer_id) else "public"
    known = set(DATA["products"].product_id)
    pids = [x for x in a.get("product_ids", []) if x in known]
    days = max(1, min(int(a.get("valid_days", 7)), 60))
    cand = recommender._offer(kind, a.get("title", "Special offer"), a.get("message", ""), target, pids, int(a.get("discount_pct", 0)),
                              days, a.get("rationale", "Requested by the seller."), "Seller-requested offer", source="seller_chat")
    await _pipeline_reply(update, cand)


async def _pipeline_reply(update: Update, cand: dict) -> None:
    """Rule-check a chat draft and answer with its card, the lowered discount, or the reasons it was blocked."""
    res = recommender.run_pipeline(DATA, rules(), [cand])
    if res["skipped"]:
        await update.message.reply_text("An offer with that name for that audience is already waiting or live. /pending or /live.")
        return
    if res["blocked"]:
        o = store.get_offer(res["blocked"][0])
        reasons = "\n".join(f"• {escape(r)}" for r in o["blocked_reasons"])
        await update.message.reply_text(f"🛡 The rules blocked this offer:\n{reasons}", parse_mode=HTML)
        return
    oid = (res["stored"] + res["adjusted"])[0]
    if res["adjusted"]:
        await update.message.reply_text(f"⚖️ You asked for {cand['discount_pct']}%, but that breaks a limit, so I lowered it to the highest allowed.")
    await _send_cards(update, [store.get_offer(oid)])


async def confirm_button(update: Update, act: str, token: str) -> None:
    q = update.callback_query
    if not store.is_seller_chat(update.effective_chat.id):
        await q.answer("Seller only", show_alert=True)
        return
    await q.answer()
    if act == "cancel":
        PENDING_RULES.pop(token, None)
        await q.edit_message_text(q.message.text_html + "\n\n✖️ Cancelled", parse_mode=HTML)
        return
    if act == "endok":
        o = store.get_offer(token)
        if o is None or not is_live(o):
            await q.edit_message_text(q.message.text_html + "\n\nAlready ended.", parse_mode=HTML)
            return
        store.set_status(token, "ended")
        await q.edit_message_text(q.message.text_html + "\n\n⏹ <b>Ended</b>: removed from the offers page.", parse_mode=HTML)
        return
    change = PENDING_RULES.pop(token, None)
    if change is None:
        await q.edit_message_text(q.message.text_html + "\n\nThis request expired. Ask me again.", parse_mode=HTML)
        return
    try:
        done = apply_change(*change)
    except ValueError as e:
        await q.edit_message_text(q.message.text_html + f"\n\n🛡 Not changed: {escape(str(e))}", parse_mode=HTML)
        return
    store.log_event("rule_change", {"source": "seller_chat", "change": done, "reasons": [done]})
    await q.edit_message_text(q.message.text_html + "\n\n✅ <b>Updated rules.yaml</b>", parse_mode=HTML)


if __name__ == "__main__":
    main()
