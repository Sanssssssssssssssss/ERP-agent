历史 capability 决策测试池：**1,944 个响应，1,940 个可恢复请求前缀，8/10 组、23/32 个可选工具有真实调用。** employee/time_off 只有配置记录，缺口保留。

- `history_cases.jsonl`：请求、响应、调用 ID、文件哈希、分发/结果证据及业务分组。完整上下文由指针读取，不把下一响应喂给路由器。
- `history_inventory.json`：各组覆盖、缺失工具、扫描边界。旧 Stage 探针与真实业务均有，不能全称为业务验收。
- `reviewed_holdout.json`：9 个独立审阅节点，允许多种注入集合，区分必要、无关与尚不确定。仅评测，不用于本轮训练。
- `reviewed_training.json`：6 个训练侧语义复核种子，保存前缀证据、允许集合、必要/无关/不确定能力。属于 Agent 审阅，不冒称人工金标。
- `reviewed_expansion.json`：新增 47 个真实前缀及固定 API 对照 ID；合计训练 28、开发 13、评测 21。保留业务家族、阶段、证据位置和明确探针标记。

维护规则：原始 trace 不修改；保存完整请求并复核相关前缀证据，再登记标签修订。历史调用只作参考，不能直接当正确答案。必要能力为正例，明确无关为负例；允许预加载和不确定都在训练时屏蔽，不能强迫模型选中。允许集合并非穷举，未明确否定的其他组合判 `needs_review`。同一业务家族、相同请求的不同别名均不得跨训练、开发与评测。标签变更递增 `label_revision`；每次实验另存带哈希的快照。

导出经过检查的独立快照；会校验原请求哈希、证据位置、标签冲突和分组隔离：

```powershell
.venv/Scripts/python.exe -m experiments.tool_routing.reviewed_dataset --output .runtime/capability-reviewed-new
```

完整扩充池使用 `model_comparison freeze` 冻结，随后 `train_reviewed` 消费带未知标签的目标。当前只有 actions/knowledge 有训练必选正例；这仍是小样本学习实验。旧训练器保留用于显式复现弱标签对照。

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
