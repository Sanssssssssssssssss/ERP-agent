# 单次决策回归

[按能力查找全部测试](../../TESTING.md) · [IR01/IR02 中断恢复结果](../../docs/interrupted-recovery.md)。新故障保留原请求及来源哈希；索引不要求移动历史文件。

Laya 接入审查新增真实前缀 E01/5、E02/26、E03/21、E03/33：冻结原输入及 state 哈希，保留 E03 银行分录被投射省略的问题；只有本地选择器对照，未追加付费调用。主模型历史业务成功不等于 Laya 原始发布正确，[审查及局部结果](../tool_routing/history/INTEGRATION_REVIEW.md)。

E01／E02／E03 完整业务新增真实节点：[R09 复现](../../.runtime/laya-contract-live-20260926/R09-recurrence/)、[R11 制造旧字段](../../.runtime/laya-contract-live-20260926/R11/)、[R12 猜子记录 ID](../../.runtime/laya-contract-live-20260926/R12/)、[R13 空查询条件](../../.runtime/laya-contract-live-20260926/R13/)、[R14 未设投产数量](../../.runtime/laya-contract-live-20260926/R14/)、[R15 提前完工](../../.runtime/laya-contract-live-20260926/R15/)、[R16 不存在的工具](../../.runtime/laya-contract-live-20260926/R16/)、[R17 缺 SOP 输入](../../.runtime/laya-contract-live-20260926/R17/)、[R18 财务旧字段](../../.runtime/laya-contract-live-20260926/R18/)。已保存完整出错／首次纠正请求、响应、哈希及业务约束，未追加付费重放，尚未预防性修复。R12 的上游诱因仍需验证。[完整业务与成本对比](../tool_routing/history/CONTRACT_RESULTS.md)

R09／R10：来自 E01 的旧库存字段试错及重复 preview，[冻结节点](../../.runtime/laya-contracts-20260926/)。`python -m experiments.agent_regression.contract_recovery --output <新目录>` 生成真实 SOP／工具契约候选；加 `--paid` 对 R07、R09、R03 各发一次请求，不执行返回工具。首次结果：R07／R09 避开原错误，R03 仍输出两项删除命令；后者改由共享预检在审批前规范化，非法命令继续拒绝。[原始响应](../../.runtime/laya-contract-nodes-20260926/)。R08 已补退货明细身份预检，真实 Odoo 局部检查确认缺产品拒绝、正确绑定接受；不宣称覆盖任意模型的所有缺省字段。

新增故障只收真实运行证据：完整原请求、首次可纠正截面、错误响应和源码哈希。人工构造边界只做离线测试；没有模型消费请求的 UI 问题不编造付费案例。以后 debug 按仓库 [维护规则](../../AGENTS.md) 补入，原判定不随补丁修改。

R01：Laya 缩小发布集合后，主模型实际调用未发布的跨实例工具。入口 `python -m experiments.agent_regression.routing_recovery`；`freeze` 固定真实失败、运行时拒绝和两个候选截面，`paid` 每分支只发一次。已有结果：补生产提示仍失败，收到真实拒绝后请求启用所需能力，额外预加载尚未消除。未执行任何返回工具。[冻结上下文](../../.runtime/capability-routing-final-20260926/R01/)、[人工审查](../../.runtime/capability-routing-final-20260926/review-summary.json)。

R02（效率，待实验）：E01 接入实跑第 3 轮，Laya 已发布 actions，旧能力列表仍显示 active=[]；模型同轮重复 configure 并正常查询。已冻结完整原请求、回包、宿主发布记录及源码哈希，未追加付费调用。只能确认多余控制工具调用，不能声称多了一轮模型调用；原因是否来自旧状态还需对照。[案例清单](../../.runtime/laya-integrated-20260926/R02/manifest.json)。

R04（效率，未稳定复现）：E01 第二次实跑第 6 轮已发布 actions，模型仍根据历史 active=false 重复 configure。原样与去掉历史启用状态各重放一次，两者均无重复 configure，不能算已修复或归因全部 token。[案例清单](../../.runtime/laya-live-v2-20260926/R04/manifest.json) · [对照](../../.runtime/laya-management-diagnosis-20260926/E01-stale/results.json)。

