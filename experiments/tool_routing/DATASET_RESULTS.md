2026-09-25：补齐八类 capability 的基础正负例覆盖，员工、请假按用户要求排除。当前在独立 ML 环境训练，尚未验收或接入生产。

| 数据 | 数量与边界 |
|---|---|
| 历史响应索引 | 原 1,944＋新增 1,792＝3,736；不是独立训练样本数 |
| 原复核池 | 62 个，原标签文件不改 |
| 新复核历史 | 44 个写入前缀，4 个超过编码器窗口，保留原文并隔离 |
| 新采集 | 60 个契约意图×3 个上下文＝180 个节点；同意图不跨集合 |
| 最终输入 | 训练 185、开发 45、测试 51；另隔离 1 个模型标注分歧 |

必要能力、推荐预加载、合法替代路径分别记录。新意图是 Agent 编写的契约探针；真实读取材料来自隔离企业库。它们不代表已完成业务，也不是独立人工金标。稀缺能力每类训练正例来自四个意图，开发和测试各一个，仍需真实业务节点持续补充。

四个超长输入主要被预检、写入计划和字段提示占据，历史观察合计 1.6–3 万字符。原文见 [输入体积分解](../../.runtime/capability-routing-20260925/oversized-breakdown.json)；本轮未把裁剪后的内容冒充完整上下文，也未验证这四个节点的路由能力。

标注暴露两处输入问题：基础工具只有笼统说明，导致 `get_model_fields`、`diagnose_current_run` 被误归到可选诊断；几个新场景夸大了财务健康摘要的实际能力。修正后，新一轮与预设标签一致 179/180，前一轮 169/180。输入同时有多处修改且只有单次比较，不作单因素因果结论。

全部真实请求 360 次；新输入 192,386、缓存输入 349,568、输出 63,113，其中 reasoning 47,430；总 token 605,067。无自动重试，返回的工具调用未执行，未跑完整业务。

验证：21 项离线检查、3 个子检查通过。281 个冻结投射输入与重新生成结果一致；开发/测试分歧没有按模型成绩删题。ML 环境仅解析源码中的工具合同，未引入生产依赖。

入口：`python -m experiments.tool_routing.collect_dataset freeze|paid|export --help`。`paid` 跳过所有已启动请求，包括状态不明的请求；不能用续跑隐式补费。`export` 只隔离训练分歧，开发/测试保留；超长评测输入直接报错。

证据：[标注输入与回包](../../.runtime/capability-routing-20260925/collection-v3/)、[类别覆盖](../../.runtime/capability-routing-20260925/dataset-v3/coverage.json)、[隔离清单](../../.runtime/capability-routing-20260925/dataset-v3/quarantine.json)、[训练日志](../../.runtime/capability-routing-20260925/head-v8b.log)。失败的首次 ML 启动发生在模型加载前，原因是隔离环境缺少生产包；输入等价修复后启动 `head-v8b`，保留原错误日志。

训练复现：

```powershell
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_reviewed --source .runtime/capability-routing-20260925/dataset-v3 --output .runtime/routing-head-repeat --epochs 24 --min-epochs 8 --patience 6
```
