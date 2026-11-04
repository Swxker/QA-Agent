# 验证与已知限制

## 方法与环境

验证平台为 Windows，Python 版本为 3.12.7，依赖版本见 requirements.txt。跨平台运行和全新环境安装尚未单独验证。

自动测试使用 pytest；业务时间使用可控时钟，CLI 和示例运行使用实际时间。最新结果为 **51 项全部通过**，原文见 [test-results-latest.txt](../examples/test-results-latest.txt)。测试覆盖输入、四工具故障、费用、依赖 DAG、幂等、锁、续跑、模型异常、DeepSeek HTTP 协议、CLI 子进程及证据对账。早期 49 项测试记录保留在 [test-results.txt](../examples/test-results.txt)。

DeepSeek 传输测试用 httpx.MockTransport 与合成响应，覆盖完整 12→8 和保存失败两条协议链。这些测试验证 tool_call_id 对应、工具观察回传和状态记账，**不是实际 DeepSeek 决策证据**，不计入真实模型路径要求。

## 14 个验收场景说明

默认输入为“秋季新品推广”，预算 20 个模拟单位，截止时间为输入生成时刻的 24 小时后。下表列出各场景对默认条件的修改，以及真实 DeepSeek 运行结果；费用均为业务模拟费用。

| 场景与记录 | 输入条件或故障设置 | 预期行为 | 真实运行结果 |
| --- | --- | --- | --- |
| 1. [正常完成 normal](../examples/real-full/normal/output.json) | 使用默认输入，不注入故障 | 生成并保存预算、时间均可行的方案 | completed；选择轻量方案，费用 8 |
| 2. [超预算后调整方案 budget-replan](../examples/real-full/budget-replan/output.json) | 预算 10，优先评估高质感短视频，允许改用轻量图文 | 观察报价超预算后调整方案、重新估算并保存 | 首选估算 12，替代方案估算 8；completed，费用 8 |
| 3. [预算不足且无可行方案 budget-impossible](../examples/real-full/budget-impossible/output.json) | 预算降低为 7 | 说明预算缺口，不生成或保存不可行方案 | incomplete；最低方案估算 8，缺口 1，支出 0 |
| 4. [缺少预算 missing-budget](../examples/real-full/missing-budget/output.json) | 不提供 budget | 指明缺失字段，等待补充，不调用模型或收费工具 | needs_input；模型和工具调用均为 0，支出 0 |
| 5. [缺少截止时间 missing-deadline](../examples/real-full/missing-deadline/output.json) | 不提供 deadline | 指明缺失字段，等待补充，不调用模型或收费工具 | needs_input；模型和工具调用均为 0，支出 0 |
| 6. [无匹配素材 missing-materials](../examples/real-full/missing-materials/output.json) | 主题改为“深海科学展览”，资料库中无匹配素材 | 根据空检索结果停止，说明资料不足 | incomplete；未保存方案，支出 0 |
| 7. [素材不完整 partial-materials](../examples/real-full/partial-materials/output.json) | 主题改为“公益活动”，仅有产品配图，缺少品牌资料 | 保留已检索结果，说明缺失资料，不将部分成功当作任务完成 | incomplete；缺少 brand，支出 0 |
| 8. [时间不足但可调整 time-replan](../examples/real-full/time-replan/output.json) | 截止时间缩短为输入生成时刻的 60 分钟后 | 首选方案来不及时改选耗时更短的方案，并重新估算 | 从预计 101 分钟的方案改为预计 41 分钟的轻量方案；completed，费用 8 |
| 9. [时间不足且无法完成 time-impossible](../examples/real-full/time-impossible/output.json) | 截止时间缩短为输入生成时刻的 5 分钟后 | 尝试可用方案后说明时间缺口，停止执行 | incomplete；轻量方案仍需约 41 分钟，支出 0 |
| 10. [工具一次失败 tool-failure](../examples/real-full/tool-failure/output.json) | 使用预算 10 的输入，save_plan 首次调用在提交前失败 | 观察错误后选择恢复动作，保留已成功脚本，不重复收费 | 首次保存失败，重试成功；脚本仅生成一次，completed，费用 8 |
| 11. [工具持续失败 persistent-failure](../examples/real-full/persistent-failure/output.json) | 使用预算 10 的输入，每次 save_plan 均在提交前失败 | 有限尝试后停止，保留成功产物和真实失败记录 | 两次保存失败后停止；incomplete，脚本保留，支出 2 |
| 12. [重复执行 repeat](../examples/real-full/repeat/output.json) | 使用预算 10 的输入，相同 request-key 连续执行两次 | 第二次直接返回已有任务，不新增模型调用、费用或方案 | 两次结果快照相同；completed，累计费用仍为 8 |
| 13. 故障恢复后续跑：[恢复前](../examples/real-full/resume/output.json)、[恢复后 resume](../examples/real-full/resume-after/output.json) | 使用预算 10 的输入，先注入持续保存故障；任务停止后模拟服务恢复，再调用 resume | 保留原脚本，仅恢复必要步骤，费用和调用次数继续累计 | 从 incomplete、支出 2 恢复为 completed、总费用 8；同一脚本，累计 12 次调用 |
| 14. [达到调用上限 limit](../examples/real-full/limit/output.json) | 使用预算 10 的输入，工具调用次数上限设为 2 | 达到上限时明确停止，不误报完成 | incomplete，LIMIT_REACHED；工具调用 2 次，支出 0 |

