"""Hosted entry point: the dashboard server and both Telegram bots in one process.

    python -m src.serve        (what the Dockerfile runs)

Listens on $PORT (set by Render / Hugging Face / most hosts), on all interfaces.
Only ONE copy of the bots may run per token: stop the laptop copy before starting a hosted one,
or set RUN_BOTS=0 to host just the dashboard while the bots run elsewhere.
"""
import logging
import os
import threading

from dotenv import load_dotenv

from src.data_loader import ROOT

log = logging.getLogger("offer_copilot.serve")


def main() -> None:
    load_dotenv(ROOT / ".env")  # local runs; hosts pass real environment variables
    os.environ.setdefault("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or 8000)
    from src import api, bot, dashboard, store
    bot.TWO_BOTS = bool(os.getenv("SELLER_BOT_TOKEN"))
    store.on_change(lambda: dashboard.export(api.DATA, api.SEGS))
    httpd = api.serve(port)
    log.info("Dashboard listening on port %s (/dashboard.html, /offers.html, /healthz)", port)
    if os.getenv("RUN_BOTS", "1") == "0":  # dashboard only, e.g. while the bots still run on a laptop
        log.info("RUN_BOTS=0: bots not started here")
        httpd.serve_forever()
        return
    threading.Thread(target=httpd.serve_forever, name="dashboard", daemon=True).start()
    bot.main()  # blocks: polls Telegram until stopped


if __name__ == "__main__":
    main()
