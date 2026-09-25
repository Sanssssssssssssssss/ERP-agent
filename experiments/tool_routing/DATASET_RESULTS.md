2026-09-25：**两组联合训练、测试与权重导出已完成；仍未达到全量替换条件。** 员工、请假排除。生产 `src/`、`desktop/` 未改。

| 同一测试输入与问法 | 默认格式通过 | 四格式合计通过 |
|---|---:|---:|
| 未训练 Laya | 12/51 | 46/204 |
| 联合训练，185 个训练节点 | 37/51 | 153/204 |
| 补数据后，226 个训练节点 | **43/51** | **172/204** |

最终默认格式：43 通过、2 失败、6 待复核；四格式均通过的节点也是 43/51。相对上一组有 19 次格式判断改善、0 次通过退步。204 次判断来自 51 个节点，不是 204 个独立业务。见[逐项对照](../../.runtime/capability-routing-20260925/joint-comparison.json)。

改善全部来自新契约测试：30 个节点四格式 120/120；21 个历史截面（含早期探针）仍为 52/84。不能据此宣称复杂业务已经泛化或业务成功率提高。

剩余问题：两个长任务明确要求先做跨实例查询、知识检索，完整输入保留了这些要求，模型仍只发布 actions。另六个历史截面也发布 actions，其中三个业务已到收尾；冻结标签对此标为不确定，保留“待复核”，不算通过。当前证据指向任务阶段辨别不足，未证明单一根因。

本次修正了发布任务问法、选项中的工具职责、整组路由选优规则，并改为编码器层＋决策头联合训练。两组各 4 轮，共 808 次更新；补数据组按开发集选第 3 轮（154/180）。可训练参数 125,102,593，词嵌入冻结；使用复核硬标签交叉熵，未复现官方 RLCD。多项训练设置同时改变，不作单因素归因。

最终数据 322 个节点：训练 226、开发 45、测试 51。新增 44 个工具契约探针，模型标注一致的 41 个入训练，3 个分歧隔离。96 个开发／测试节点的输入与标签保持一致；新探针是 Agent 编写，并非新执行的完整业务。此池已有开发接触，不是盲测。见[数据覆盖](../../.runtime/capability-routing-20260925/dataset-v4/coverage.json)、[隔离清单](../../.runtime/capability-routing-20260925/dataset-v4/quarantine.json)。

验证：23 项离线检查＋3 个子检查通过；官方 SDK 逐类核对 204/204 一致，导出后八类批量调用 51/51 一致。RTX 4070 Laptop 上，模型加载约 6.68 秒，首调用 0.60 秒，后续 50 个不同节点 P50 0.26 秒／P95 1.22 秒；不是端到端业务耗时。概率未校准。见[训练回执](../../.runtime/capability-routing-20260925/joint-v11-expanded/summary.json)、[导出核验](../../.runtime/capability-routing-20260925/selected-model/export-receipt.json)。

本轮新增 44 次付费 POST：新输入 50,286、缓存输入 32,256、输出 9,797（含 reasoning 7,610），总计 **92,339 token**。复用旧回包 180 份，无额外付费重试、无完整业务执行。数据采集累计 404 次／697,406 token，不含更早的其他实验。训练、测试、导出仅本地运行。[用量回执](../../.runtime/capability-routing-20260925/collection-v4/expansion-summary.json)。

已核实 tokenizer 配置哈希变化来自官方加载器的兼容转换，转换后与本地文件逐字节一致，发生于本轮训练之前；原权重和其余锁定文件通过校验。[核验记录](../../.runtime/capability-routing-20260925/tokenizer-compatibility/receipt.json)。早期失败的决策头实验、日志及[原报告](../../.runtime/capability-routing-20260925/head-v8c/report-before-joint.md)保留。

实验权重：[selected-model](../../.runtime/capability-routing-20260925/selected-model/)。通过 `laya.load(path, device="cuda")` 加载，问题定义使用同目录 `questions.json`，输入沿用冻结的运行状态投射；返回值仅用于能力发布建议。模型默认窗口 8192，调用前须拒绝超长输入，防止 SDK 静默截断。

复现联合训练（隔离 ML 环境；输出目录必须不存在）：

```powershell
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_joint --source .runtime/capability-routing-20260925/dataset-v4 --output .runtime/routing-joint-repeat --epochs 4
```

完整证据：[训练日志](../../.runtime/capability-routing-20260925/joint-v11-expanded.log)、[每项测试](../../.runtime/capability-routing-20260925/joint-v11-expanded/candidate-test.jsonl)、[未训练模型对照](../../.runtime/capability-routing-20260925/joint-v11-expanded/baseline-summary.json)、[导出脚本](../../.runtime/capability-routing-20260925/export-and-check.py)。下一步需要从真实阶段切换截面补训练和独立验收，保留正常只读、已完成业务及合法写入对照。
