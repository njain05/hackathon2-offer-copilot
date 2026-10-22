"""Claude as the offer strategist. Output is UNTRUSTED: callers must pass it through rules.validate_offer."""
import json
import logging
import os

log = logging.getLogger("offer_copilot.agent")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

SYSTEM = """You are the marketing strategist for an Indian dry-fruit e-commerce seller (dry fruits, nuts, makhana, fasting foods).
You choose WHICH offer suits WHOM and write the message. A separate rules engine will check your offer against hard limits,
so stay realistic: never discount customers who would buy anyway (champions/loyal), be careful with high-return customers,
protect margin, use festival timing (Diwali ~8 Nov 2026), and respect points expiring / abandoned carts.
Keep every text field short and plain: messages are read on a phone."""

_TEXT = {"type": "string"}
_INT = {"type": "integer"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


PERSONAL_SCHEMA = _obj({"discount_pct": _INT, "title": _TEXT, "message": _TEXT, "rationale": _TEXT})
CAMPAIGN = _obj({"title": _TEXT, "message": _TEXT, "target": _TEXT, "product_ids": {"type": "array", "items": _TEXT},
                 "discount_pct": _INT, "valid_days": _INT, "rationale": _TEXT, "expected_impact": _TEXT})
CAMPAIGNS_SCHEMA = _obj({"campaigns": {"type": "array", "items": CAMPAIGN}})


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _call(user: str, schema: dict) -> dict | None:
    """One structured-output request. Returns the parsed JSON object, or None (and logs why) on any failure."""
    try:
        import anthropic
        msg = anthropic.Anthropic().beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # retry on a backup model if declined
        )
    except Exception as e:  # network, auth, rate limit: the caller falls back to the rule engine
        log.warning("AI call failed (%s): %s", type(e).__name__, str(e)[:200])
        return None
    if msg.stop_reason != "end_turn":
        log.warning("AI reply unusable: stop_reason=%s", msg.stop_reason)
        return None
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("AI reply was not valid JSON")
        return None


def personalise(profile: dict, candidate: dict, rules: dict) -> dict | None:
    """AI picks discount + wording for one customer, starting from the rule-based candidate."""
    prompt = f"""CUSTOMER PROFILE:\n{json.dumps(profile, default=str, ensure_ascii=False)}
RULE-BASED STARTING POINT (you may improve it):\n{json.dumps(candidate, default=str, ensure_ascii=False)}
BUSINESS LIMITS (you will be checked against these):\n{json.dumps(rules, ensure_ascii=False)}
Decide this customer's offer. discount_pct may be 0 if this customer needs no discount.
title: max 8 words. message: customer-facing, max 35 words, warm, uses ₹ if needed.
rationale: 1-2 sentences for the seller on why this offer suits this customer."""
    return _call(prompt, PERSONAL_SCHEMA)


def propose_campaigns(context: dict, rules: dict) -> list[dict] | None:
    prompt = f"""BUSINESS CONTEXT:\n{json.dumps(context, default=str, ensure_ascii=False)}
BUSINESS LIMITS:\n{json.dumps(rules, ensure_ascii=False)}
Propose 3-5 campaigns. target is "ALL" or one of the segment names. Use only product_ids from the catalogue (2-4 per campaign).
message: customer-facing, max 35 words. rationale and expected_impact: one sentence each, for the seller."""
    out = _call(prompt, CAMPAIGNS_SCHEMA)
    return out["campaigns"] if out and out.get("campaigns") else None
