历史 capability 决策测试池：**1,944 个响应，1,940 个可恢复请求前缀，8/10 组、23/32 个可选工具有真实调用。** employee/time_off 只有配置记录，缺口保留。

- `history_cases.jsonl`：请求、响应、调用 ID、文件哈希、分发/结果证据及业务分组。完整上下文由指针读取，不把下一响应喂给路由器。
- `history_inventory.json`：各组覆盖、缺失工具、扫描边界。旧 Stage 探针与真实业务均有，不能全称为业务验收。
- `reviewed_holdout.json`：9 个独立审阅节点，允许多种注入集合，区分必要、无关与尚不确定。仅评测，不用于本轮训练。
- `reviewed_training.json`：6 个训练侧语义复核种子，保存前缀证据、允许集合、必要/无关/不确定能力。属于 Agent 审阅，不冒称人工金标。

维护规则：原始 trace 不修改；新增案例先核对完整请求，再登记证据与标签修订。历史调用只作参考，不能直接当正确答案。允许预加载的能力记为可选正例；明确无关才记负例，不确定保留 `null`。同一业务家族、相同请求的不同别名均不得跨训练与评测。修改标签需递增 `label_revision`，保留 Git 历史；每次实验另存带哈希的数据快照。

导出经过检查的独立快照；会校验原请求哈希、证据位置、标签冲突和分组隔离：

```powershell
.venv/Scripts/python.exe -m experiments.tool_routing.reviewed_dataset --output .runtime/capability-reviewed-new
```

目前仅 6 个复核训练种子，覆盖不足，暂不据此重训。导出目标是“根据前缀可以开放哪些能力”，与原来的“下一步碰巧调用什么”不同；旧训练器尚不消费这个含未知标签的目标。

普通回归不加载模型，也不调用网络：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_capability_routing.py tests/test_dynamic_tools.py -q
```

本机已有冻结数据和权重时，重放输入对照及独立审阅：

```powershell
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.replay_decider --output .runtime/capability-routing-replay-new
```

输出目录必须不存在。依赖、原始上下文及权重保留在本机忽略目录；新机器需先恢复这些材料，索引本身不包含它们。来源哈希不一致时停止，不静默换题。

重建弱标签输入并复现旧训练，仅作研究对照。必须显式启用弱标签：

```powershell
.venv/Scripts/python.exe -m experiments.tool_routing.decision_dataset --output .runtime/capability-dataset-new
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_decider --dataset .runtime/capability-dataset-new --output .runtime/capability-scorer-new --max-len 4096 --allow-reference-labels
```

历史下一工具仅是覆盖参考；`training_eligible` 表示材料可恢复、工具结果明确，不代表当时的业务选择正确。未来新增案例仍先审最早决策点，再补允许行为。详见[调优结果](../../../experiments/tool_routing/TUNING.md)。
