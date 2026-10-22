"""Telegram bot for the seller (v0: /start hello + remember the seller chat id).

Run from repo root:  .venv/bin/python -m src.bot
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs, which contain the token
log = logging.getLogger("offer_copilot")


def read_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def remember_seller(chat_id: int) -> None:
    """Store chat id as SELLER_CHAT_ID in data/state.json (keeps any other keys)."""
    state = read_state()
    state["SELLER_CHAT_ID"] = chat_id
    write_state(state)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    remember_seller(chat_id)
    log.info("Seller chat registered: %s", chat_id)
    await update.message.reply_text("Hello from Offer Copilot 👋")


def main() -> None:
    load_dotenv(ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing — copy .env.example to .env and fill it in.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    log.info("Offer Copilot bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
