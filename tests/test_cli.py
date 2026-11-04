import json
import subprocess
import sys
from pathlib import Path

from scripts.prepare_examples import prepare


ROOT = Path(__file__).resolve().parents[1]


def call(db, *args):
    proc = subprocess.run([sys.executable, "-m", "src.cli", "--db", str(db), *map(str, args)],
                          cwd=ROOT, capture_output=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


def test_cli_run_repeat_show_trace_export(tmp_path):
    prepare(tmp_path/"inputs")
    db = tmp_path/"cli.sqlite3"
    args = ("run", "--provider", "mock", "--input", tmp_path/"inputs"/"budget-replan.json", "--request-key", "same")
    code, state = call(db, *args, "--output", tmp_path/"evidence")
    assert code == 0 and state["spent_total"] == "8.00"
    assert (tmp_path/"evidence"/"trace.jsonl").exists()
    assert call(db, *args) == (code, state)
    assert call(db, "show", state["run_id"])[1] == state
    code, events = call(db, "trace", state["run_id"], "--output", tmp_path/"trace.jsonl")
    assert code == 0 and events[-1]["action"] == "finish"
    code, result = call(db, "export", state["run_id"], "--output", tmp_path/"plan.md")
    assert code == 0
    assert "8.00" in (tmp_path/"plan.md").read_text(encoding="utf-8")


def test_cli_missing_resume_and_exit_codes(tmp_path):
    prepare(tmp_path/"inputs")
    db = tmp_path/"cli.sqlite3"
    code, state = call(db, "run", "--provider", "mock", "--input", tmp_path/"inputs"/"missing-budget.json", "--request-key", "missing")
    assert code == 3
    assert call(db, "export", state["run_id"], "--output", tmp_path/"missing.md")[0] == 4
    assert call(db, "resume", state["run_id"], "--budget", "10")[0] == 0
    assert call(db, "run", "--provider", "mock", "--input", tmp_path/"inputs"/"normal.json", "--request-key", "missing")[0] == 6
    assert call(db, "show", "unknown")[0] == 2
    assert call(db, "run", "--provider", "mock", "--input", tmp_path/"inputs"/"budget-impossible.json", "--request-key", "impossible")[0] == 4
