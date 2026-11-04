import json

import httpx
import pytest

from src.models import DeepSeek, ModelError
from src.schemas import tool_schemas


def response(name="search_materials", args=None):
    return {"model": "test-model", "choices": [{"message": {"role": "assistant", "content": None,
        "reasoning_content": "MUST_NOT_PERSIST", "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args or {"reason_summary": "检索可用素材"})}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20}}


@pytest.fixture
def state():
    return {"messages": [{"role": "user", "content": "theme"}], "input": {"theme": "秋季新品"}}


def test_deepseek_payload_and_reasoning_not_persisted(monkeypatch, state):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        return httpx.Response(200, json=response())
    model = DeepSeek(httpx.Client(transport=httpx.MockTransport(handler)))
    action, assistant, metadata = model.choose(state)
    assert action.name == "search_materials"
    assert "MUST_NOT_PERSIST" not in json.dumps(assistant)
    assert metadata["usage"]["completion_tokens"] == 20
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert len(seen[0]["tools"]) == 7


@pytest.mark.parametrize("code,attempts", [(401, 1), (400, 1), (429, 2), (503, 2)])
def test_http_errors_are_bounded_and_redacted(monkeypatch, state, code, attempts):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, text="DO_NOT_LEAK_PROVIDER_BODY")
    model = DeepSeek(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError) as error:
        model.choose(state)
    assert len(calls) == attempts
    assert "DO_NOT_LEAK" not in str(error.value)
    assert "synthetic-test-key" not in str(error.value)


@pytest.mark.parametrize("body", [{"choices": []}, {}, response(args={"reason_summary": "x", "broken": 1})])
def test_invalid_response_or_arguments(monkeypatch, state, body):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    model = DeepSeek(httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))))
    if body.get("choices"):
        # Provider adapter parses action, tool executor separately validates tool args.
        action, _, _ = model.choose(state)
        assert "broken" in action.arguments
    else:
        with pytest.raises(ModelError):
            model.choose(state)


def test_schemas_require_brief_reason_not_chain_of_thought():
    for tool in tool_schemas():
        schema = tool["function"]["parameters"]
        assert "reason_summary" in schema["required"]
        assert schema["additionalProperties"] is False


@pytest.mark.parametrize("fault", [None, "save_plan:once"])
def test_full_protocol_loop_with_synthetic_http_responses(tmp_path, monkeypatch, fault):
    """Transport fixture, NOT real model reasoning evidence."""
    from datetime import datetime, timedelta, timezone
    from src.agent import Runner
    from src.storage import Store

    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    actions = [
        ("search_materials", {}),
        ("revise_plan", {"material_id": "premium", "objective": "新品推广", "channel": "短视频"}),
        ("estimate_plan", {}),
        ("revise_plan", {"material_id": "basic", "objective": "轻量推广", "channel": "图文"}),
        ("estimate_plan", {}),
        ("generate_script", {"title": "新品活动", "body": "通过新品使用场景引出活动主题，展示素材中的亮点并邀请用户评论互动。", "material_ids": ["basic"]}),
        ("estimate_plan", {}), ("save_plan", {}),
    ]
    if fault:
        actions.append(("save_plan", {}))
    actions.append(("finish", {}))
    requests = []
    def handler(request):
        index = len(requests)
        body = json.loads(request.content)
        requests.append(body)
        observations = [m for m in body["messages"] if m["role"] == "tool"]
        if index == 3:
            assert json.loads(observations[-1]["content"])["completion_total"] == "12.00"
        if index == 8 and fault:
            assert json.loads(observations[-1]["content"])["error_code"] == "INJECTED_FAILURE"
        # Every tool result must refer to a preceding assistant tool call.
        ids = {c["id"] for m in body["messages"] if m["role"] == "assistant" for c in m["tool_calls"]}
        assert all(m["tool_call_id"] in ids for m in observations)
        name, args = actions[index]
        data = response(name, {**args, "reason_summary": "Synthetic protocol fixture"})
        data["choices"][0]["message"]["tool_calls"][0]["id"] = f"test-{index}"
        return httpx.Response(200, json=data)
    model = DeepSeek(httpx.Client(transport=httpx.MockTransport(handler)))
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    state = Runner(Store(tmp_path/"test.sqlite3"), clock=lambda: now, model=model).create(
        {"theme": "新品推广", "budget": "10", "deadline": (now+timedelta(days=1)).isoformat()}, provider="deepseek", fault=fault)
    assert state["status"] == "completed"
    assert state["saved_plan"]["total_cost"] == "8.00"
    assert len(requests) == len(actions)
    assert len(state["ledger"]) == 3
