import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.agent import Runner, public_result
from src.models import Mock, ModelError
from src.schemas import Action
from src.storage import Conflict, Store
from src.tools import ToolError, execute, quote, spent

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path/"agent.sqlite3")
    return store, Runner(store, clock=lambda: NOW)


def payload(budget="10", minutes=1440, theme="秋季新品推广"):
    return {"theme": theme, "budget": budget, "deadline": (NOW+timedelta(minutes=minutes)).isoformat()}


def seed(rig, budget="10"):
    return rig[1].create(payload(budget), request_key="seed", max_tools=1)


def select(state, material="basic"):
    execute(state, "revise_plan", {"material_id": material, "objective": "目标", "channel": "图文"}, NOW)


def generate(state):
    return execute(state, "generate_script", {"title": "新品活动", "body": "展示新品使用场景和产品特点，邀请用户留言分享使用体验并参与互动。",
                                               "material_ids": [state["plan"]["material_id"]]}, NOW)


def test_normal_and_replanning(rig):
    normal = rig[1].create(payload("20"), request_key="normal")
    changed = rig[1].create(payload("10"), request_key="changed")
    assert normal["status"] == changed["status"] == "completed"
    assert spent(normal) == Decimal(12)
    assert spent(changed) == Decimal(8)
    quotes = [e["result"]["completion_total"] for e in changed["trace"] if e["action"] == "estimate_plan"]
    assert quotes == ["12.00", "8.00", "8.00"]
    assert changed["plan"]["version"] == 2
    assert len(changed["ledger"]) == 3
    assert rig[0].plan(changed["saved_plan"]["plan_id"]) == changed["saved_plan"]


@pytest.mark.parametrize("missing", ["budget", "deadline"])
def test_missing_input_never_calls_model_or_tools(rig, missing):
    data = payload()
    del data[missing]
    state = rig[1].create(data, request_key=missing)
    assert state["status"] == "needs_input"
    assert state["missing_fields"] == [missing]
    assert state["counts"] == {"decisions": 0, "tools": 0}
    assert spent(state) == 0
    filled = rig[1].resume(state["run_id"], **{missing: payload()[missing]})
    assert filled["status"] == "completed"
    assert spent(filled) == 8


@pytest.mark.parametrize("data", [payload("-1"), payload("NaN"), payload("Infinity"), payload("1.001"),
                                  {**payload(), "deadline": "2030-01-01T10:00:00"}, {**payload(), "theme": "   "},
                                  {**payload(), "unknown": True}])
def test_reject_invalid_inputs(rig, data):
    with pytest.raises(ValidationError):
        rig[1].create(data)


def test_past_deadline_stops_before_model(rig):
    state = rig[1].create(payload(minutes=-1))
    assert state["reason_code"] == "DEADLINE_PASSED"
    assert state["counts"]["tools"] == 0


def test_budget_impossible_has_gap_no_spend_no_save(rig):
    state = rig[1].create(payload("7"))
    assert state["status"] == "incomplete"
    assert public_result(state)["completion_estimate"]["budget_gap"] == "1.00"
    assert not state.get("saved_plan")
    assert spent(state) == 0


@pytest.mark.parametrize("minutes,expected", [(60, "completed"), (5, "incomplete")])
def test_deadline_changes_choice(rig, minutes, expected):
    state = rig[1].create(payload("20", minutes))
    assert state["status"] == expected
    assert state["plan"]["material_id"] == "basic"
    if expected == "incomplete":
        assert public_result(state)["completion_estimate"]["time_gap_minutes"] > 0


@pytest.mark.parametrize("theme,partial", [("深海科学展览", False), ("公益活动", True)])
def test_insufficient_materials(rig, theme, partial):
    state = rig[1].create(payload(theme=theme))
    assert state["status"] == "incomplete"
    assert state["trace"][0]["result"]["status"] == "partial"
    assert spent(state) == 0
    if partial:
        assert state["estimate"]["missing_coverage"] == ["brand"]