**14/14 通过表示上述 14 个场景均符合预期，不表示生成了 14 份成功方案。** 信息不足应返回 needs_input，不可行或持续失败应返回 incomplete。费用、状态及调用次数见 [真实运行总表](../examples/real-full/summary.json)。

每个场景目录包含 input.json、output.json 和 trace.jsonl；保存成功后另有 plan.json。重复执行的第二份快照位于 repeat-second，续跑后的快照位于 resume-after；它们分别属于第 12、13 个场景，不额外计数。网络异常和缺少模型配置为补充验证记录，不计入上述 14 个场景。

## 实际离线运行

运行命令：`python scripts/run_examples.py --provider mock --output examples/mock`。14 个验收场景，外加重复请求和续跑后的快照；总表见 [summary.json](../examples/mock/summary.json)。每个目录均有 input.json、output.json、trace.jsonl，完成的任务另有 plan.json。

| 用例 | 预期 | 实际 | 证据目录 |
| --- | --- | --- | --- |
| 正常 | 保存，费用 12 | completed，12.00 | [normal](../examples/mock/normal/output.json) |
| 超预算调整 | 先 12，再 8，保存 | completed，8.00，计划版本 2 | [budget-replan](../examples/mock/budget-replan/trace.jsonl) |
| 无预算可行解 | 不保存，说明缺口 | incomplete，支出 0，最低方案缺 1 | [budget-impossible](../examples/mock/budget-impossible/output.json) |
| 缺预算 | 等待输入，零调用 | needs_input，0 次 | [missing-budget](../examples/mock/missing-budget/output.json) |
| 缺时间 | 等待输入，零调用 | needs_input，0 次 | [missing-deadline](../examples/mock/missing-deadline/output.json) |
| 无素材 | 不编造方案 | incomplete，支出 0 | [missing-materials](../examples/mock/missing-materials/trace.jsonl) |
| 部分素材 | 保留结果，拒绝不完整保存 | incomplete，缺 brand | [partial-materials](../examples/mock/partial-materials/output.json) |
| 时间可调整 | 改快方案后完成 | completed，basic，费用 8 | [time-replan](../examples/mock/time-replan/trace.jsonl) |
| 时间不可行 | 明确时间缺口 | incomplete，支出 0 | [time-impossible](../examples/mock/time-impossible/output.json) |
| 保存一次失败 | 留下失败记录，复用脚本 | completed，脚本只收费一次，总额 8 | [tool-failure](../examples/mock/tool-failure/trace.jsonl) |
| 保存持续失败 | 不伪报完成，保留脚本 | incomplete，脚本已完成，支出 2 | [persistent-failure](../examples/mock/persistent-failure/output.json) |
| 重复请求 | 不新增调用/费用/方案 | 两份快照完全相同 | [repeat](../examples/mock/repeat/output.json)、[repeat-second](../examples/mock/repeat-second/output.json) |
| 故障恢复续跑 | 只补做保存与结束 | incomplete 的 2 → completed 的 8 | [resume](../examples/mock/resume/output.json)、[resume-after](../examples/mock/resume-after/trace.jsonl) |
| 调用上限 | 明确停止 | incomplete，LIMIT_REACHED，2 次工具调用 | [limit](../examples/mock/limit/output.json) |

## 两条可审查的离线路径

预算调整：search→revise premium→estimate 12（不可行）→revise basic→estimate 8→generate（支出 2）→estimate（累计仍为 8）→save（支出增加 6）→finish。trace 中下一动作由实际后继事件填写。

保存故障：在相同流程到达 save 时，注入一次提交前失败，返回 failed、支出增加 0、累计 2；下一动作是重新 save，原脚本不变；保存后累计 8，最后 finish。失败 attempt 永久存在，不被后续成功覆盖。

