"""Dashboard server: serves web/ and a small JSON API so the sales team can act from the dashboard.

Run from repo root:  .venv/bin/python -m src.api     then open http://localhost:8000/dashboard.html
Every write needs the X-Seller-Code header (= SELLER_CODE in .env). Every offer goes through rules.validate_offer
(via recommender.run_pipeline on create, and again on approve), exactly like the bots.
"""
import asyncio
import hmac
import html
import json
import logging
import os
import re
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from src import agent, audience, bot, chat, dashboard, messages, recommender, store
from src.data_loader import ROOT, TODAY
from src.rules import apply_change, describe_change, load_rules, validate_offer

log = logging.getLogger("offer_copilot.api")
DATA, SEGS = bot.DATA, bot.SEGS
WEB = ROOT / "web"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------- offers from a dashboard draft ----------
def draft_to_offer(d: dict, rules: dict) -> tuple[dict, dict | None]:
    """Dashboard form -> candidate offer (not stored). Returns (offer, audience selection or None)."""
    known = set(DATA["products"].product_id)
    pids = [x for x in d.get("product_ids") or [] if x in known]
    days = max(1, min(int(d.get("valid_days") or 7), 60))
    disc = int(d.get("discount_pct") or 0)
    title = (d.get("title") or "").strip() or "Special offer"
    message = (d.get("message") or "").strip() or f"{disc}% off, just for you."
    if d.get("audience_mode") == "everyone":
        o = recommender._offer("public", title, message, "ALL", pids, disc, days, d.get("rationale") or "Created on the dashboard.",
                               "Seller-created offer", source="dashboard")
        return o, None
    return recommender.group_offer(DATA, SEGS, rules, d.get("filters"), pids, disc, days, title, message, d.get("rationale") or "", "dashboard")


def check(d: dict) -> dict:
    """Dry run: what the rules would say, without storing anything."""
    r = load_rules()
    o, sel = draft_to_offer(d, r)
    hist = store.group_history() if o["target"] == "GROUP" else []
    v = validate_offer(o, DATA, r, hist)
    shown = recommender.with_discount(o, v.adjusted_offer["discount_pct"]) if v.adjusted_offer else o
    return {"ok": v.ok, "reasons": v.reasons, "adjusted_discount": v.adjusted_offer["discount_pct"] if v.adjusted_offer else None,
            "audience": sel, "preview": html.unescape(re.sub(r"<[^>]+>", "", messages.customer_message(shown, DATA)))}


def create(d: dict) -> dict:
    r = load_rules()
    o, _ = draft_to_offer(d, r)
    res = recommender.run_pipeline(DATA, r, [o])
    if res["skipped"]:
        raise ApiError(HTTPStatus.CONFLICT, "An offer with this name for this audience is already waiting or live.")
    oid = (res["stored"] + res["adjusted"] + res["blocked"])[0]
    return {"offer": store.get_offer(oid), "adjusted": bool(res["adjusted"])}


def deliver(o: dict) -> int:
    """Send an approved offer to every targeted shopper through the customer bot. Returns chats reached."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chats = bot.audience_chats(o)
    if not token or not chats:
        return 0

    async def send() -> int:
        from telegram import Bot
        sent = 0
        async with Bot(token) as b:
            for chat_id in chats:
                try:
                    await b.send_message(chat_id, "🎉 New offer for you!\n\n" + messages.customer_message(o, DATA),
                                         parse_mode="HTML", reply_markup=bot.customer_keyboard(o["offer_id"]))
                    sent += 1
                except Exception as e:
                    log.warning("Could not deliver %s: %s", o["offer_id"], type(e).__name__)
        return sent
    return asyncio.run(send())


def decide(oid: str, action: str) -> dict:
    o = store.get_offer(oid)
    if o is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Offer not found")
    if action == "reject":
        if o["status"] != "pending":
            raise ApiError(HTTPStatus.CONFLICT, f"Offer is already {o['status']}")
        return {"offer": store.set_status(oid, "rejected")}
    if action == "end":
        if not bot.is_live(o):
            raise ApiError(HTTPStatus.CONFLICT, "Offer is not live")
        return {"offer": store.set_status(oid, "ended")}
    if o["status"] != "pending":
        raise ApiError(HTTPStatus.CONFLICT, f"Offer is already {o['status']}")
    v = validate_offer(o, DATA, load_rules(), bot.approval_history(o))  # rules may have changed since it was made
    if not v.ok:
        store.log_event("blocked", {"source": o["source"], "title": o["title"], "proposed": o["discount_pct"], "reasons": v.reasons})
        return {"offer": store.set_status(oid, "blocked", v.reasons), "sent": 0}
    store.set_status(oid, "approved")
    o = store.get_offer(oid)
    return {"offer": o, "sent": deliver(o)}


# ---------- AI helpers ----------
ASK_SCHEMA = agent._obj({
    "audience_mode": {"type": "string", "enum": ["everyone", "group"]},
    "segments": {"type": "array", "items": {"type": "string"}}, "cities": {"type": "array", "items": {"type": "string"}},
    "categories": {"type": "array", "items": {"type": "string"}}, "first_time_only": {"type": "boolean"}, "with_cart": {"type": "boolean"},
    "product_ids": {"type": "array", "items": {"type": "string"}}, "discount_pct": {"type": "integer"}, "valid_days": {"type": "integer"},
    "title": {"type": "string"}, "message": {"type": "string"}, "rationale": {"type": "string"}})


def ask(text: str) -> dict:
    """Plain-language request -> a filled dashboard form for the seller to review (nothing is created)."""
    if not agent.available():
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "The AI isn't set up (no ANTHROPIC_API_KEY). Fill the form by hand.")
    opts = audience.options(DATA)
    prompt = f"""The seller wants an offer: "{text}"