@pytest.mark.parametrize("tool", ["search_materials", "estimate_plan", "generate_script", "save_plan"])
def test_one_failure_is_observed_and_success_is_preserved(rig, tool):
    state = rig[1].create(payload(), fault=tool+":once")
    assert state["status"] == "completed"
    errors = [e for e in state["trace"] if e["result"].get("error_code") == "INJECTED_FAILURE"]
    assert len(errors) == 1
    assert errors[0]["result"]["status"] == "failed"
    assert errors[0]["result"]["charged_units"] == "0.00"
    assert errors[0]["next_action"] == tool
    assert spent(state) == 8
    assert len([e for e in state["trace"] if e["action"] == "generate_script" and e["result"]["status"] == "succeeded"]) == 1


def test_persistent_failure_and_resume(rig):
    store, runner = rig
    state = runner.create(payload(), fault="save_plan:always")
    assert state["status"] == "incomplete"
    assert state["steps"]["save"]["status"] == "failed"
    assert spent(state) == 2
    failed_attempts = [e["result"] for e in state["trace"] if e.get("result", {}).get("error_code") == "INJECTED_FAILURE"]
    assert [e["failures_in_plan"] for e in failed_attempts] == [1, 2]
    assert [e["retryable"] for e in failed_attempts] == [True, False]
    script = state["script"]
    # Simulate external service restoration, without changing artifacts or counters.
    owner, recovered = store.claim(state["run_id"])
    recovered["faults"]["save_plan"]["mode"] = "once"
    store.save(recovered, owner, release=True)
    resumed = Runner(store, clock=lambda: NOW).resume(state["run_id"])
    assert resumed["status"] == "completed"
    assert resumed["script"] == script
    assert spent(resumed) == 8
    assert len([x for x in resumed["ledger"] if x["label"] == "script generation"]) == 1
    assert resumed["counts"]["tools"] > state["counts"]["tools"]


def test_request_idempotency_and_conflict(rig):
    store, runner = rig
    state = runner.create(payload(), request_key="same")
    assert runner.create(payload(), request_key="same") == state
    assert runner.resume(state["run_id"]) == state
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    with pytest.raises(Conflict):
        runner.create(payload("9"), request_key="same")


def test_resume_cannot_change_constraints(rig):
    state = rig[1].create(payload("7"))
    with pytest.raises(ValueError):
        rig[1].resume(state["run_id"], budget="10")
    assert rig[0].get(state["run_id"])["input"]["budget"] == "7"


@pytest.mark.parametrize("limit", ["max_tools", "max_decisions"])
def test_limit_is_cumulative_across_resume(rig, limit):
    state = rig[1].create(payload(), **{limit: 2})
    assert state["reason_code"] == "LIMIT_REACHED"
    assert rig[1].resume(state["run_id"]) == state


def test_dependencies_and_cost_guards_cannot_be_bypassed(rig):
    state = seed(rig)
    for name in ("save_plan", "finish"):
        with pytest.raises(ToolError):
            execute(state, name, {}, NOW)
    select(state, "premium")
    with pytest.raises(ToolError, match="estimate_plan"):
        generate(state)
    execute(state, "estimate_plan", {}, NOW)
    with pytest.raises(ToolError) as error:
        generate(state)
    assert error.value.code == "PLAN_INFEASIBLE"
    assert spent(state) == 0


def test_fresh_estimate_required_and_sunk_costs_retained(rig):
    state = seed(rig, "20")
    select(state)
    execute(state, "estimate_plan", {}, NOW)
    generate(state)
    with pytest.raises(ToolError) as error:
        execute(state, "save_plan", {}, NOW)
    assert error.value.code == "ESTIMATE_REQUIRED"
    select(state, "premium")
    assert not state.get("script")
    assert len(state["archived_scripts"]) == 1
    assert spent(state) == 2
    assert quote(state, NOW)["completion_total"] == "14.00"


def test_deadline_rechecked_before_charge(rig):
    state = seed(rig)
    select(state)
    execute(state, "estimate_plan", {}, NOW)
    with pytest.raises(ToolError) as error:
        execute(state, "generate_script", {"title": "x", "body": "x"*30, "material_ids": ["basic"]}, NOW+timedelta(days=2))
    assert error.value.code == "PLAN_INFEASIBLE"
    assert spent(state) == 0


