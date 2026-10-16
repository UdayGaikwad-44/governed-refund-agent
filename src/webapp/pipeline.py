"""One request through the governed pipeline: intake -> rule engine -> (kill switch) -> decision."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agents.governed import classify_reason, decide_governed  # noqa: E402

REASONS = ("damaged", "wrong_item", "change_of_mind", "not_received")


def keyword_reason(message):
    """Very simple no-LLM intake for demos. Far weaker than the LLM; shown only so the app runs anywhere."""
    m = message.lower()
    if re.search(r"never (came|arrived|received)|not (received|delivered)|lost|where is my|tracking", m):
        return "not_received"
    if re.search(r"wrong (item|product|colour|color|size sent)|different (item|product)|instead of", m):
        return "wrong_item"
    if re.search(r"broke|broken|damag|crack|torn|tear|defect|not working|doesn.?t work|stopped working|power on|not charging|leak|dead|chipped|bent|hole|ruined", m):
        return "damaged"
    return "change_of_mind"


def load_clauses():
    """Map clause id -> its text, parsed from policy/return_policy.md."""
    lines = (ROOT / "policy" / "return_policy.md").read_text().splitlines()
    clauses, i = {}, 0
    while i < len(lines):
        m = re.match(r"\*\*(R-\d\d)\b", lines[i])
        if m:
            j, block = i + 1, [lines[i].replace("**", "")]
            while j < len(lines) and lines[j].startswith("- "):
                block.append(lines[j])
                j += 1
            clauses[m.group(1)] = "\n".join(block)
            i = j
            continue
        i += 1
    return clauses


def process(message, order, customer, evidence, mode, manual_reason=None,
            auto_approve=True, client=None, model=None):
    """mode: 'llm' | 'keyword' | 'manual'. Returns a dict describing everything that happened."""
    error = None
    if mode == "llm":
        try:
            reason = classify_reason(client, model, message)
        except Exception as e:                    # model down, rate limit, crash -> fail closed
            reason, error = None, str(e)[:200]
    elif mode == "keyword":
        reason = keyword_reason(message)
    else:
        reason = manual_reason

    decision = decide_governed(order, customer, evidence, reason)
    killed = False
    if not auto_approve and decision["action"] == "approve":
        killed = True
        decision = {"action": "escalate", "refund_amount": None,
                    "clauses": decision["clauses"] + ["KILL-SWITCH"],
                    "proposed_amount": decision["refund_amount"]}
    return {"intake_mode": mode, "reason": reason, "intake_error": error,
            "kill_switch_applied": killed, **decision}
