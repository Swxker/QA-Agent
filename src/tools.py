"""Business tools. All side effects are checkpointed by the runner in one transaction."""
import copy
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


class ToolError(Exception):
    def __init__(self, code, message, retryable=False):
        self.code, self.message, self.retryable = code, message, retryable


def material(state):
    selected = state.get("plan", {}).get("material_id")
    for item in state["catalog"]:
        if item["id"] == selected:
            return item
    raise ToolError("DEPENDENCY_MISSING", "Select a retrieved material first")


def spent(state):
    return sum((Decimal(x["amount"]) for x in state["ledger"]), Decimal(0))


def signature(state):
    return digest({"version": state["plan"]["version"], "ledger": state["ledger"],
                   "script_id": state.get("script", {}).get("id")})


def quote(state, now):
    item = material(state)
    rules = state["rules"]
    has_script = bool(state.get("script"))
    remaining = Decimal(item["license_cost"]) + Decimal(rules["save_cost"])
    minutes = item["minutes"] + rules["save_minutes"]
    if not has_script:
        remaining += Decimal(rules["script_cost"])
        minutes += rules["script_minutes"]
    if state.get("saved_plan"):
        remaining, minutes = Decimal(0), 0
    total = spent(state) + remaining
    deadline = datetime.fromisoformat(state["input"]["deadline"])
    finish = now + timedelta(minutes=minutes)
    gap = max(Decimal(0), total - Decimal(state["input"]["budget"]))
    missing = sorted(set(rules["required_coverage"]) - set(item["coverage"]))
    return {
        "signature": signature(state), "plan_version": state["plan"]["version"],
        "spent": money(spent(state)), "remaining_cost": money(remaining),
        "completion_total": money(total), "budget_gap": money(gap),
        "remaining_minutes": minutes, "expected_finish": finish.isoformat(),
        "time_gap_minutes": max(0, round((finish-deadline).total_seconds()/60, 3)),
        "missing_coverage": missing,
        "feasible": gap == 0 and finish <= deadline and not missing,
    }


def require_feasible(state, now):
    if not state.get("estimate") or state["estimate"]["signature"] != signature(state):
        raise ToolError("ESTIMATE_REQUIRED", "Run estimate_plan for current plan and artifacts first")
    current = quote(state, now)
    if not current["feasible"]:
        raise ToolError("PLAN_INFEASIBLE", json.dumps(current, ensure_ascii=False))
    return current


def charge(state, key, amount, label):
    if not any(row["key"] == key for row in state["ledger"]):
        state["ledger"].append({"key": key, "amount": money(amount), "label": label})


def set_step(state, step_id, status, dependencies=(), artifact_id=None):
    state["steps"][step_id] = {"status": status, "depends_on": list(dependencies),
                               "artifact_id": artifact_id, "plan_version": state.get("plan", {}).get("version", 0)}


