"""Claude as the offer strategist. Output is UNTRUSTED: callers must pass it through rules.validate_offer."""
import json
import os
import re

SYSTEM = """You are the marketing strategist for an Indian dry-fruit e-commerce seller (dry fruits, nuts, makhana, fasting foods).
You choose WHICH offer suits WHOM and write the message. A separate rules engine will check your offer against hard limits,
so stay realistic: never discount customers who would buy anyway (champions/loyal), be careful with high-return customers,
protect margin, use festival timing (Diwali ~8 Nov 2026), and respect points expiring / abandoned carts.
Reply with ONLY one JSON object, no prose, no markdown."""


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _call(user: str, max_tokens: int = 1200):
    """Return parsed JSON (dict or list) or None on any failure."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            max_tokens=max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def personalise(profile: dict, candidate: dict, rules: dict) -> dict | None:
    """AI picks discount + wording for one customer, starting from the rule-based candidate."""
    prompt = f"""CUSTOMER PROFILE:\n{json.dumps(profile, default=str, ensure_ascii=False)}
RULE-BASED STARTING POINT (you may improve it):\n{json.dumps(candidate, default=str, ensure_ascii=False)}
BUSINESS LIMITS (you will be checked against these):\n{json.dumps(rules, ensure_ascii=False)}
Return JSON: {{"discount_pct": int, "title": str (max 8 words), "message": str (max 35 words, warm, uses ₹ if needed),
"rationale": str (1-2 sentences for the seller: why this offer for this customer), "reasoning": str (your short step-by-step logic)}}.
discount_pct may be 0 if this customer needs no discount."""
    out = _call(prompt, 700)
    return out if isinstance(out, dict) else None


def propose_campaigns(context: dict, rules: dict) -> list[dict] | None:
    prompt = f"""BUSINESS CONTEXT:\n{json.dumps(context, default=str, ensure_ascii=False)}
BUSINESS LIMITS:\n{json.dumps(rules, ensure_ascii=False)}
Propose 3-5 campaigns. Return a JSON list; each item: {{"title": str, "message": str (customer-facing, max 35 words),
"target": "ALL" or one of the segment names, "product_ids": [ids from the catalogue], "discount_pct": int, "valid_days": int,
"rationale": str, "expected_impact": str}}. Use only product_ids that exist in the catalogue."""
    out = _call(prompt, 1800)
    return out if isinstance(out, list) else None
