"""Run from repository root: python -m src.cli --help."""
import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from src.agent import ROOT, Runner, public_result
from src.storage import Conflict, Store


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def export_evidence(directory, state):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    write_json(path/"input.json", state["input"])
    write_json(path/"output.json", public_result(state))
    (path/"trace.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False)+"\n" for e in state["trace"]), encoding="utf-8")
    if state.get("saved_plan"):
        write_json(path/"plan.json", state["saved_plan"])


def markdown_plan(doc):
    rows = "\n".join(f"| {x['label']} | {x['amount']} |" for x in doc["ledger"])
    steps = "\n".join(f"{i}. {text}" for i, text in enumerate(doc["execution_steps"], 1))
    return f"""# {doc['theme']}

方案 ID：{doc['plan_id']}

目标：{doc['plan']['objective']}。渠道：{doc['plan']['channel']}。

素材：{doc['material']['name']}（{doc['material']['id']}）

## 内容脚本

### {doc['script']['title']}

{doc['script']['body']}

## 执行步骤

{steps}

## 费用

| 项目 | 模拟单位 |
| --- | ---: |
{rows}

总计：{doc['total_cost']}；预算余额：{doc['budget_remaining']}。

截止时间：{doc['deadline']}。保存时间：{doc['saved_at']}。

{doc['notice']}
"""


def parser():
    p = argparse.ArgumentParser(description="内容活动规划 Agent（CLI / DeepSeek / 离线 Mock）")
    p.add_argument("--db", default=str(ROOT/".runtime"/"agent.sqlite3"), help="SQLite path (before subcommand)")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="create a task; repeated request key does not execute again")
    run.add_argument("--provider", choices=["mock", "deepseek"], required=True)
    run.add_argument("--input", required=True)
    run.add_argument("--request-key", required=True)
    run.add_argument("--fault", help="evaluation only: save_plan:once, generate_script:always, etc.")
    run.add_argument("--max-decisions", type=int, default=20)
    run.add_argument("--max-tool-calls", type=int, default=20)
    run.add_argument("--output", help="evidence directory")
    for name in ("show", "trace", "resume", "export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_id")
        if name == "resume":
            cmd.add_argument("--budget")
            cmd.add_argument("--deadline")
            cmd.add_argument("--output")
        if name == "trace":
            cmd.add_argument("--output", help="JSONL destination")
        if name == "export":
            cmd.add_argument("--format", choices=["json", "markdown"], default="markdown")
            cmd.add_argument("--output", required=True)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        store = Store(args.db)
        if args.command == "run":
            payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
            print(f"Executing provider={args.provider}; request_key={args.request_key}", file=sys.stderr)
            state = Runner(store).create(payload, args.provider, args.request_key, args.fault,
                                         args.max_decisions, args.max_tool_calls)
        elif args.command == "resume":
            state = Runner(store).resume(args.run_id, args.budget, args.deadline)
        else:
            state = store.get(args.run_id)
        if args.command == "trace":
            if args.output:
                dest = Path(args.output)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("".join(json.dumps(e, ensure_ascii=False)+"\n" for e in state["trace"]), encoding="utf-8")
            print(json.dumps(state["trace"], ensure_ascii=False, indent=2))
            return 0
        if args.command == "export":
            if not state.get("saved_plan"):
                print(json.dumps({"error": "NO_SAVED_PLAN", "run_id": state["run_id"]}))
                return 4
            doc = store.plan(state["saved_plan"]["plan_id"])
            if args.format == "json":
                write_json(args.output, doc)
            else:
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output).write_text(markdown_plan(doc), encoding="utf-8")
            print(json.dumps({"plan_id": doc["plan_id"], "output": str(Path(args.output).resolve())}))
            return 0
        if getattr(args, "output", None):
            export_evidence(args.output, state)
        print(json.dumps(public_result(state), ensure_ascii=False, indent=2))
        if args.command == "show":
            return 0
        if state["reason_code"] == "CONFIGURATION_ERROR":
            return 5
        return {"completed": 0, "needs_input": 3}.get(state["status"], 4)
    except Conflict as exc:
        print(json.dumps({"error": "CONFLICT", "message": str(exc)}, ensure_ascii=False))
        return 6
    except (ValidationError, ValueError, KeyError, FileNotFoundError) as exc:
        # Validation messages can contain user inputs, but never model credentials.
        print(json.dumps({"error": "INVALID_INPUT", "message": str(exc)}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"error": "INTERNAL_ERROR", "message": "Unexpected local error; checkpoint retained if available"}))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
