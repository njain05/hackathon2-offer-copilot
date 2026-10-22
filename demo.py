"""Offline showcase: python demo.py   (uses Claude if ANTHROPIC_API_KEY is set, else rule-based + a mock 'AI' to show guardrails)."""
import os
import re

from dotenv import load_dotenv

load_dotenv()
from src import agent, recommender, store
from src.data_loader import load_all
from src.rules import load_rules, validate_offer
from src.segments import segment_customers

strip = lambda s: re.sub(r"<[^>]+>", "", s)


def main():
    store.STATE = store.STATE.with_name("demo_state.json")  # never touch the live bot's data
    store.AUDIT = store.AUDIT.with_name("demo_audit.jsonl")
    store.STATE.unlink(missing_ok=True); store.AUDIT.unlink(missing_ok=True)
    data, rules = load_all(), load_rules()
    segs = segment_customers(data)
    print("== 1. Customer segments (inferred) ==")
    print(segs.segment.value_counts().to_string(), "\n")

    print("== 2. Campaign proposals ==")
    ai = recommender.ai_campaigns(data, segs, rules) if agent.available() else None
    cands = ai or recommender.generate_candidates(data, segs, rules)
    print("Source:", "Claude AI agent" if ai else "rule engine (no API key)")
    if not ai:  # showcase the guardrail with a deliberately bad 'AI' proposal
        bad = recommender._offer("public", "MEGA MAKHANA BLAST", "Half price!", "ALL", ["P001", "P002"], 60, 5,
                                 "Mock AI wants to go big", "Huge volume", source="ai_agent")
        cands.append(bad)
    res = recommender.run_pipeline(data, rules, cands)
    print({k: len(v) for k, v in res.items()}, "\n")
    for o in store.get_offers():
        print(f"[{o['status'].upper():8}] {o['title']} -> {o['target']} {o['discount_pct']}%  {o['blocked_reasons'][:1]}")

    print("\n== 3. Guardrail audit log (AI/rule proposals the rules changed or blocked) ==")
    for e in store.read_audit(10):
        print(f"- {e['kind']}: {e.get('title') or e.get('customer')} proposed {e.get('proposed')}% -> {e.get('final', 'BLOCKED')} | {e['reasons'][0][:90]}")

    print("\n== 4. Personal offers for 4 different shoppers ==")
    for seg in ["at_risk", "champion", "price_sensitive", "lapsed"]:
        cid = segs[segs.segment == seg].customer_id.iloc[0]
        r = recommender.offer_for_customer(data, segs, rules, cid)
        print(f"\n{cid} [{seg}] -> ", f"{r['offer']['discount_pct']}% off: {r['offer']['title']}" if r["offer"] else "NO OFFER")
        print("   why:", r["explanation"], r["notes"] or "")
    print("\nAPI:", "Claude used" if agent.available() else "no ANTHROPIC_API_KEY set -> rule-based path")


if __name__ == "__main__":
    main()
