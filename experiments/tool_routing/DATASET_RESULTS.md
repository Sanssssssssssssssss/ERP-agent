2026-09-26：**最后调优完成，增加可选的不一致回退；保留 v14 权重。** [使用入口](README.md)、[本轮逐项审查及用量](../../.runtime/capability-routing-final-20260926/review-summary.json)。

同一批 60 个节点，v11 为 49 个通过，当前为 **52 通过、3 失败、5 待复核**。总体提高，仍有个别原来正确的节点退步，不能说逐题改善。同输入、问法、8192 窗口的未针对任务训练对照为 5/60。此前本轮报告的 3/60 使用了 SDK 默认 1024 窗口，已作废并重算；当前模型成绩不受影响。这批题已有开发接触，不能作为盲测或主模型替代能力的证明。

| 剩余漏选 | 观察到的问题 | 本轮结果 |
|---|---|---|
| 跨实例前置查询 | 要求完整保留，仍偏向后续写入；等价问法均漏选 | 主模型首次也叫了未发布工具；收到真实拒绝后改为启用能力 |
| 已批准的退货写入 | 忽略尚未执行的写入；选项标签变化会改变决定 | 不一致回退；主模型沿用原动作身份继续 |
| 付款向导继续执行 | 把创建向导当成阶段结束 | 不一致回退；主模型读取原向导，未重复创建 |

`--verify-labels` 增加一次本地等价判定：只交换选项标签，语义不变；分歧不发布候选集。60 节点为 **52 通过、2 回退、1 失败、5 待复核**，回退不算答对。暖态 P50 从 298 ms 增至 588 ms。5 个待复核主要是提前加载未来可能需要的能力，不擅自改为正确。

实验发布入口复用 `DynamicToolController`，保留 list/configure；宿主接管或存在未决写入时保留当前工具。宿主标记必须来自实时状态。接入时需让主模型的手动选择在本 run 内优先，避免下一轮被 Laya 撤销。生产尚未接入，此处没有验证完整业务成本下降。

真实模型 5 个截面有 4 个下一步符合冻结约束。跨实例失败追加 R01 两个单轮分支：补生产提示仍失败，真实运行时拒绝后恢复为 `configure_odoo_tools`；但额外预加载 actions、diagnostics，尚未最小化。只验证下一步意图，未执行返回工具；审批身份与原验证回包一致不替代实时 ActionStore 检查。[原始检查](../../.runtime/capability-routing-final-20260926/recovery/)、[R01 完整证据](../../.runtime/capability-routing-final-20260926/R01/)。

验证：33 项离线检查通过；常驻 CLI 实际验证分歧回退及已完成任务返回基础工具。7 次付费 POST：新输入 104,842、缓存输入 61,824、输出 2,125（含 reasoning 504），合计 **168,791 token**，未知用量 0，无自动重试、无完整业务运行。以下保留此前训练记录。

2026-09-25：**训练已全部结束。交付 v14 权重＋历史意图修复，可本地调用；尚未通过全面替代验收，生产未切换。** 员工、请假排除。[交付核验](../../.runtime/capability-routing-phase-20260925/delivery-check.json)。

| 实际批量调用 | 原测试通过 | 新增阶段控制通过 |
|---|---:|---:|
| v11 参考 | 43/51 | 6/9 |
| v12 | 42/51 | 3/9 |
| v13 | 48/51 | 5/9 |
| v14 | 44/51 | 7/9 |
| v14＋历史意图（交付） | 44/51 | 8/9 |

交付版本默认格式合计 **52/60：3 个必选能力漏选、5 个待复核**。漏选为跨实例前置查询、退货库存待写入、付款向导创建后的继续执行。概率未校准，不能依靠高置信度保证正确。[逐项结果](../../.runtime/capability-routing-phase-20260925/intent-heldout-probe/decisions.jsonl)。

v14 完成 4 轮、836 次更新，选择第 4 轮（开发集 167/180）。已修复训练样本漏覆盖、过大权重和阶段失衡；训练集阶段判断从 9/14 提升到 14/14。评测统一为实际八类批量推理，SDK 核对 204/204。v15 从 v14 补训 4 轮、880 次更新，耗时 91.6 分钟；开发集依次 154、155、157、157/180，未晋级。最终选回初始 v14，导出权重逐字节一致。[训练回执](../../.runtime/capability-routing-phase-20260925/joint-v15-phase-expansion/summary.json)、[导出核对](../../.runtime/capability-routing-phase-20260925/model-v15/export-check.json)。

