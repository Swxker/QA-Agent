"""Independently reconcile exported real or mock evidence; never reads credentials."""
import argparse
import json
from decimal import Decimal
from pathlib import Path


def audit(directory):
    directory = Path(directory)
    rows, latest = [], {}
    for output in sorted(directory.glob("*/output.json")):
        state = json.loads(output.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (output.parent/"trace.jsonl").read_text(encoding="utf-8").splitlines()]
        ledger = state["ledger"]
        spent = sum((Decimal(row["amount"]) for row in ledger), Decimal(0))
        charges = sum((Decimal(e["result"].get("charged_units", "0")) for e in events), Decimal(0))
        checks = {
            "ledger_matches_total": spent == Decimal(state["spent_total"]),
            "trace_charges_match_ledger": charges == spent,
            "unique_charge_keys": len({row["key"] for row in ledger}) == len(ledger),
            "failed_attempts_free": all(Decimal(e["result"].get("charged_units", "0")) == 0 for e in events if e["result"]["status"] == "failed"),
            "within_limits": all(state["counts"][k] <= state["limits"][k] for k in ("tools", "decisions")),
        }
        if state["status"] == "completed":
            checks["saved_and_within_budget"] = bool(state.get("saved_plan")) and spent <= Decimal(state["input"]["budget"])
            checks["saved_total_matches"] = Decimal(state["saved_plan"]["total_cost"]) == spent
        rows.append({"case": output.parent.name, "status": state["status"], "spent": str(spent), "checks": checks})
        previous = latest.get(state["run_id"], [])
        if len(events) >= len(previous):
            latest[state["run_id"]] = events
    cross_checks = {}
    for left, right, key in (("repeat", "repeat-second", None), ("resume", "resume-after", "script")):
        if (directory/left/"output.json").exists() and (directory/right/"output.json").exists():
            a = json.loads((directory/left/"output.json").read_text(encoding="utf-8"))
            b = json.loads((directory/right/"output.json").read_text(encoding="utf-8"))
            cross_checks[f"{left}_preserves_success"] = a == b if key is None else a[key] == b[key]
    usage = {k: 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    models = set()
    for events in latest.values():
        for e in events:
            meta = e.get("model", {})
            if meta.get("reported_model"):
                models.add(meta["reported_model"])
            for key in usage:
                usage[key] += meta.get("usage", {}).get(key, 0)
    return {"all_passed": bool(rows) and all(all(x["checks"].values()) for x in rows) and all(cross_checks.values()),
            "cases": rows, "cross_checks": cross_checks, "unique_runs": len(latest),
            "reported_models": sorted(models), "usage_unique_run_events": usage,
            "usage_boundary": "Reported successful API responses only; not a billing reconciliation; repeated snapshots deduplicated."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", required=True)
    args = p.parse_args()
    result = audit(args.directory)
    (Path(args.directory)/"audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["all_passed"] else 4)