这是 Mock 根据观察的固定策略，不能证明真实模型具备相同行为。真实模式的代码没有沿用 Mock 策略，下一动作来自 DeepSeek API。

## 已知限制

1. 模型可能直接选择便宜方案；业务成功与“该次证明预算重规划”分别判断。评估脚本要求实际观察到 12→8，未观察到则该项检查失败。
2. 素材检索采用关键词匹配，资料量有限，不支持多素材 coverage 合并、真实版权核查或语义检索。正文仅做结构与引用校验，未对营销质量、事实性或合规性进行专项评估。
3. 耗时为模拟可行性，不是真实内容生产实测。Trace 记录了返回模型和 token 用量，但单次验收不证明长期成功率；未将 token 用量换算为实际账单。
4. 单机 SQLite 和本地工具原子性不等于外部付费 API 的 exactly-once；模型请求超时可能消耗供应商 token。硬退出后需等待最长 180 秒租约过期。
5. 达到累计上限后不允许通过续跑重置额度；需要创建新任务。

## 2026-11-04 真实模型验收

实际执行 `python scripts/run_examples.py --provider deepseek --output examples/real`，两条路径全部 checks 为 true，见 [真实总表](../examples/real/summary.json)。返回模型为 deepseek-flash，provider 为 deepseek，均无 Mock 回退。

| 场景 | 实际行为 | 结果 |
| --- | --- | --- |
| 预算重规划 | 检索→选 premium→估算 12→模型选 basic→估算 8→生成→复核→保存→结束 | 9 次决策/调用，支出 8，completed |
| 工具失败恢复 | 保存首次失败（费用 0、累计 2）→模型选择重试保存→累计 8→结束 | 10 次决策/调用，脚本仅生成一次，completed |

另有一次预算 20 的补充故障恢复运行，首次保存失败后重试成功，支出 12；见 [save-failure](../examples/real/save-failure/output.json)。该补充运行不增加 14 个验收场景的计数。

对应 [预算 Trace](../examples/real/budget-replan/trace.jsonl)、[失败 Trace](../examples/real/tool-failure/trace.jsonl)。步骤、参数、结果与简短依据均来自实际运行，没有预填决策日志。

网络连接异常时，两条任务返回 MODEL_ERROR、业务费用 0；记录见 [网络异常用例](../examples/attempts/sandbox-network/summary.json)。恢复连接后重新执行成功。缺少模型配置时的异常输出见 [real-unavailable](../examples/real-unavailable/output.json)。

回归测试为 51 项通过，包含证据对账与篡改检测，原文见 [最新测试结果](../examples/test-results-latest.txt)。独立账本审计见 [核心路径 audit.json](../examples/real/audit.json)。

14 个真实模型验收场景首轮 13/14 符合预期，续跑场景未通过，原始证据完整保留在 [full-before-retry-guidance](../examples/attempts/full-before-retry-guidance/summary.json)。模型连续尝试保存，停止时已经使用 19 次调用；恢复服务后的第 20 次保存成功，但没有剩余额度执行 finish，故如实返回 LIMIT_REACHED，未重置额度或改写成功状态。

据此改进失败反馈与提示：明确同计划失败次数，第二次失败反馈 retryable=false，建议停止并保留产物待服务恢复。模型仍决定下一动作；再次执行全部 14 个验收场景验证改进效果，结果见 [完整真实总表](../examples/real-full/summary.json)。

改进后的 14 个真实模型验收场景 **14/14 检查通过**：持续保存故障在两次失败后主动停止，保留费用 2 和脚本；服务恢复后续跑，仅保存并结束，累计 12 次调用、总费用 8，没有重新生成脚本。重复请求两份快照完全相同。正常场景模型直接选择 basic，费用 8；与 Mock 首选 premium 的行为不同，记录按实际保留。

独立审计 [real-full/audit.json](../examples/real-full/audit.json) 所有检查通过，覆盖 14 个唯一任务及重复/续跑快照。全部 14 个验收场景去重后的供应商返回用量为输入 282233、输出 11329、合计 293562 tokens；不包含核心两路径和首次未全部通过的验收运行，初次真实验收及修复复验记录区间内的用量合计另见 [real-session.json](../examples/real-session.json)，这些均不等于实际账单。

## 后续复验命令

执行 `python scripts/run_examples.py --provider deepseek --output examples/real`，检查真实返回模型名、token 用量、预算调整和失败恢复。若未通过，先保留失败路径，分析模型选择及工具描述，再修改提示或工具反馈并重新验证；不能把测试期望复制成实际 Trace。