R05（编排切换，局部确认）：E06 第二次实跑第 12 轮撤下 actions，主模型只 configure。原样重放复现；真实 controller 保留 actions 后直接提出 validate_write。未执行返回工具，尚非完整业务改进。[案例清单](../../.runtime/laya-live-v2-20260926/R05/manifest.json) · [对照](../../.runtime/laya-management-diagnosis-20260926/api-results.json)。

R06（漏预加载，局部确认）：SALE 第 3 轮 Laya 选空，主模型仅 configure；预发布 actions 后提出合法目标的预检。选择器的上游错判原因仍待分离，不能将预发布对照当作已修复权重。[冻结与约束](../../.runtime/laya-management-diagnosis-20260926/R06-manifest.json)。

R04／R06 接口整理复测（`b2f7a1f`）：R04 原样再次重复 configure，生产清理后的候选直接提出 preview＋validate，存在冗余预检，效率待审；R06 缺工具恢复保持正确，Laya 漏选仍未修复。每分支一次，共 4 POST，无工具执行。[冻结及判定](../../.runtime/laya-clean-integration-20260926/frozen.json) · [审查](../../.runtime/laya-clean-integration-20260926/review.json)。

R04／R05／R06 宿主独占编排实验：`python -m experiments.agent_regression.host_routing freeze --output <新目录>`，随后同目录执行 `paid`。真实 Laya 决策＋真实 controller 生成候选，主模型不再看到目录／配置入口及其历史调用；当前 run 的成功 SOP 回执提供工具依赖。三节点 A/B 各一次，共 6 POST，返回工具不执行。输入和约束见 [冻结记录](../../.runtime/laya-exclusive-nodes-20260926/frozen.json)。依赖兜底单独计数，不算 Laya 原始预测正确。回退点 `724a1c9`。

上述候选三个节点的目标／安全检查通过，R04 仍有额外 preview；三个完整业务也通过。结果见 [宿主编排实验](../tool_routing/history/EXCLUSIVE_RESULTS.md)。

R07：本轮 SALE 请求 2 将 SOP 操作写成 `write+confirm`，请求 3 自行修正；[原请求、首次纠正节点及哈希](../../.runtime/laya-exclusive-20260926/R07/manifest.json)。R08：E06 请求 11 的嵌套退货明细缺 `product_id`，预检未提示，请求 12 的创建被 Odoo 拒绝并记为 `known_failed`；请求 13 补字段重新预检，[证据](../../.runtime/laya-exclusive-20260926/R08/manifest.json)。二者已归档，未追加付费重放、未宣称预防性修复完成。

R03 在本轮 E06 请求 29 再次出现，30 自行修正；[复现记录](../../.runtime/laya-exclusive-20260926/R03-recurrence/manifest.json)。沿用原安全约束，不另造案例。

R03（参数恢复，待实验）：E06 第 29 轮使用两项关系删除参数，被校验拦截；第 30 轮自行改为合法三项参数。已保存完整出错与恢复截面、禁止越权和保留原单关系的判定规则，未追加付费调用。[案例清单](../../.runtime/laya-integrated-20260926/R03/manifest.json)。

本轮入口：`python -m experiments.agent_regression.incidents` 离线冻结；加 `--paid` 执行 A04 的 A/B 和原 B02/B03 保护，共 4 次，不执行返回的工具。A04 来源是 S01499 的真实 `confirm` 误调用；跨 run 防重发和桌面回读范围用离线测试验证。结果保存在 `.runtime/agent-regression-guards-20260924/`。

D04 来自明确选择“整个会话”后，模型把无绑定状态误判为“只生成提案”的真实请求。只替换生产状态回包和工具定义，不注入业务绑定或改写旧历史。冻结：`python -m experiments.agent_regression.incidents --directory .runtime/agent-regression-scope-20260924 --case D04`；同目录加 `--paid`（不带 `--case`）执行 A/B 共 2 次。[原始传播证据](../../.runtime/agent-regression-full-20260923/full/S01499/conversation-scope-finding.json)。

待维护：2003 的 SOP 缺必填输入、空查询条件、猜字段及同轮依赖读取（[原请求与哈希索引](../../.runtime/agent-regression-full-20260923/full/bench/2003-replay-candidates.json)）。后续修改对应能力时冻结该节点再验证。

