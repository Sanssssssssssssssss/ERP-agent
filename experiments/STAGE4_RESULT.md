# 第四阶段验收：统一写动作与持久恢复

更新：2026-09-04。结论：**第四阶段已验收，可以进入第五阶段。** 模型实验所用源码提交为 `ec728da30fa41049495fed994392ea0249eb759b`。只做了 A/B；C 组和 Compiler 均未读取、移植或运行。

## 已验收边界

`odoo_runtime/actions.py` 将预览、实时校验、批准写入、允许的方法、chatter 和附件输入收进同一动作边界；`odoo_runtime/store.py` 用标准库 SQLite 保存批准、发送、已知失败、已验证和待对账状态。动作凭证绑定规范化参数、实例、身份、公司、策略摘要和有效期；参数、策略或关键前态变化会拒绝旧凭证。

发送前失败可安全停止；发送后出现未知异常默认进入 `needs_reconciliation`，不会盲目重试。执行后从 Odoo 重读字段或业务状态，只有可验证后态才记为 `verified`。同一受影响对象使用保守资源锁；四个已允许副作用方法必须携带精确、非空、正整数记录 ID。未列入清单的方法、通知型 chatter、越界上传和关系指令在发送前拒绝。

本地账本保证本进程边界内不重复领取并诚实记录未知结果；它不承诺 Odoo 服务端严格一次执行，也不提供跨多请求原子事务。需要远端严格幂等时仍需 Odoo 业务键或服务端支持。

## 三层验收

1. **确定性检查：** Harness 全量为 **55 passed、29 subtests passed**；动作、报告和 MCP 关系参数专项为 **129 passed、4 subtests passed**。完整固定 MCP 套件为 **931 passed、1 项 Windows symlink 权限用例按计划排除**；一个既有时间戳并列排序用例首次波动，原命令单独重跑通过。
2. **真实 Odoo、无模型：** `.runtime/stage4/20260904T073508Z-1144/action-gate` 完成 11 个真实副作用并全部 `verified`，包括 6 个写动作、1 个 chatter 和 4 个业务方法；`llm_calls=0`。独立复核从 SQLite 重算类型、状态和 SHA，与导出摘要一致。
3. **真实模型 A/B：** 两组使用同一 case 2262 快照、`ec728da`、模型/high reasoning、读取后端、World 模式、工具契约和系统提示。A 固定自 r7；B 是相同提交下对失败 B 的最小补跑 r8。两组均自然 `STOP`、100 分、62/62 条适用规则通过，无 Harbor/Agent 异常。

| 指标 | A：MCP 动作（r7） | B：原生动作（r8） |
| --- | ---: | ---: |
| trial | `HpMsKdZ` | `T8MZLBD` |
| 模型请求 / HTTP 200 | 66 / 66 | 56 / 56 |
| 模型可见工具调用 | 150 | 107 |
| 实际 MCP / 原生动作分发 | 62 / 0 | 2 / 33 |
| Odoo JSON-2 尝试 / 可恢复错误 | 195 / 11 | 157 / 8 |
| 动作账本 | 不要求 | 14 个，全部 `verified` |
| token 合计 | 3,179,025 | 2,237,262 |

B 的 ledger 与 World integrity 均为 valid，动作账本由独立子 agent 直接从 SQLite 重算为 7 个 write、7 个 method，摘要、状态、回执和数据库 SHA 全部一致；后端错路由为 0。两组快照 SHA-256 均为 `169886cd1c4fa78290816fe57288f0d00b68f596b15be7eaa7236acc324a8eb1`。

## 保留的失败证据

r7 的原生 B 得分 22.77，仍保存在 `.runtime/jobs/stage4-actions-ab-20260904-r7`。它的 19 个动作全部 `verified`，账本与 World 完整，45/45 模型请求均为 HTTP 200；模型却把 7 天交期采购单的 `origin` 显式关联到四张销售单，导致采购到货晚于其中三张早期订单的需求日期，失败 `po_delivery_schedule_compliance` 及其依赖的 `supply_timing_feasible`。

原生动作层保存、发送并读回了模型给出的相同 `origin` 和 `date_planned`，没有转换漂移。该记录因此归类为模型业务规划波动，不是动作传输或恢复故障。r8 的成功只证明原生链路能够完成此任务，不证明原生后端更优、更省 token 或更稳定；采购规划稳定性属于第五阶段 SOP 的实验问题，不能在安全层硬编码 Bench 答案。

独立子 agent 在 r7 失败后核对执行参数、账本与 verifier 因果，又在 r8 后重算 SQLite 并确认严格门槛满足，最终结论为 `ACCEPT`。
