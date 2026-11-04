import copy
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from src.models import ConfigurationError, DeepSeek, Mock, ModelError
from src.schemas import Action, PARAMS, RunInput
from src.storage import Store
from src.tools import ToolError, digest, execute, money, quote, spent

ROOT = Path(__file__).resolve().parents[1]


def utcnow():
    return datetime.now(timezone.utc)


def load_data(data_dir):
    path = Path(data_dir)
    catalog = json.loads((path / "materials.json").read_text(encoding="utf-8"))
    rules = json.loads((path / "tool_rules.json").read_text(encoding="utf-8"))
    # Rules are trusted repository configuration, still reject unsafe arithmetic.
    if len({x["id"] for x in catalog}) != len(catalog):
        raise ValueError("duplicate material IDs")
    for item in catalog:
        price = Decimal(item["license_cost"])
        if not price.is_finite() or price < 0 or not isinstance(item["minutes"], int) or item["minutes"] < 0:
            raise ValueError("invalid material cost/duration")
    for key in ("script_cost", "save_cost"):
        price = Decimal(rules[key])
        if not price.is_finite() or price < 0:
            raise ValueError("invalid tool cost")
    for key in ("script_minutes", "save_minutes"):
        if not isinstance(rules[key], int) or rules[key] < 0:
            raise ValueError("invalid tool duration")
    return catalog, rules


