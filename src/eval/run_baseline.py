"""Run the ungoverned baseline on a test set and score it. Saves progress after every case
and resumes automatically, so a rate limit or Ctrl-C never loses work.

    python src/eval/run_baseline.py --cases cases_v1.jsonl
    python src/eval/run_baseline.py --cases cases_v1.jsonl --limit 5
    python src/eval/run_baseline.py --cases cases_v1.jsonl --fresh      # ignore saved progress
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

from agents.baseline import DailyLimitReached, decide_with_llm, make_client
from eval.metrics import print_report, score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases_v0.jsonl")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=3.0, help="seconds between calls")
    ap.add_argument("--fresh", action="store_true", help="ignore saved progress and start over")
    args = ap.parse_args()

    model = os.environ.get("LLM_MODEL")
    if not model:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        model = os.environ.get("LLM_MODEL", "openai/gpt-oss-20b")

    cases = [json.loads(l) for l in open(ROOT / "data" / "eval" / args.cases)]
    if args.limit:
        cases = cases[: args.limit]

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    cache_path = out_dir / f"progress_{Path(args.cases).stem}_{safe_model}.json"
    preds = {}
    if cache_path.exists() and not args.fresh:
        preds = json.loads(cache_path.read_text())
        print(f"Resuming: {len(preds)} cases already done for {model}")

    client = make_client()
    stopped = False
    try:
        for c in cases:
            if c["case_id"] in preds:
                continue
            try:
                out = decide_with_llm(client, model, c)
            except DailyLimitReached as e:
                print(f"\nDAILY LIMIT REACHED at {c['case_id']}:\n{str(e)[:400]}")
                stopped = True
                break
            except Exception as e:
                print(f"{c['case_id']}: ERROR {str(e)[:200]}")
                out = None
            if out is not None:                       # only keep good answers; errors get retried next run
                preds[c["case_id"]] = out
                cache_path.write_text(json.dumps(preds))
            exp = c["expected"]["action"]
            got = out["action"] if out else "error"
            if exp != got:
                print(f"{c['case_id']}: expected={exp:<12} got={got:<12} <-- differs")
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved; run the same command again to resume.")
        stopped = True

    done = [c for c in cases if c["case_id"] in preds]
    report = score(done, preds)
    complete = len(done) == len(cases)
    print_report(f"BASELINE ({model}, no governance, {args.cases}) - {len(done)}/{len(cases)} cases done", report)

    if complete:
        path = out_dir / f"baseline_{Path(args.cases).stem}.json"
        path.write_text(json.dumps({"model": model, "report": report, "predictions": preds}, indent=2))
        print(f"\nComplete. Saved {path.relative_to(ROOT)}")
    else:
        print("\nINCOMPLETE. Run the same command again later and it will continue from where it stopped.")
        if stopped:
            print("If the message above mentions a daily limit, wait until it resets (the message says how long).")


if __name__ == "__main__":
    main()
