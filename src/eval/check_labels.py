"""Do the labels agree with the deterministic rules?  python src/eval/check_labels.py [cases_v0.jsonl]"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from governance.rules import decide

name = sys.argv[1] if len(sys.argv) > 1 else "cases_v0.jsonl"
rows = [json.loads(l) for l in open(ROOT / "data" / "eval" / name)]
bad = 0
for c in rows:
    got = decide(c["order"], c["customer"], c["evidence"], c["intake"]["reason"])
    exp = c["expected"]
    ok = got["action"] == exp["action"] and (exp["action"] != "approve" or got["refund_amount"] == exp["refund_amount"])
    if not ok:
        bad += 1
        print(f"MISMATCH {c['case_id']}: expected {exp['action']} {exp['refund_amount']}, rules gave {got['action']} {got['refund_amount']}")
print(f"{name}: {len(rows) - bad}/{len(rows)} labels agree with the rule engine")