class Runner:
    def __init__(self, store, data_dir=ROOT/"data", clock=utcnow, model=None):
        self.store: Store = store
        self.catalog, self.rules = load_data(data_dir)
        self.clock, self.model_override = clock, model

    def create(self, payload, provider="mock", request_key=None, fault=None, max_decisions=20, max_tools=20):
        validated = RunInput.model_validate(payload).model_dump(mode="json")
        if provider not in ("mock", "deepseek"):
            raise ValueError("provider must be mock or deepseek")
        if not 1 <= max_decisions <= 100 or not 1 <= max_tools <= 100:
            raise ValueError("limits must be between 1 and 100")
        faults = {}
        if fault:
            tool, mode = fault.split(":", 1)
            if tool not in ("search_materials", "estimate_plan", "generate_script", "save_plan") or mode not in ("once", "always"):
                raise ValueError("fault must be business_tool:once or business_tool:always")
            faults[tool] = {"mode": mode, "used": 0}
        import os
        config = {"input": validated, "provider": provider, "catalog": self.catalog, "rules": self.rules,
                  "fault": fault, "limits": [max_decisions, max_tools],
                  "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash") if provider == "deepseek" else "offline-policy",
                  "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com") if provider == "deepseek" else None}
        state = {
            "run_id": str(uuid.uuid4()), "provider": provider, "model_config": {k: config[k] for k in ("model", "base_url")},
            "input": validated, "status": "running", "reason_code": None,
            "created_at": self.clock().isoformat(), "updated_at": self.clock().isoformat(),
            "catalog": self.catalog, "rules": self.rules, "faults": faults,
            "limits": {"decisions": max_decisions, "tools": max_tools},
            "counts": {"decisions": 0, "tools": 0}, "steps": {}, "trace": [], "ledger": [],
            "retrieved": [], "plan_history": [], "archived_scripts": [],
            "messages": [{"role": "user", "content": json.dumps({"request": validated, "rules": self.rules}, ensure_ascii=False)}],
            "resumptions": 0,
        }
        state, created = self.store.create(request_key or state["run_id"], digest(config), state)
        if not created:
            return state
        return self._execute(state["run_id"])

    def resume(self, run_id, budget=None, deadline=None):
        owner, state = self.store.claim(run_id)
        try:
            if state["status"] == "completed":
                return state
            payload = dict(state["input"])
            changed = False
            for key, value in (("budget", budget), ("deadline", deadline)):
                if value is not None:
                    if payload[key] is not None and str(payload[key]) != str(value):
                        raise ValueError("resume can only fill missing fields; create a new request for changed constraints")
                    if payload[key] is None:
                        payload[key], changed = value, True
            state["input"] = RunInput.model_validate(payload).model_dump(mode="json")
            if state["counts"]["decisions"] >= state["limits"]["decisions"] or state["counts"]["tools"] >= state["limits"]["tools"]:
                if not state.get("pending"):
                    return state  # cumulative caps cannot be bypassed by resume
            # Repeating resume on an already missing input does not consume model calls.
            state["resumptions"] += 1
            if changed:
                state["messages"].append({"role": "user", "content": "Missing inputs supplied: " + json.dumps(state["input"], ensure_ascii=False)})
            else:
                state["messages"].append({"role": "user", "content": "Resume using successful artifacts; reconsider the last failed step. Do not repeat committed charges."})
            failed = next((e for e in reversed(state["trace"]) if e.get("result", {}).get("error_code") == "INJECTED_FAILURE"), None)
            if failed and state["reason_code"] == "MODEL_STOPPED":
                state["resume_action"] = {"name": failed["action"], "arguments": failed["arguments"]}
            state["status"], state["reason_code"] = "running", None
            state.pop("reason", None)
            return self._drive(state, owner)
        finally:
            self.store.save(state, owner, release=True)

    def _execute(self, run_id):
        owner, state = self.store.claim(run_id)
        try:
            return self._drive(state, owner)
        finally:
            self.store.save(state, owner, release=True)

    def _drive(self, state, owner):
        missing = [key for key in ("budget", "deadline") if state["input"][key] is None]
        state["missing_fields"] = missing
        if missing:
            state["status"], state["reason_code"] = "needs_input", "MISSING_FIELDS"
            state["reason"] = "请补齐：" + ", ".join(missing)
            return state
        if datetime.fromisoformat(state["input"]["deadline"]) <= self.clock() and not state.get("saved_plan"):
            state["status"], state["reason_code"] = "incomplete", "DEADLINE_PASSED"
            state["reason"] = "截止时间已过，停止后续收费工具；此前成功结果和费用保留。"
            return state
        try:
            model = self.model_override or (Mock() if state["provider"] == "mock" else DeepSeek())
            if state["provider"] == "deepseek" and not self.model_override:
                if model.model != state["model_config"]["model"] or model.base != state["model_config"]["base_url"].rstrip("/"):
                    raise ConfigurationError("Resume requires the original DEEPSEEK_MODEL and DEEPSEEK_BASE_URL")
        except ConfigurationError as exc:
            state["status"], state["reason_code"], state["reason"] = "incomplete", "CONFIGURATION_ERROR", str(exc)
            return state
        while state["status"] == "running":
            if not state.get("pending"):
                if any(state["counts"][k] >= state["limits"][k] for k in ("decisions", "tools")):
                    state["status"], state["reason_code"] = "incomplete", "LIMIT_REACHED"
                    state["reason"] = "累计模型决策或工具调用次数达到上限；已成功产物仍保留。"
                    break
                state["counts"]["decisions"] += 1
                self.store.save(state, owner)
                try:
                    action, assistant, metadata = model.choose(copy.deepcopy(state))
                    action = Action.model_validate(action)
                except (ModelError, ValidationError) as exc:
                    state["status"], state["reason_code"], state["reason"] = "incomplete", "MODEL_ERROR", str(exc)
                    state["trace"].append({"sequence": len(state["trace"])+1, "timestamp": self.clock().isoformat(),
                        "action": "model_decision", "arguments": {}, "result": {"status": "failed", "summary": str(exc)},
                        "next_action": "resume_or_stop", "reason_summary": "模型未返回可执行动作，保存现场。"})
                    break
                state.pop("resume_action", None)
                state["counts"]["tools"] += 1
                state["pending"] = {"action": action.model_dump(mode="json"), "assistant": assistant, "metadata": metadata}
                # The pending intent is durable before execution. Recovery replays it;
                # local tools have no effects outside the final atomic SQLite checkpoint.
                self.store.save(state, owner)
            self._perform(state, owner)
        state["updated_at"] = self.clock().isoformat()
        if state["trace"] and state["status"] != "running" and state["trace"][-1].get("next_action") is None:
            state["trace"][-1]["next_action"] = "end"
        return state

    def _perform(self, state, owner):
        pending = state["pending"]
        action = Action.model_validate(pending["action"])
        name, raw_args = action.name, action.arguments
        before_cost = spent(state)
        prior_steps = copy.deepcopy(state["steps"])
        working = copy.deepcopy(state)
        try:
            args = PARAMS[name].model_validate(raw_args).model_dump(mode="json")
            fault = state["faults"].get(name)
            if fault and (fault["mode"] == "always" or fault["used"] == 0):
                fault["used"] += 1
                raise ToolError("INJECTED_FAILURE", f"Injected pre-commit {name} failure", True)
            result = execute(working, name, args, self.clock())
            # Replace only after successful tool execution; failed tools cannot leak mutations.
            state.clear()
            state.update(working)
        except ValidationError:
            result = {"status": "failed", "error_code": "INVALID_ARGUMENTS", "retryable": True,
                      "summary": "Arguments do not match tool schema; choose corrected arguments"}
        except ToolError as exc:
            result = {"status": "failed", "error_code": exc.code, "retryable": exc.retryable, "summary": exc.message}
            if exc.code == "INJECTED_FAILURE":
                count = 1 + sum(e.get("action") == name and e.get("plan_version") == state.get("plan", {}).get("version", 0)
                                and e.get("result", {}).get("status") == "failed" for e in state["trace"])
                result["failures_in_plan"] = count
                result["retryable"] = exc.retryable and count < 2
                if count >= 2:
                    result["summary"] += "; repeated failures: stop and retain artifacts for resume after service recovery"
        except Exception:
            result = {"status": "failed", "error_code": "TOOL_INTERNAL_ERROR", "retryable": False,
                      "summary": "Local tool raised an unexpected error; no changes committed"}
        status_key = {"search_materials": "search", "revise_plan": "select", "estimate_plan": "estimate",
                      "generate_script": "script", "save_plan": "save"}.get(name)
        if result["status"] == "failed" and status_key:
            if state["steps"].get(status_key, {}).get("status") != "succeeded":
                state["steps"][status_key] = {**state["steps"].get(status_key, {"depends_on": [], "artifact_id": None}),
                                            "status": "failed", "error_code": result["error_code"]}
        result["charged_units"] = money(spent(state)-before_cost)
        event = {
            "sequence": len(state["trace"])+1, "timestamp": self.clock().isoformat(),
            "plan_version": state.get("plan", {}).get("version", 0),
            "action": name, "arguments": raw_args, "reason_summary": action.reason_summary,
            "result": result, "spent_total": money(spent(state)),
            "step_changes": {k: v for k, v in state["steps"].items() if prior_steps.get(k) != v},
            "model": pending["metadata"], "next_action": None,
        }
        if state["trace"]:
            state["trace"][-1]["next_action"] = name
        state["trace"].append(event)
        assistant = pending["assistant"]
        state["messages"].append(assistant)
        state["messages"].append({"role": "tool", "tool_call_id": assistant["tool_calls"][0]["id"],
                                   "content": json.dumps(result, ensure_ascii=False)})
        state.pop("pending", None)
        if state["status"] != "running":
            event["next_action"] = "end"
        if state["status"] == "completed":
            state.pop("reason", None)
        state["updated_at"] = self.clock().isoformat()
        self.store.save(state, owner)


def public_result(state):
    result = {k: state.get(k) for k in (
        "run_id", "provider", "model_config", "status", "reason_code", "reason", "input", "missing_fields",
        "plan", "plan_history", "steps", "script", "archived_scripts", "estimate", "saved_plan", "ledger",
        "counts", "limits", "resumptions", "created_at", "updated_at",
    )}
    result["spent_total"] = money(spent(state))
    result["budget_remaining"] = None
    if state["input"]["budget"] is not None:
        result["budget_remaining"] = money(Decimal(state["input"]["budget"])-spent(state))
    if state.get("plan") and all(state["input"][k] is not None for k in ("budget", "deadline")):
        # For reproducible evidence, report at last checkpoint time, not at query time.
        result["completion_estimate"] = quote(state, datetime.fromisoformat(state["updated_at"]))
    result["trace_count"] = len(state["trace"])
    has_real = any(e.get("model", {}).get("provider") == "deepseek" for e in state["trace"])
    result["evidence_boundary"] = ("offline_mock_flow_only" if state["provider"] == "mock" else
                                   "real_model_tool_decisions" if has_real else "no_real_model_decision_evidence")
    return result