Today: {TODAY.isoformat()} (Diwali 8 Nov 2026). Turn the request into the offer form.
SEGMENTS (keys): {json.dumps(opts['segments'], ensure_ascii=False)}
CITIES: {json.dumps(opts['cities'])}   FAVOURITE CATEGORIES: {json.dumps(opts['categories'])}
PRODUCTS: {json.dumps(opts['products'], ensure_ascii=False)}
RULES: {json.dumps(load_rules(), ensure_ascii=False)}
Use audience_mode "everyone" only for a public deal for all; otherwise "group" with the filters they described (empty lists = no filter).
Pick 1-4 product_ids matching the request. Use exactly the discount they asked for (the rules engine lowers it if needed);
if they gave none, choose a sensible one within the limits. message: customer-facing, max 35 words, mention only chosen products.
rationale: one sentence for the seller."""
    out = agent._call(prompt, ASK_SCHEMA)
    if not out:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "The AI couldn't draft that. Try rephrasing, or fill the form by hand.")
    f = {k: out.pop(k) for k in ("segments", "cities", "categories", "first_time_only", "with_cart")}
    f["segments"] = [s for s in f["segments"] if s in audience.SEGMENT_LABELS]
    f["cities"] = [c for c in f["cities"] if c in opts["cities"]]
    f["categories"] = [c for c in f["categories"] if c in opts["categories"]]
    return {**out, "filters": f}


WRITE_SCHEMA = agent._obj({"title": {"type": "string"}, "message": {"type": "string"}})


def write_copy(d: dict) -> dict:
    if not agent.available():
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "The AI isn't set up (no ANTHROPIC_API_KEY).")
    names = DATA["products"].set_index("product_id").product_name
    who = audience.label(d.get("filters") or {}) if d.get("audience_mode") != "everyone" else "all customers"
    out = agent._call(f"""Write a short offer for {who} of Nishtha's Pantry: {int(d.get('discount_pct') or 0)}% off on
{', '.join(names.get(p, p) for p in d.get('product_ids') or [])}, valid {int(d.get('valid_days') or 7)} days (today {TODAY.isoformat()}, Diwali 8 Nov).
title: max 8 words. message: customer-facing, warm, max 35 words, uses ₹ only if needed, mention only these products.""", WRITE_SCHEMA)
    if not out:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "The AI couldn't write that right now.")
    return out


# ---------- one customer ----------
def find_customer(q: str) -> dict:
    m = bot.find_customers(q)
    if m.empty:
        return {"matches": []}
    if len(m) > 1:
        return {"matches": [{"customer_id": r.customer_id, "name": r.name, "city": r.city} for r in m.head(10).itertuples()]}
    cid = m.customer_id.iloc[0]
    prof = recommender.customer_profile(DATA, SEGS, cid)
    c = DATA["customers"].set_index("customer_id").loc[cid]
    return {"matches": [], "customer": {**prof, "name": c["name"], "city": c.city, "segment_label": audience.SEGMENT_LABELS.get(prof["segment"], prof["segment"]),
                                        "lifetime_value": prof["lifetime_value"], "on_bot": cid in set(store.all_shoppers().values())}}


def suggest_for(cid: str) -> dict:
    """Engine (and AI, if set up) suggests this customer's offer; stored as pending for the seller to approve."""
    if cid not in set(DATA["customers"].customer_id):
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown customer")
    res = recommender.offer_for_customer(DATA, SEGS, load_rules(), cid)
    if res["offer"] is None:
        return {"offer": None, "explanation": res["explanation"]}
    oid = store.add_offer({**res["offer"], "status": "pending"})
    return {"offer": store.get_offer(oid), "explanation": res["explanation"], "notes": res["notes"], "used_ai": res["used_ai"]}