def execute(state, name, args, now):
    if name == "search_materials":
        cap = args.get("max_license_cost")
        found = [x for x in state["catalog"] if any(t in state["input"]["theme"] for t in x["topics"])
                 and (cap is None or Decimal(x["license_cost"]) <= Decimal(str(cap)))]
        state["retrieved"] = list(dict.fromkeys(state["retrieved"] + [x["id"] for x in found]))
        set_step(state, "search", "succeeded", artifact_id="search-results")
        partial = any(set(state["rules"]["required_coverage"]) - set(x["coverage"]) for x in found)
        return {"status": "partial" if partial or not found else "succeeded", "materials": found,
                "summary": "Candidates are not yet licensed. Check coverage and estimate before generation."}

    if name == "revise_plan":
        if state.get("saved_plan"):
            raise ToolError("PLAN_ALREADY_SAVED", "Saved plan is immutable; finish this run")
        if args["material_id"] not in state["retrieved"]:
            raise ToolError("MATERIAL_NOT_RETRIEVED", "Search and select a returned material ID")
        old = state.get("plan")
        if old and all(old.get(k) == v for k, v in args.items()):
            return {"status": "succeeded", "reused": True, "plan": old}
        if old:
            state["plan_history"].append(copy.deepcopy({"plan": old, "steps": state["steps"]}))
            for key in list(state["steps"]):
                if key.startswith("estimate") or key in ("script", "save"):
                    state["steps"][key]["status"] = "superseded"
            if state.get("script"):
                state["archived_scripts"].append(state.pop("script"))
        state.pop("estimate", None)
        state["plan"] = {**args, "version": (old["version"]+1 if old else 1)}
        set_step(state, "select", "succeeded", ["search"], args["material_id"])
        version = state["plan"]["version"]
        set_step(state, f"estimate-v{version}-before-script", "pending", ["select"])
        set_step(state, "script", "pending", [f"estimate-v{version}-before-script"])
        set_step(state, f"estimate-v{version}-before-save", "pending", ["script"])
        set_step(state, "save", "pending", ["script", f"estimate-v{version}-before-save"])
        return {"status": "succeeded", "plan": state["plan"]}

    if name == "estimate_plan":
        result = quote(state, now)
        phase = "before-save" if state.get("script") else "before-script"
        result["step_id"] = f"estimate-v{state['plan']['version']}-{phase}"
        state["estimate"] = result
        deps = ["select", "script"] if state.get("script") else ["select"]
        set_step(state, result["step_id"], "succeeded", deps, result["step_id"])
        return {"status": "succeeded", **result}

    if name == "generate_script":
        if state.get("script"):
            return {"status": "succeeded", "reused": True, "script": state["script"]}
        require_feasible(state, now)
        if args["material_ids"] != [state["plan"]["material_id"]]:
            raise ToolError("INVALID_MATERIAL_REFERENCE", "Script must reference exactly the selected material")
        if not args["title"].strip() or len(args["body"].strip()) < 20:
            raise ToolError("INVALID_SCRIPT", "Nonblank title and at least 20 body characters required")
        script_id = "script-" + digest({"run": state["run_id"], "v": state["plan"]["version"]})[:16]
        state["script"] = {"id": script_id, **args, "plan_version": state["plan"]["version"]}
        charge(state, script_id, state["rules"]["script_cost"], "script generation")
        set_step(state, "script", "succeeded", ["select", state["estimate"]["step_id"]], script_id)
        return {"status": "succeeded", "script": state["script"]}

    if name == "save_plan":
        if state.get("saved_plan"):
            return {"status": "succeeded", "reused": True, "plan_id": state["saved_plan"]["plan_id"]}
        if not state.get("script"):
            raise ToolError("DEPENDENCY_MISSING", "Generate script before saving")
        final_quote = require_feasible(state, now)
        item = material(state)
        plan_id = "plan-" + state["run_id"]
        charge(state, plan_id+":license", item["license_cost"], "material license")
        charge(state, plan_id+":save", state["rules"]["save_cost"], "plan save")
        set_step(state, "save", "succeeded", ["script", state["estimate"]["step_id"]], plan_id)
        state["saved_plan"] = {
            "plan_id": plan_id, "theme": state["input"]["theme"], "plan": copy.deepcopy(state["plan"]),
            "script": copy.deepcopy(state["script"]), "material": item,
            "execution_steps": ["检索并核对素材", "选择活动形式与目标", "核算预算和耗时", "生成脚本", "复核后保存方案"],
            "steps": copy.deepcopy(state["steps"]), "ledger": copy.deepcopy(state["ledger"]),
            "total_cost": money(spent(state)),
            "budget_remaining": money(Decimal(state["input"]["budget"])-spent(state)),
            "deadline": state["input"]["deadline"], "saved_at": now.isoformat(),
            "remaining_preparation_minutes": final_quote["remaining_minutes"],
            "expected_finish": final_quote["expected_finish"],
            "notice": "合成素材与模拟费用；此记录证明方案保存，不代表活动已发布。",
        }
        return {"status": "succeeded", "plan_id": plan_id, "total_cost": money(spent(state))}

    if name == "finish":
        if not state.get("saved_plan") or state["steps"].get("save", {}).get("status") != "succeeded":
            raise ToolError("NOT_COMPLETED", "A successful persisted save is required before finish")
        state["status"], state["reason_code"] = "completed", "SAVED"
        return {"status": "succeeded", "summary": "Saved plan verified; run completed"}

    if name == "stop":
        if state.get("saved_plan"):
            raise ToolError("PLAN_ALREADY_SAVED", "Plan saved successfully; call finish")
        state["status"], state["reason_code"] = "incomplete", "MODEL_STOPPED"
        state["reason"] = args["reason"]
        return {"status": "succeeded", "summary": args["reason"]}
    raise ToolError("UNKNOWN_TOOL", name)
