"""Run actual cases and export evidence. Each invocation uses an isolated DB and new keys."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prepare_examples import prepare
from src.agent import Runner, public_result
from src.cli import export_evidence
from src.storage import Store


def validate_case(name, state):
    trace = state["trace"]
    out = public_result(state)
    expected = {"missing-budget": "needs_input", "missing-deadline": "needs_input",
                "budget-impossible": "incomplete", "missing-materials": "incomplete",
                "partial-materials": "incomplete", "time-impossible": "incomplete",
                "persistent-failure": "incomplete", "limit": "incomplete"}.get(name, "completed")
    checks = {"expected_status": state["status"] == expected}
    if name == "budget-replan":
        quotes = [(i, e["result"]) for i, e in enumerate(trace) if e.get("action") == "estimate_plan" and e["result"]["status"] == "succeeded"]
        checks["observed_12_then_8"] = any(q["completion_total"] == "12.00" and any(
            r["completion_total"] == "8.00" for j, r in quotes if j > i) for i, q in quotes)
        checks["spent_8"] = out["spent_total"] == "8.00"
    if name in ("tool-failure", "persistent-failure", "resume"):
        failures = [e for e in trace if e.get("result", {}).get("error_code") == "INJECTED_FAILURE"]
        checks["failure_recorded"] = bool(failures)
        checks["failed_attempts_free"] = all(e["result"]["charged_units"] == "0.00" for e in failures)
        checks["script_generated_once"] = len([e for e in trace if e.get("action") == "generate_script" and e["result"]["status"] == "succeeded"]) == 1
    checks["within_call_limits"] = all(state["counts"][k] <= state["limits"][k] for k in ("decisions", "tools"))
    return checks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["mock", "deepseek"], required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--all-scenarios", action="store_true", help="also run the full acceptance matrix with the real provider")
    p.add_argument("--cases", nargs="+", help="run selected acceptance cases, preserving other evidence directories")
    args = p.parse_args()
    target = Path(args.output)
    cases = prepare(target/"inputs")
    db = Path(".runtime") / f"evaluation-{uuid.uuid4().hex}.sqlite3"
    store = Store(db)
    runner = Runner(store)
    scenarios = list(cases) + ["tool-failure", "persistent-failure", "repeat", "resume", "limit"]
    if args.provider == "deepseek" and not args.all_scenarios:
        scenarios = ["budget-replan", "tool-failure"]
    if args.cases:
        allowed = set(cases) | {"tool-failure", "persistent-failure", "repeat", "resume", "limit"}
        if not set(args.cases) <= allowed:
            p.error("unknown case name")
        scenarios = args.cases
    summary = []
    for name in scenarios:
        payload = cases.get(name, cases["budget-replan"])
        fault = "save_plan:once" if name == "tool-failure" else "save_plan:always" if name in ("persistent-failure", "resume") else None
        state = runner.create(payload, args.provider, name, fault, max_tools=2 if name == "limit" else 20)
        export_evidence(target/name, state)
        if name == "repeat":
            repeated = runner.create(payload, args.provider, name)
            export_evidence(target/"repeat-second", repeated)
            assert repeated == state, "Repeated request changed state"
        if name == "resume":
            # Simulate external service restoration explicitly, not a model action.
            owner, recovered = store.claim(state["run_id"])
            recovered["faults"]["save_plan"]["mode"] = "once"
            recovered["trace"].append({"sequence": len(recovered["trace"])+1, "action": "evaluation_restore_service",
                "arguments": {}, "result": {"status": "succeeded", "summary": "Evaluator disabled persistent pre-commit fault"},
                "reason_summary": "External tool service recovered; this is not a model action.", "next_action": "resume"})
            store.save(recovered, owner, release=True)
            state = runner.resume(state["run_id"])
            export_evidence(target/"resume-after", state)
        summary.append({"case": name, "status": state["status"], "reason_code": state["reason_code"],
                        "spent": public_result(state)["spent_total"], "counts": state["counts"], "run_id": state["run_id"],
                        "checks": validate_case(name, state)})
    target.mkdir(parents=True, exist_ok=True)
    (target/"summary.json").write_text(json.dumps({"provider": args.provider, "database": str(db), "results": summary}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(all(row["checks"].values()) for row in summary) else 4


if __name__ == "__main__":
    raise SystemExit(main())
