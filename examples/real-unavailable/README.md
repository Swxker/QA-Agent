# 缺少模型配置的异常处理

本用例验证缺少 DEEPSEEK_API_KEY 时的行为：返回 CONFIGURATION_ERROR，模型与工具调用次数均为零，不会自动切换为 Mock。input.json、output.json 和空 trace.jsonl 为该异常场景的实际记录。

运行：`python -m src.cli run --provider deepseek --input examples/inputs/budget-replan.json --request-key unavailable-deepseek-demo --output examples/real-unavailable`。

正常配置下的真实模型验收结果见 [完整验收总表](../real-full/summary.json)。
