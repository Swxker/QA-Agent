# 内容任务规划 Agent

帮助运营人员在预算和截止时间内生成并保存一份内容活动方案。采用 **CLI** 运行，模型供应商为 **DeepSeek**，使用 Python 原生决策循环。

**自动测试 51 项通过，真实模型的 14 个验收场景均符合预期。** 真实运行覆盖预算 12→8 的重规划、工具失败恢复、重复执行和续跑。Mock 与真实模型运行记录分别保存，便于核对验证范围。

## 评审入口

| 评审内容 | 入口 |
| --- | --- |
| 需求与成功条件 | [docs/01-requirements.md](docs/01-requirements.md) |
| 架构、决策、费用、状态和续跑 | [docs/02-solution.md](docs/02-solution.md) |
| 测试预期、实际结果与限制 | [docs/03-evaluation.md](docs/03-evaluation.md) |
| AI 辅助范围与个人贡献 | [docs/04-ai-usage.md](docs/04-ai-usage.md) |
| 复盘与实际用时 | [docs/05-retrospective.md](docs/05-retrospective.md) |
| 实际离线运行总表 | [examples/mock/summary.json](examples/mock/summary.json) |
| 真实 DeepSeek 两条核心路径 | [examples/real/summary.json](examples/real/summary.json) |
| 真实模型 14 个验收场景记录 | [examples/real-full/summary.json](examples/real-full/summary.json) |
| 最新自动测试结果 | [examples/test-results-latest.txt](examples/test-results-latest.txt) |
| 超预算调整路径 | [examples/mock/budget-replan/trace.jsonl](examples/mock/budget-replan/trace.jsonl) |
| 保存故障路径 | [examples/mock/tool-failure/trace.jsonl](examples/mock/tool-failure/trace.jsonl) |
| 自动测试结果 | [examples/test-results.txt](examples/test-results.txt) |
| 缺少模型配置的异常输出 | [examples/real-unavailable/output.json](examples/real-unavailable/output.json) |

## 环境与安装

需要 Python 3.11+ 和 pip，SQLite 随 Python 提供。DeepSeek 模式需要 API key 和网络连接；离线 Mock 无需密钥。

在仓库根目录打开终端。Windows PowerShell 安装命令：

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux 安装命令：

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

确认版本输出为 Python 3.11 或更高。下文命令采用 PowerShell 语法，直接调用虚拟环境解释器，无需激活环境；macOS / Linux 的解释器路径为 `.venv/bin/python`，环境变量使用对应 shell 的设置语法。所有命令均从仓库根目录执行。

## 离线运行

生成相对当前时间有效的输入（已提交的输出是历史证据，不应用其旧截止时间长期复跑）：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_examples.py --output examples\inputs
.\.venv\Scripts\python.exe -m src.cli run --provider mock --input examples\inputs\budget-replan.json --request-key mock-budget-demo --output examples\manual-budget
```

预算 10 时，Mock 先估算 12，观察超预算后改选 8 的方案，最终保存。查看 `output.json`、`trace.jsonl`、`plan.json`。Mock 脚本为模板文本，不能证明生成质量或真实模型决策。

一条命令执行全部离线场景；每次使用独立数据库和新截止时间，不受此前运行影响：

```powershell
.\.venv\Scripts\python.exe scripts\run_examples.py --provider mock --output examples\mock
.\.venv\Scripts\python.exe -m pytest -q
```

## DeepSeek 真实运行

`.env.example` 仅说明环境变量，程序不自动加载 `.env`。在当前 shell 配置，勿提交真实密钥：

```powershell
$env:DEEPSEEK_API_KEY = "替换为你的密钥"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-flash"
.\.venv\Scripts\python.exe scripts\prepare_examples.py --output examples\inputs
.\.venv\Scripts\python.exe -m src.cli run --provider deepseek --input examples\inputs\budget-replan.json --request-key real-budget-demo --output examples\real\budget-replan
.\.venv\Scripts\python.exe -m src.cli run --provider deepseek --input examples\inputs\normal.json --request-key real-save-failure-demo --fault save_plan:once --output examples\real\save-failure
```

模型名可按账户可用模型调整。接口使用非 thinking 模式和 Function Calling，工具选择由模型产生，本地执行器回传真实结果。默认基地址和模型名依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/tool_calls/)；参数详见 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

同时运行并检验两条真实路径：

```powershell
.\.venv\Scripts\python.exe scripts\run_examples.py --provider deepseek --output examples\real
```

执行全部 14 个真实模型验收场景：

```powershell
.\.venv\Scripts\python.exe scripts\run_examples.py --provider deepseek --all-scenarios --output examples\real-full
.\.venv\Scripts\python.exe scripts\audit_evidence.py --directory examples\real-full
```

14 个场景分别是：正常完成、超预算后调整方案、预算不足且无可行方案、缺少预算、缺少截止时间、无匹配素材、素材不完整、时间不足但可调整、时间不足且无法完成、工具一次失败、工具持续失败、重复执行、故障恢复后续跑、达到调用上限。各场景的输入条件、预期行为及实际结果见 [14 个验收场景说明](docs/03-evaluation.md#14-个验收场景说明)。

“14/14 通过”表示 14 个场景的行为均符合预期；对于缺少信息、预算不足或持续故障的场景，正确结果是等待补充或返回未完成。

`audit_evidence.py` 独立核对账本、Trace 收费、重复 Key、预算、调用上限，以及重复请求和续跑的产物一致性，并汇总去重后的 token 用量。token 用量不是实际货币账单。可用 `--cases resume` 对单个场景复验，建议输出到新目录保留此前证据。

脚本检查是否实际观察到 `12 → 8`、是否记录故障且保留脚本；不满足时退出 4，并保留实际结果。模型可能直接选便宜方案，这样虽可完成业务，也没有证明该次运行发生预算重规划，检查会如实失败。没有密钥、鉴权失败或模型不可用时不会自动切换 Mock。

## 输入与约束

```json
{
  "theme": "秋季新品推广，优先评估高质感短视频，超预算可改用轻量图文",
  "budget": "10",
  "deadline": "2030-01-02T18:00:00+08:00"
}
```

`theme` 必填且非空；预算为非负、最多两位小数的模拟单位；截止时间必须带时区。预算或截止时间缺失时保存 `needs_input` 任务，不调用模型及收费工具。未知字段、负预算、无时区时间属于输入错误。

## 查询、重复执行、续跑与导出

完整可复制示例（从 JSON 输出取得任务 ID）：

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py scripts\prepare_examples.py --output examples\inputs
$task = (& $py -m src.cli run --provider mock --input examples\inputs\missing-budget.json --request-key missing-budget-demo | ConvertFrom-Json)
$task.status
& $py -m src.cli resume $task.run_id --budget 10 --output examples\manual-resumed
& $py -m src.cli show $task.run_id
& $py -m src.cli trace $task.run_id --output examples\manual-resumed\trace.jsonl
& $py -m src.cli export $task.run_id --format markdown --output examples\manual-resumed\plan.md
```

