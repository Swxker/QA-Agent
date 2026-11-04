# 方案与实现

## 技术取舍

原生 Python 工具循环，argparse CLI，SQLite 事务持久化；无 Agent 框架。httpx 0.27.0 负责 HTTP/超时，Pydantic 2.8.2 负责输入和工具参数校验，pytest 7.4.4 负责自动测试。依赖见 requirements.txt。任务规模小，原生循环让决策、状态与记账边界直接可审查。

DeepSeek 官方资料核对日期：2026-11-01。使用 [Function Calling](https://api-docs.deepseek.com/guides/tool_calls/) 和 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。默认 deepseek-flash，可配置。显式 `thinking: disabled`、`tool_choice: required`，每轮只接受一个工具调用，不依赖 Beta strict。模型响应的 reasoning_content 不保存、不导出。

## 决策循环

1. 校验业务输入；检查请求指纹和幂等 Key。
2. 读取任务检查点，获取带 fencing token 的执行租约。
3. 将原始请求、工具对话、当前计划、账本、估算、剩余次数交给模型。
4. 模型返回工具名、参数、简短依据；持久化 pending 意图。
5. 参数与业务校验，通过后在状态副本上执行工具。
6. 成功时提交产物、费用、步骤、Trace 和工具消息；失败时丢弃副本的所有修改，保留失败记录。
7. 用真实工具消息继续请求模型，直到 finish、stop、异常或次数上限。

每条 assistant tool_call 与其 tool_call_id 结果一起持久化。续跑恢复原对话，下一策略由模型根据工具结果决定。执行器负责约束校验，不在超预算时直接代选替代方案。Mock 使用固定策略，仅用于验证流程和工程约束。

## 工具与约束

search_materials 返回关键词匹配的合成资料，不执行采购。revise_plan 只能选择检索过的 ID，模型给出目标与渠道；更换目标/素材/渠道时归档旧脚本、作废依赖估算，沉没费用保留。estimate_plan 根据规则表计算报价。generate_script 接受模型撰写的正文，校验引用后持久化，工具内部不再调用模型。save_plan 必须有脚本和当前版本的可行估算。finish 必须有已保存方案。

部分资料仍返回可用素材，但 coverage 缺少 product/brand 任一项时不准保存；模型可继续检索、调整或停止，不能编造补全。当前工具不支持合并多份素材补齐 coverage，这属于范围限制。

## 费用与时间

初始 premium：授权 9＋脚本 2＋保存 1＝12；替代 basic：5＋2＋1＝8。检索、估算、计划调整不收费。脚本成功提交时记 2，保存时原子提交授权和保存费用。注入故障均在提交前，免费。

完成总费用＝累计已发生费用＋当前方案未支付费用。已有脚本不再计脚本待付费，弃用脚本的费用仍计入累计。Decimal 运算，账本有唯一业务 Key。生成脚本后账本签名改变，必须重新 estimate，防止使用过期报价。

模拟耗时表示尚需的素材准备、脚本准备和保存时间。没有实际等待或发布。以当前时间＋剩余模拟耗时检查截止时间；脚本成功后相应工作视作完成。保存前重新检查。输出的 expected_finish 是模拟可行性估算，不是已实际执行生产工作的证据。

## 状态与依赖

任务：running、needs_input、completed、incomplete。reason_code 区分缺失字段、模型异常、配置错误、主动停止、截止时间已过和上限等。

步骤：pending、succeeded、failed、superseded；计划选定时建立后续 pending 步骤。工具执行期间以持久化 pending 动作表示，不预先宣称步骤成功。历史计划保留在 plan_history；所有失败 attempt 永久留在 trace。被替代的步骤不能作为当前成功依赖。

依赖图：search→select→estimate-vN-before-script→script→estimate-vN-before-save→save。每次计划修改增加版本；生成前后的估算有不同 ID，避免时序倒置和依赖环。脚本、保存还记录其实际前置步骤。

## 幂等、原子性与恢复

请求指纹包含规范化输入、provider、模型配置、素材与规则、故障选项、执行上限。同 Key 不同指纹冲突；重复 run 只返回已有检查点。resume 是显式继续执行，只能补齐缺失约束；不清零费用或调用计数。

SQLite runs 保存完整 JSON 检查点，plans 保存每任务唯一方案。状态、计划和账本在同一事务写入；每次写入检查 owner token 与租约。单次租约 180 秒，每次检查点刷新。崩溃后等待租约到期再恢复；旧 worker 不能写。pending 工具意图可重放，因为所有本地副作用仅在最终检查点事务内生效。

若生成脚本已提交、保存失败，续跑直接复用脚本。硬退出期间的模型请求可能已经消耗供应商 token，无法用本地事务保证供应商计费 exactly-once；未来外部采购也需供应商幂等与对账，不能照搬本地保证。

默认各 20 次模型决策/函数调用（含控制动作），上限 100，续跑累计。网络请求每次 30 秒，瞬态错误最多尝试 2 次；不对业务工具做隐藏自动重试。无合法模型动作时明确 MODEL_ERROR；合法但业务非法的动作返回失败工具消息供模型调整。

2026-11-04 真实验收发现模型可能在持续故障下反复重试。改进后的错误观察包含 failures_in_plan；同工具同计划第二次注入失败会返回 retryable=false，并建议保留产物等待服务恢复。提示明确至多一次重试后应停止或换可行计划。执行器不直接替模型选重试、停止或替代方案；真实 Trace 用于确认模型确实响应反馈。显式续跑可在服务恢复后再次尝试，累计上限保持不变。

## Trace

JSONL 记录序号、时刻、动作、参数、计划版本、简短依据、工具结果、费用、步骤变化、下个动作及模型用量。next_action 来源于下一条实际事件，不是预设预测；最后终态为 end。评估器恢复外部服务明确标记 evaluation_restore_service，不伪装模型动作。