12 个真实 trace 节点，A/B 各一次请求；返回工具意图只保存，不执行。完整请求、推理、原始响应与判定依据在 `.runtime/agent-regression-20260923/`。

```powershell
python -m experiments.agent_regression.freeze
python -m experiments.agent_regression.provenance
python -m experiments.agent_regression.prepare
python -m pytest tests/test_agent_regression.py -q
# 由负责人设置 COMMAND_CODE_API_KEY 后，执行已授权的 24 次请求：
python -m experiments.agent_regression.runner --paid
python -m experiments.agent_regression.runner --summary
```

可用 `--case A01`、`--group tool_contract`、`--arm candidate` 选择子集。每个节点/分支只允许启动一次；失败、取消或用量不明均不补发。HTTP 无超时、无重试、无自动续跑。

阶段与上下文追加对照入口：`python -m experiments.agent_regression.context_followup` 离线准备/核验，显式加 `--paid` 才发送。B01 三分支、A03/B02 各两分支、E02 三分支，共 10 次；已运行分支禁止再次启动。完整证据在 `.runtime/agent-regression-context-20260923/`，结果见 [报告](RESULTS.md)。

四条完整业务已于 `f32cc60` 运行，入口、冻结哈希、岗位 profile、审批和完整日志保存在 `.runtime/agent-regression-full-20260923/full/`。该目录已有 attempt 标记；查看 `summary.json` 与各题日志，不重复启动。S01499 聊天及2278成本仍待验收，详见 [报告](RESULTS.md)。

`freeze.json` 锁定来源、oracle 和上下文哈希；`cases/*/manifest.json` 的 `boundary` 说明故障传播切点，不能将其当成上游根因的独立证明。A03/B02/B03/C03/D03 是正确保护，D01/D02 是已修问题防回归。B01/C01/C02 的上游问题是当前阶段与历史目标混入；A01/A02 检查工具契约。两个旧 prepared 分支保留其真实来源标识。

`provenance.json` 补充源码版本、根因位置、首次可纠正请求和归档证据哈希。历史补丁版本以当时 source-hashes 为准；未知 commit 不猜测。D01/D02 保留祖先原请求；C02 勘误仅澄清“三类绑定各需唯一”，原 oracle 与通过条件不变。

| 节点 | 检查点 | 根因或保护范围 |
|---|---|---|
| A01 | 确认请求 1 | 首轮裸工具名；名称契约缺口是待验证原因 |
| A02 | 确认请求 7 | SOP 已在请求 3 消费；区分方法与字段写入 |
| A03 | 确认请求 12 | 目录权限误报缺模型；保护正确确认动作 |
| B01 | 开票请求 29 | host 目标串接在请求 1 已出现 |
| B02 | 投递请求 8 | 保护合法发送提案 |
| B03 | 最终追问请求 2 | 保护 SMTP 接受证据与不重发 |
| C01 | 开票请求 39 | 同一范围根因；PDF 拒绝后的恢复点 |
| C02 | 开票请求 41 | 同一范围根因；缺绑定后的交接点 |
| C03 | 资格查询请求 3 | 保护业务选择权 |
| D01 | 已修 B01 分支 | 内部公司联系人不是订单客户 |
| D02 | 已修 C06 分支 | 公司与客户查询范围歧义 |
| D03 | 原 B02 请求 2 | 金额冲突需要澄清；源码 commit 未知 |

候选调用生产 builder，仅替换声明位置；逆替换必须还原完整原请求。这只控制实验输入，**不要求输出遵循 Golden Trace**。正确身份、授权、业务约束和证据决定结果；额外合理只读、措辞或工具顺序变化允许通过人工复核。无法确定的语义标为 `needs_review`，不调用模型裁判。单节点不证明最终业务成功。

C02 的新 runtime 会在 typed handoff 后暂停，其强制续出的模型回复属于反事实分析。真实暂停由离线会话持久化检查验收，不能把该回复里的工具意图计为生产调用。结果和未通过项见 [简短报告](RESULTS.md)。

`results/*/*/` 保存请求、原始 SSE、意图和结构检查。`summary.json` 单列 fresh/cache/output、reasoning 子集、请求数、工具意图、耗时与未知值；这些数字不参与业务正确性打分。最终业务回归由负责人另行执行。
