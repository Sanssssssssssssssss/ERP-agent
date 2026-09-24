历史 capability 决策测试池：**1,944 个响应，1,940 个可恢复请求前缀，8/10 组、23/32 个可选工具有真实调用。** employee/time_off 只有配置记录，缺口保留。

- `history_cases.jsonl`：请求、响应、调用 ID、文件哈希、分发/结果证据及业务分组。完整上下文由指针读取，不把下一响应喂给路由器。
- `history_inventory.json`：各组覆盖、缺失工具、扫描边界。旧 Stage 探针与真实业务均有，不能全称为业务验收。
- `reviewed_holdout.json`：9 个独立审阅节点，允许多种注入集合，区分必要、无关与尚不确定。仅评测，不用于本轮训练。

普通回归不加载模型，也不调用网络：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_capability_routing.py tests/test_dynamic_tools.py -q
```

本机已有冻结数据和权重时，重放输入对照及独立审阅：

```powershell
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.replay_decider --output .runtime/capability-routing-replay-new
```

输出目录必须不存在。依赖、原始上下文及权重保留在本机忽略目录；新机器需先恢复这些材料，索引本身不包含它们。来源哈希不一致时停止，不静默换题。

重建当前输入并试验新权重，仍只用本地 GPU：

```powershell
.venv/Scripts/python.exe -m experiments.tool_routing.decision_dataset --output .runtime/capability-dataset-new
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_decider --dataset .runtime/capability-dataset-new --output .runtime/capability-scorer-new --max-len 4096
```

历史下一工具仅是覆盖参考；`training_eligible` 表示材料可恢复、工具结果明确，不代表当时的业务选择正确。未来新增案例仍先审最早决策点，再补允许行为。详见[调优结果](../../../experiments/tool_routing/TUNING.md)。