同一 `--request-key` 和相同输入、provider、规则及执行选项，只返回已有任务，不再次执行。相同 Key 不同输入退出 6。更换 Key 可启动新任务；重新生成截止时间后也应更换 Key。`resume` 是显式继续工作，区别于重复 `run`；仅允许补齐原先缺失的预算或时间，不能修改既有约束。已完成任务重复续跑无副作用。

默认数据库为 `.runtime/agent.sqlite3`。自定义数据库参数必须位于子命令前：

```powershell
.\.venv\Scripts\python.exe -m src.cli --db .runtime\demo.sqlite3 run --provider mock --input examples\inputs\normal.json --request-key isolated-demo
```

## 故障与执行上限

```powershell
.\.venv\Scripts\python.exe -m src.cli run --provider mock --input examples\inputs\budget-replan.json --request-key failure-demo --fault save_plan:once --output examples\manual-failure
.\.venv\Scripts\python.exe -m src.cli run --provider mock --input examples\inputs\budget-replan.json --request-key limit-demo --max-decisions 3 --max-tool-calls 3 --output examples\manual-limit
```

故障参数仅用于评估，支持四个业务工具的 `:once` / `:always`，状态会持久化。一次失败默认提交前发生、不扣费；失败 attempt 保留，重试新增记录。持续故障恢复的演示由评估脚本明确记录 `evaluation_restore_service`，不是模型虚构的成功步骤。

默认累计最多 20 次模型决策、20 次函数调用（包含计划和结束控制动作），可配置 1–100。续跑不重置上限。网络请求每轮最多尝试两次，仅瞬态错误重试，单次超时 30 秒；真实 API 超时重试可能产生额外 token 费用，不属于业务模拟账本。

## 输出与退出码

标准输出为 UTF-8 JSON，标准错误为简短进度。`show` 是最后检查点的快照，不重新执行或重新报价。`trace` 为 JSON 数组，文件导出为 JSONL。`--output` 保存输入、结果、Trace 和成功方案，权威状态始终在 SQLite。

| 退出码 | 含义 |
| --- | --- |
| 0 | 任务完成，或查询/导出成功 |
| 2 | 输入/命令错误、任务不存在 |
| 3 | 缺少预算或截止时间 |
| 4 | 未完成：不可行、故障、模型异常或次数上限等 |
| 5 | 配置错误或程序内部异常 |
| 6 | 幂等冲突、并发执行或租约丢失 |

SQLite 实现单机并发互斥，进程硬退出后租约最多 180 秒过期；旧执行者不能提交。当前工具的效果全部在本地 SQLite 内提交，未来接入外部收费工具需另加供应商幂等与对账。

## 提交信息

代码、文档、合成数据和运行证据位于同一仓库。提交信息包括题号、GitHub URL 和最终 commit SHA。`.runtime`、`.env` 和真实密钥不纳入版本控制。
