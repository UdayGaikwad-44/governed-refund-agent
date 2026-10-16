"""Compare three systems on the same cases:
   A  baseline        - LLM decides alone (results from run_baseline.py)
   B  governed        - LLM does intake only; rule engine decides
   C  gated baseline  - LLM decides; gate blocks unsafe approvals

    python src/eval/run_governed.py --cases cases_v1.jsonl
Needs results/baseline_<cases>.json from run_baseline.py first. Intake results are cached/resumable.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agents.baseline import DailyLimitReached, make_client
from agents.governed import classify_reason, decide_governed, gate_proposal
from eval.metrics import print_report, score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases_v1.jsonl")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()

    model = os.environ.get("LLM_MODEL", "llama31-8k")
    stem = Path(args.cases).stem
    cases = [json.loads(l) for l in open(ROOT / "data" / "eval" / args.cases)]

    base_path = ROOT / "results" / f"baseline_{stem}.json"
    if not base_path.exists():
        sys.exit(f"Run run_baseline.py first: {base_path} not found")
    base = json.loads(base_path.read_text())
    if base.get("model") != model:
        print(f"WARNING: baseline was run with {base.get('model')} but LLM_MODEL is {model}. "
              f"Use the same model for a fair comparison.")
    preds_a = base["predictions"]

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    cache = ROOT / "results" / f"intake_{stem}_{safe}.json"
    reasons = {} if args.fresh or not cache.exists() else json.loads(cache.read_text())
    if reasons:
        print(f"Resuming intake: {len(reasons)} cases already done")

    client = make_client()
    try:
        for c in cases:
            if c["case_id"] in reasons:
                continue
            try:
                r = classify_reason(client, model, c["customer_message"])
            except DailyLimitReached as e:
                print(f"DAILY LIMIT REACHED:\n{str(e)[:300]}\nRun the same command later to resume.")
                break
            except Exception as e:
                print(f"{c['case_id']}: ERROR {str(e)[:150]}")
                r = None
            reasons[c["case_id"]] = r
            cache.write_text(json.dumps(reasons))
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\nInterrupted; progress saved. Run again to resume.")

    if len(reasons) < len(cases):
        sys.exit(f"Intake incomplete ({len(reasons)}/{len(cases)}). Run the same command again.")

    # Intake accuracy against the gold reasons
    wrong = [(c["case_id"], c["intake"]["reason"], reasons[c["case_id"]]) for c in cases
             if reasons[c["case_id"]] != c["intake"]["reason"]]
    print(f"\nIntake accuracy: {len(cases) - len(wrong)}/{len(cases)}")
    for cid, gold, got in wrong:
        print(f"  {cid}: gold={gold} got={got}")

    preds_b, preds_c, overrides = {}, {}, []
    for c in cases:
        cid = c["case_id"]
        r = reasons[cid]
        preds_b[cid] = decide_governed(c["order"], c["customer"], c["evidence"], r)
        final, changed = gate_proposal(preds_a.get(cid), c["order"], c["customer"], c["evidence"], r)
        preds_c[cid] = final
        if changed:
            overrides.append(cid)

    rep_a, rep_b, rep_c = score(cases, preds_a), score(cases, preds_b), score(cases, preds_c)
    print_report("A: baseline (LLM decides alone)", rep_a)
    print_report("B: governed (LLM intake + rule engine)", rep_b)
    print_report("C: gated baseline (LLM decides, gate blocks unsafe approvals)", rep_c)
    print(f"\nGate overrides in C: {len(overrides)} {overrides}")

    n_adv = sum(1 for c in cases if c["expected"].get("injection_attempt"))
    rows = [
        ("A. LLM alone", rep_a), ("B. LLM intake + rule engine", rep_b), ("C. LLM + safety gate", rep_c)]
    md = [f"Model: {model}   Cases: {len(cases)} ({n_adv} injection attempts)   Intake accuracy: {len(cases) - len(wrong)}/{len(cases)}", "",
          "| System | Accuracy | Unsafe approvals | Injection held | Wrong amounts |", "|---|---|---|---|---|"]
    for name, r in rows:
        md.append(f"| {name} | {r['accuracy']:.1%} | {r['unsafe_approvals']} | {r['injection_held']} | {len(r['wrong_amount_cases'])} |")
    (ROOT / "results" / f"results_table_{stem}.md").write_text("\n".join(md) + "\n")
    (ROOT / "results" / f"comparison_{stem}.json").write_text(json.dumps(
        {"model": model, "intake_wrong": wrong, "gate_overrides": overrides,
         "A": rep_a, "B": rep_b, "C": rep_c}, indent=2))
    print("\n" + "\n".join(md))
    print(f"\nSaved results/results_table_{stem}.md and results/comparison_{stem}.json")


if __name__ == "__main__":
    main()
