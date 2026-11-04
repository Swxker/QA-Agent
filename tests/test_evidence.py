import json
from datetime import datetime, timedelta, timezone

from scripts.audit_evidence import audit
from src.agent import Runner
from src.cli import export_evidence
from src.storage import Store


def test_audit_detects_tampered_trace_cost(tmp_path):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    payload = {"theme": "新品推广", "budget": "10", "deadline": (now+timedelta(days=1)).isoformat()}
    state = Runner(Store(tmp_path/"task.sqlite3"), clock=lambda: now).create(payload)
    export_evidence(tmp_path/"case", state)
    assert audit(tmp_path)["all_passed"]
    trace = tmp_path/"case"/"trace.jsonl"
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    events[-1]["result"]["charged_units"] = "99"
    trace.write_text("".join(json.dumps(e, ensure_ascii=False)+"\n" for e in events), encoding="utf-8")
    result = audit(tmp_path)
    assert not result["all_passed"]
    assert not result["cases"][0]["checks"]["trace_charges_match_ledger"]


def test_empty_evidence_cannot_pass(tmp_path):
    assert not audit(tmp_path)["all_passed"]
