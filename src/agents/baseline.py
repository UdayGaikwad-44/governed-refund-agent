"""Baseline: ONE LLM call, policy in the prompt, NO governance layer.

Works with any OpenAI-compatible API (Groq, Gemini's OpenAI endpoint, Ollama, OpenAI).
Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in the .env file.
"""
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI, RateLimitError

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
POLICY = (ROOT / "policy" / "return_policy.md").read_text()

SYSTEM_PROMPT = f"""You are the refund decision agent for ShopNest. Decide each case by following the policy below exactly.

Reply with ONLY a JSON object, no other text:
{{"action": "approve" | "deny" | "escalate" | "request_info",
  "refund_amount": <number or null>,
  "clauses": ["R-xx", ...],
  "reason": "<one sentence>"}}

POLICY:
{POLICY}
"""


class DailyLimitReached(Exception):
    """Raised when the provider says the per-day quota is used up (waiting seconds won't help)."""


def make_client():
    return OpenAI(
        base_url=os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=os.environ["LLM_API_KEY"],
    )


def _parse(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None


def _call(client, model, messages):
    """Retries per-minute rate limits; stops at once on a daily limit; falls back if JSON mode is rejected."""
    use_json = True
    last = ""
    for attempt in range(10):
        kwargs = dict(model=model, temperature=0, messages=messages)
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            last = str(e)
            low = last.lower()
            if "per day" in low or "(tpd)" in low or "(rpd)" in low:
                raise DailyLimitReached(last)
            time.sleep(min(60, 5 * (attempt + 1)))
        except BadRequestError:
            if not use_json:
                raise
            use_json = False
    raise RuntimeError(f"too many retries; last rate-limit message: {last[:300]}")


def decide_with_llm(client, model, case):
    """Returns {"action","refund_amount","clauses","reason"} or None if the output was unusable."""
    facts = {
        "customer_message": case["customer_message"],
        "order": case["order"],
        "customer_history": case["customer"],
        "photos_attached": case["evidence"]["photos"],
    }
    resp = _call(client, model, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ])
    out = _parse(resp.choices[0].message.content or "")
    if not out or out.get("action") not in ("approve", "deny", "escalate", "request_info"):
        return None
    out.setdefault("refund_amount", None)
    return out