输入审计发现 41 个节点把上一轮意图存于 `reasoning_content`，旧投射只读空 `content`。现在仅在正文为空时补入有长度限制的历史意图，仍标为未核验。四种格式合计从 371/420 提升到 **378/420，零通过退步**；这是 105 个节点的重复格式检查，含开发集，不能当作 420 个独立业务。正式 CLI 与 105 个受测输入一致，常驻进程的真实请求、非法输入、超窗拒绝和恢复通过。[输入对照](../../.runtime/capability-routing-phase-20260925/intent-format-check/summary.json)。额外事实摘要曾令开发集 42/45 降到 36/45，已放弃。

数据池为 371 个节点：训练 275、开发 45、测试 51，另有 9 个阶段控制。相对 v14 新增 12 个真实审批续调截面、5 个明确标记的有序前置意图变体；争议能力仅在训练中屏蔽，原始标签与分歧保留。v14 权重实际使用的是此前 258 个训练节点。`dataset-v5-intent` 已按修复后的投射重建，371 个节点标签未变，尚未用于新一轮训练。[数据审计](../../.runtime/capability-routing-phase-20260925/v15-data-audit.json)、[当前数据](../../.runtime/capability-routing-phase-20260925/dataset-v5-intent/coverage.json)。

交付模型：[model-v14-intent](../../.runtime/capability-routing-phase-20260925/model-v14-intent/)。28 项离线检查＋3 个子检查通过。旧 bundle 与旧源码配套；回退至 `a6c6358` 可使用原 v14，不能绕过投射哈希检查。

本轮复核及对照共 76 次付费 POST。已知新输入 161,971、缓存输入 88,192、输出 61,720（含 reasoning 57,417），合计 **311,883 token，另 1 次用量未知**。未知项由评测器未兼容可选标注字段而中断，已修复，未重跑。Laya 训练与推理在本机运行，没有完整业务执行。

下面保留 v11 历史结果；对应旧源码与冻结数据。

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

v11 数据补充为 44 次付费 POST：新输入 50,286、缓存输入 32,256、输出 9,797（含 reasoning 7,610），总计 **92,339 token**。复用旧回包 180 份，无额外付费重试、无完整业务执行。截至 v11，数据采集累计 404 次／697,406 token，不含更早的其他实验。[用量回执](../../.runtime/capability-routing-20260925/collection-v4/expansion-summary.json)。

已核实 tokenizer 配置哈希变化来自官方加载器的兼容转换，转换后与本地文件逐字节一致，发生于本轮训练之前；原权重和其余锁定文件通过校验。[核验记录](../../.runtime/capability-routing-20260925/tokenizer-compatibility/receipt.json)。早期失败的决策头实验、日志及[原报告](../../.runtime/capability-routing-20260925/head-v8c/report-before-joint.md)保留。

实验权重：[selected-model](../../.runtime/capability-routing-20260925/selected-model/)。通过 `laya.load(path, device="cuda")` 加载，问题定义使用同目录 `questions.json`，输入沿用冻结的运行状态投射；返回值仅用于能力发布建议。模型默认窗口 8192，调用前须拒绝超长输入，防止 SDK 静默截断。

复现联合训练（隔离 ML 环境；输出目录必须不存在）：

```powershell
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_joint --source .runtime/capability-routing-20260925/dataset-v4 --output .runtime/routing-joint-repeat --epochs 4
```

完整证据：[训练日志](../../.runtime/capability-routing-20260925/joint-v11-expanded.log)、[每项测试](../../.runtime/capability-routing-20260925/joint-v11-expanded/candidate-test.jsonl)、[未训练模型对照](../../.runtime/capability-routing-20260925/joint-v11-expanded/baseline-summary.json)、[导出脚本](../../.runtime/capability-routing-20260925/export-and-check.py)。下一步需要从真实阶段切换截面补训练和独立验收，保留正常只读、已完成业务及合法写入对照。
