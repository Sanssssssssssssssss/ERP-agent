接入审查：状态投射、决策目标、依赖生命周期和评测口径存在缺口。现有证据不足以把 E02／E03 的 token 增长归因于 Laya 模型能力。审查源码 `f69657e`；本轮生产实现及权重未改。

当前实际链路：每次主模型请求前 → 从消息提取 goal、最近 8 条工具结果、旧宿主通知、先前意图 → Laya 判断 8 组是否发布 → runtime 并入 SOP／未决动作依赖 → 主模型接收工具并执行业务。Laya 不生成业务参数，不运行 Odoo。

三个 run 的 107 个请求全部走该链路，每次做两次标签交换推理，共 214 次本地 forward。但三个 run 都是前两轮 17 个基础工具，第 3 轮起固定 22 个工具，只有一次集合变化；主模型消息内旧目录／配置入口提及为 0。**本轮增加的探索不能归因于工具集合反复切换。**

| 检查项 | 本项目实际情况 | 对照与修复方向 |
|---|---|---|
| 决策状态 | 从聊天截最近结果；没有直接接收宿主当前阶段、未决账本和任务范围状态 | 从现有宿主对象构造最小事实；未知阶段明确未知，不能把上轮计划当事实 |
| 证据裁切 | 任意数组只留前 3 项；E03 请求 21 的分录 `[121,122,8,110]` 丢掉银行分录 110，整个投射中不再包含它 | 按业务对象和关联保留必要事实；缺证据应返回缺失标记，不能把缺失当“不需要能力” |
| 判断目标 | 问“当前未完成阶段”，却允许“先读一次，所以可以不发布 actions”；任务相关、当前可执行、下一个调用混用 | 固定为当前授权范围内所需能力可用性；审批和方法前置条件继续由执行层判断 |
| 训练及评测 | 354 节点中，训练 53、开发 11、测试 9 个同时接受空集及 actions；这些并非全部错误标签，但不能验证主动预加载是否承担成功 | 新契约另立版本；原评测不改答案。分别衡量业务动作覆盖、主动预加载、实际发布效果 |
| 发布策略 | SOP 依赖整个 run 不到期；44 轮集合包含 runtime 补入／保留项 | 确定依赖直接保留，Laya 判断其他相关能力；只有宿主确认阶段变更才释放依赖，不能为了验证模型而撤掉保护 |
| 可靠性 | 两次 A/B 标签都选“不发布”仍可能遗漏所需工具；未校准分数不代表可靠 | 明确证据不足与决策不确定的回退；标签交换用于诊断，不能代替正确性／校准 |
| 职责粒度 | 17 个读／查字段／SOP／观察工具常驻；付款、制造、库存都通过同一 actions 组 | 当前目录只能接管组选择，不能同时被当作业务步骤编排器；先完成这个职责的对照，不扩大规划架构 |

Mu 源码对照，固定在 `1272e53c0990d7653910fb47061741dead664442`：

- [capability.disclosure](https://github.com/qybaihe/mu/blob/1272e53c0990d7653910fb47061741dead664442/packages/kyrn-judge/src/decisions/capability-disclosure.ts) 使用明确的 `user_message`，每个候选一个相关性判断。其作用域是用户任务，不是推测下一次具体工具调用。
- [目录接入](https://github.com/qybaihe/mu/blob/1272e53c0990d7653910fb47061741dead664442/packages/kyrn-judge/src/extension/features/catalog.ts) 在用户轮次开始预判；已打开的能力保留，还提供 `find_capability` 恢复。它不是每个模型请求重选全目录。我们可保留每请求前的发布钩子，但状态未变时复用决策；恢复可接已有 diagnose 路径，不必让主模型重新常规管理能力。
- [DecisionEngine](https://github.com/qybaihe/mu/blob/1272e53c0990d7653910fb47061741dead664442/packages/kyrn-judge/src/decision.ts) 将 buildState、questions、policy、fallback 分开，记录决策来源和版本；[task.frame](https://github.com/qybaihe/mu/blob/1272e53c0990d7653910fb47061741dead664442/packages/kyrn-judge/src/decisions/task-frame.ts) 明确携带目标、约束、当前子目标；[tool.admission](https://github.com/qybaihe/mu/blob/1272e53c0990d7653910fb47061741dead664442/packages/kyrn-judge/src/decisions/tool-admission.ts) 将长输出分类另立决策点，不与能力选择混在一起。
- 这是源码对照，未运行 Mu，不能把其注释中的效果当本项目实测。Laya 官方 [分阶段接入](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/docs/staged-adoption.md) 也要求同请求、同问题语义比较，显式 unknown／review；不能把参考链路的每次分歧都判成模型错误。

本地真实权重实验：从已经完成的 run 冻结 E01/5、E02/26、E03/21 三个实际写入节点，以及 E03/33 完成节点。先核对重建投射 SHA256 与当时决策一致；不使用后续响应构造输入。所有分支同权重，不训练、不执行工具。

| 节点 | 原投射／原问题 | 去部署杂项、保留同一组业务观察，并缩短问题 |
|---|---|---|
| E01/5 数量写入预检 | 不发布 actions | 两次标签结果不一致，需回退 |
| E02/26 创建半成品制造单 | 不发布 actions | 仍不发布 |
| E03/21 银行核销 | 不发布，actions 原始分数 0.0637 | 发布，两次标签一致，分数 0.9450 |
| E03/33 完成后汇报 | 不发布 | 仍不发布 |

单独改 `has_dynamic_controller` 为 true 或删除该字段，四节点均未修正；单独去部署杂项已让 E03/21 转为发布。由此只能确认输入表达影响该节点，不能声称短提示已解决整个接入。分数未经校准；四节点已被审查，不是盲测。

最小后续实施边界：复用现有 provider/controller/SOP/ActionStore/World，先版本化“宿主事实 → 决策输入 → 发布结果”；为任务相关性和即时执行授权确立不同字段。保留共享权限、审批与未知写保护。按新契约重审训练输入及预加载标签，再做同模型、同前缀的原路由／固定工具集／新 Laya 对照。现有二进制开关、简短问题或继续添加字段补丁不足以完成这一点。

本轮仅本地推理：24 次路由实验、48 次 SDK forward、1,200,336 个 SDK 输入 token；付费 API 0、Odoo 调用 0。证据：[结构统计](../../.runtime/laya-interface-audit-20260926/structure.json) · [原输入及单变量冻结](../../.runtime/laya-interface-audit-20260926/frozen-probe.json) · [状态／问题对照](../../.runtime/laya-interface-audit-20260926/contract-frozen.json) · [响应](../../.runtime/laya-interface-audit-20260926/contract-predictions.jsonl) · [用量及源码哈希](../../.runtime/laya-interface-audit-20260926/summary.json)。