def change_rule(d: dict) -> dict:
    key, value, cat = d.get("rule", ""), int(d.get("value", 0)), d.get("category", "")
    preview, err = describe_change(key, value, cat, load_rules())
    if err:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, err)
    if not d.get("confirm"):
        return {"preview": preview, "applied": False}
    done = apply_change(key, value, cat)
    store.log_event("rule_change", {"source": "dashboard", "change": done, "reasons": [done]})
    return {"preview": done, "applied": True, "rules": load_rules()}


# ---------- dashboard chat ----------
def _pipeline_result(cand: dict) -> dict:
    res = recommender.run_pipeline(DATA, load_rules(), [cand])
    if res["skipped"]:
        return {"text": "An offer with that name for that audience is already waiting or live. See Offers below."}
    oid = (res["stored"] + res["adjusted"] + res["blocked"])[0]
    o = store.get_offer(oid)
    if res["blocked"]:
        return {"text": "The rules blocked this offer:\n• " + "\n• ".join(o["blocked_reasons"]), "offers": [o]}
    if res["adjusted"]:
        return {"text": f"You asked for {cand['discount_pct']}%, but that breaks a limit, so I lowered it to {o['discount_pct']}%. Approve it when you're happy.", "offers": [o]}
    who = f" It reaches {len(o['customer_ids'])} customers." if o.get("customer_ids") else ""
    return {"text": f"Drafted and within the rules.{who} Approve it to send it out.", "offers": [o]}


def chat_turn(body: dict) -> dict:
    """One message in the dashboard chat: Claude answers or picks an action; we carry it out with the same rule-checked code."""
    text = str(body.get("text", "")).strip()[:600]
    if not text:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Type a message first.")
    if not agent.available():
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "The chat needs ANTHROPIC_API_KEY in .env. Everything else on the dashboard works without it.")
    history = [h for h in body.get("history") or [] if isinstance(h, dict)][-6:]
    res = chat.interpret(text, DATA, SEGS, load_rules(), history)
    if res is None:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "I couldn't work that out just now. Try rephrasing.")
    if "reply" in res:
        return {"text": res["reply"]}
    tool, a = res["tool"], res["input"]
    log.info("Dashboard chat action: %s", tool)
    if tool == "suggest_for_customer":
        found = find_customer(a.get("query", ""))
        if found["matches"]:
            return {"text": "Several customers match. Which one?\n" + "\n".join(f"• {m['name']} ({m['city']}, {m['customer_id']})" for m in found["matches"])}
        if not found.get("customer"):
            return {"text": "I couldn't find that customer."}
        c = found["customer"]
        intro = f"{c['name']} ({c['city']}): {c['segment_label']}, {c['orders_total']} orders, ₹{c['lifetime_value']:,} spent."
        if not c["marketing_opt_in"]:
            return {"text": intro + " They opted out of marketing, so no offer."}
        s = suggest_for(c["customer_id"])
        if not s["offer"]:
            return {"text": f"{intro} No discount: {s['explanation']}"}
        return {"text": f"{intro} {s['explanation']}", "offers": [s["offer"]]}
    if tool == "draft_offer":
        target = a.get("target", "ALL")
        kind = "personal" if target in set(DATA["customers"].customer_id) else "public"
        cand = recommender._offer(kind, a.get("title", "Special offer"), a.get("message", ""), target,
                                  [x for x in a.get("product_ids", []) if x in set(DATA["products"].product_id)],
                                  int(a.get("discount_pct", 0)), max(1, min(int(a.get("valid_days", 7)), 60)),
                                  a.get("rationale", "Asked for in the dashboard chat."), "Seller-requested offer", source="dashboard")
        return _pipeline_result(cand)
    if tool == "draft_group_offer":
        f = {k: a.get(k) for k in ("segments", "cities", "categories", "first_time_only", "with_cart")}
        o, sel = recommender.group_offer(DATA, SEGS, load_rules(), f, a.get("product_ids"), a.get("discount_pct", 0), a.get("valid_days", 7),
                                         a.get("title", ""), a.get("message", ""), a.get("rationale", ""), "dashboard")
        if not sel["count"]:
            return {"text": f"No customers match {sel['label']} right now (after leaving out opted-out and recently-offered customers)."}
        return _pipeline_result(o)
    if tool == "run_campaigns":
        r = load_rules()
        ai = recommender.ai_campaigns(DATA, SEGS, r) if agent.available() else None
        res = recommender.run_pipeline(DATA, r, ai or recommender.generate_candidates(DATA, SEGS, r))
        new = [store.get_offer(i) for i in res["stored"] + res["adjusted"]]
        return {"text": f"{'Claude' if ai else 'The rule engine'} proposed {len(new)} campaign(s): {len(res['adjusted'])} lowered by the rules, "
                        f"{len(res['blocked'])} blocked, {len(res['skipped'])} already waiting or live.", "offers": new}
    if tool == "show_stats":
        k = dashboard.build(DATA, SEGS, load_rules())["kpis"]
        return {"text": f"Live offers: {k['offers_live']} · waiting for you: {k['offers_pending']} · rejected: {k['offers_rejected']} · "
                        f"blocked by rules: {k['offers_blocked']} · guardrail saves: {k['guardrail_saves']}.\n"
                        f"Revenue in the last 30 days: ₹{k['revenue_30']:,} ({k['revenue_delta_pct']:+}% vs the 30 before)."}
    if tool == "list_offers":
        which = a.get("which", "pending")
        items = [o for o in store.get_offers("pending")] if which == "pending" else [o for o in store.get_offers("approved") if bot.is_live(o)]
        return {"text": f"{len(items)} offer(s) {'waiting for approval' if which == 'pending' else 'live'}." if items else f"Nothing {which} right now.", "offers": items[-8:]}
    if tool == "end_offer":
        o = store.get_offer(a.get("offer_id", ""))
        if o is None or not bot.is_live(o):
            return {"text": "I couldn't find that live offer."}
        return {"text": f"Take “{o['title']}” ({o['discount_pct']}% off) down? Customers will stop seeing it.", "confirm": {"kind": "end", "offer_id": o["offer_id"]}}
    if tool == "change_rule":
        key, value, cat = a.get("rule", ""), int(a.get("new_value", 0)), a.get("category", "")
        preview, err = describe_change(key, value, cat, load_rules())
        if err:
            return {"text": f"I can't change that: {err}"}
        return {"text": f"Change the rules? {preview}. New offers are checked against the new limit.",
                "confirm": {"kind": "rule", "rule": key, "category": cat, "value": value}}
    return {"text": "I'm not sure how to do that yet. Try the Create an offer form."}


