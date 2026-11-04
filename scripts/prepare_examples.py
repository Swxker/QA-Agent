"""Generate nonexpired synthetic requests. Run from repository root."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def prepare(output):
    target = Path(output)
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=8)))
    normal = {"theme": "秋季新品推广", "budget": "20", "deadline": (now+timedelta(days=1)).isoformat()}
    cases = {
        "normal": normal,
        "budget-replan": {**normal, "budget": "10", "theme": "秋季新品推广，优先评估高质感短视频，超预算可改用轻量图文"},
        "budget-impossible": {**normal, "budget": "7"},
        "missing-budget": {k: v for k, v in normal.items() if k != "budget"},
        "missing-deadline": {k: v for k, v in normal.items() if k != "deadline"},
        "missing-materials": {**normal, "theme": "深海科学展览"},
        "partial-materials": {**normal, "theme": "公益活动"},
        "time-replan": {**normal, "deadline": (now+timedelta(minutes=60)).isoformat()},
        "time-impossible": {**normal, "deadline": (now+timedelta(minutes=5)).isoformat()},
    }
    for name, payload in cases.items():
        (target/f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return cases


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="examples/inputs")
    args = p.parse_args()
    prepare(args.output)
    print(json.dumps({"output": str(Path(args.output).resolve()), "cases": 9}))