def test_tool_level_success_reuse_does_not_charge_again(rig):
    state = rig[1].create(payload())
    before = copy.deepcopy(state["ledger"])
    assert execute(state, "save_plan", {}, NOW)["reused"]
    assert generate(state)["reused"]
    assert state["ledger"] == before


def test_successful_step_dependencies_form_a_dag(rig):
    state = rig[1].create(payload())
    steps = state["steps"]
    def visit(key, path):
        assert key not in path, "cyclic step dependencies"
        assert steps[key]["status"] == "succeeded"
        for dependency in steps[key]["depends_on"]:
            visit(dependency, path+[key])
    visit("save", [])


def test_lock_and_fencing_prevent_concurrent_commit(rig):
    store, runner = rig
    state = runner.create({"theme": "x"})
    owner, state = store.claim(state["run_id"])
    with pytest.raises(Conflict):
        store.claim(state["run_id"])
    with store.connect() as conn:
        conn.execute("UPDATE runs SET lease=0 WHERE id=?", (state["run_id"],))
    new_owner, fresh = store.claim(state["run_id"])
    with pytest.raises(Conflict):
        store.save(state, owner)
    store.save(fresh, new_owner, release=True)


class FailModel:
    def choose(self, state):
        raise ModelError("provider unavailable")


def test_model_error_keeps_state_and_can_resume(rig):
    store, _ = rig
    state = Runner(store, clock=lambda: NOW, model=FailModel()).create(payload())
    assert state["reason_code"] == "MODEL_ERROR"
    assert state["trace"][0]["result"]["status"] == "failed"
    assert spent(state) == 0
    assert rig[1].resume(state["run_id"])["status"] == "completed"


def test_missing_deepseek_key_never_falls_back(rig, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    state = rig[1].create(payload(), provider="deepseek")
    assert state["reason_code"] == "CONFIGURATION_ERROR"
    assert not state["trace"]
    assert public_result(state)["evidence_boundary"] == "no_real_model_decision_evidence"


class MaliciousModel:
    def choose(self, state):
        action = Action(name="save_plan", arguments={"invented": True}, reason_summary="invalid")
        return action, {"role": "assistant", "content": None, "tool_calls": [{"id": "bad", "type": "function", "function": {
            "name": "save_plan", "arguments": "{}"}}]}, {"provider": "mock"}


def test_bad_model_arguments_cannot_create_success(rig):
    state = Runner(rig[0], clock=lambda: NOW, model=MaliciousModel()).create(payload(), max_tools=3)
    assert state["reason_code"] == "LIMIT_REACHED"
    assert all(e["result"]["status"] == "failed" for e in state["trace"])
    assert not state.get("saved_plan")
    assert spent(state) == 0


def test_unexpected_tool_failure_rolls_back_mutation(rig, monkeypatch):
    def broken_tool(state, *args):
        state["ledger"].append({"key": "bad", "amount": "99", "label": "must roll back"})
        raise RuntimeError("private diagnostic")
    monkeypatch.setattr("src.agent.execute", broken_tool)
    state = rig[1].create(payload(), max_tools=2)
    assert state["status"] == "incomplete"
    assert spent(state) == 0
    assert state["trace"][0]["result"]["error_code"] == "TOOL_INTERNAL_ERROR"
    assert "private diagnostic" not in json.dumps(state)


def test_interrupted_pending_action_is_replayed_once(rig):
    store, runner = rig
    state = runner.create(payload(), max_tools=1)
    owner, state = store.claim(state["run_id"])
    state["limits"]["tools"] = 20  # test setup only
    action, assistant, metadata = Mock().choose(state)
    state["counts"]["decisions"] += 1
    state["counts"]["tools"] += 1
    state["pending"] = {"action": action.model_dump(mode="json"), "assistant": assistant, "metadata": metadata}
    store.save(state, owner, release=True)
    resumed = runner.resume(state["run_id"])
    assert resumed["status"] == "completed"
    assert spent(resumed) == 8
    assert len([e for e in resumed["trace"] if e["action"] == "revise_plan"]) == 2