# ---------- HTTP ----------
GET_ROUTES = {
    "/api/dashboard": lambda q: dashboard.build(DATA, SEGS, load_rules()),
    "/api/options": lambda q: {**audience.options(DATA), "rules": load_rules(), "ai": agent.available()},
    "/api/customer": lambda q: find_customer(q.get("q", [""])[0]),
}
POST_ROUTES = [
    (r"^/api/audience$", lambda b, m: audience.select(DATA, SEGS, load_rules(), b.get("filters"))),
    (r"^/api/check$", lambda b, m: check(b)),
    (r"^/api/offers$", lambda b, m: create(b)),
    (r"^/api/offers/(OF-\d+)/(approve|reject|end)$", lambda b, m: decide(m.group(1), m.group(2))),
    (r"^/api/ask$", lambda b, m: ask(str(b.get("text", ""))[:500])),
    (r"^/api/write$", lambda b, m: write_copy(b)),
    (r"^/api/customers/(C\d+)/suggest$", lambda b, m: suggest_for(m.group(1))),
    (r"^/api/rules$", lambda b, m: change_rule(b)),
    (r"^/api/chat$", lambda b, m: chat_turn(b)),
]


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet: one line per API call only
        if self.path.startswith("/api/"):
            log.info("%s %s", self.command, self.path.split("?")[0])

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")  # always serve the latest dashboard page
        super().end_headers()

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        code = os.getenv("SELLER_CODE", "")
        return bool(code) and hmac.compare_digest(self.headers.get("X-Seller-Code", "").encode(), code.encode())

    def _run(self, fn) -> None:
        if not self._authorised():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Enter the seller code to use the dashboard."})
            return
        try:
            self._json(HTTPStatus.OK, fn())
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except (ValueError, KeyError, TypeError) as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": f"Bad request: {e}"})
        except Exception:
            log.exception("API error")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong. Check the server log."})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in GET_ROUTES:
            self._run(lambda: GET_ROUTES[url.path](parse_qs(url.query)))
        elif url.path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
        else:
            super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        for pattern, fn in POST_ROUTES:
            m = re.match(pattern, url.path)
            if m:
                def call(fn=fn, m=m):
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 100_000:
                        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large")
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    return fn(body, m)
                self._run(call)
                return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})


def serve(port: int | None = None) -> ThreadingHTTPServer:
    port = int(os.getenv("DASHBOARD_PORT", "8000")) if port is None else port
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(WEB)))


def main() -> None:
    load_dotenv(ROOT / ".env")
    bot.TWO_BOTS = bool(os.getenv("SELLER_BOT_TOKEN"))  # same delivery rules as the running bots
    if not os.getenv("SELLER_CODE"):
        raise SystemExit("Set SELLER_CODE in .env: the dashboard asks for it before any change.")
    store.on_change(lambda: dashboard.export(DATA, SEGS))
    httpd = serve()
    log.info("Dashboard on http://localhost:%s/dashboard.html (offers page: /offers.html). Ctrl+C to stop.", httpd.server_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
