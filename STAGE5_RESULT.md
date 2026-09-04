# 第五阶段验收：原生业务能力与可靠运行上下文

更新：2026-09-05。结论：**第五阶段已验收，可以冻结；无需追加第二版修复。** 运行代码检查点为 `5a73ee4d9b16a9251a861952a89725022f50059e`。只做了 A/B；C 组和 Compiler 均未读取、移植或运行。

## 已验收边界

`integration/odoo_tools.py` 可将 21 个只读业务能力在保持原工具契约的前提下，从 MCP sidecar 切换到 `NativeCapabilities`。真实 Odoo、无模型门禁 `.runtime/stage5/20260904T110914Z-1168/capability-gate` 覆盖全部 21 个能力，结果为 `passed`、`llm_calls=0`；字段策略 fail-closed，异步任务持久化有效，知识索引明确延期而未伪装完成。

本次同时修复了两个独立运行时缺口：

1. 日期不再写入 system prompt；runner 注册最小只读 `get_current_time` 工具，返回本地时区与 UTC，模型按需调用。
2. 当 provider 声明 `requiresReasoningContentOnAssistantMessages` 时，历史 thinking 统一回放到 `reasoning_content`，不再保留为 `reasoning` 并另塞空字段。DeepSeek 的工具调用规范要求携带 `tools` 的后续请求完整回传该字段。

## 确定性检查

根 Harness 与 OpenAI-compatible 适配器共 **147 passed、1 个 Node-only skip**。测试覆盖：时间只作为工具暴露、system prompt 不含运行日期、无 thinking 时保留空兼容字段、有 thinking 时写入非空 `reasoning_content`、未启用该兼容项的 provider 继续沿用原字段签名。

独立子 agent 复核当前 diff 后给出 `ACCEPT`：修复受兼容开关严格限制，未发现跨 provider 破坏。

## 真实模型 A/B

Job：`.runtime/jobs/stage5-capabilities-clock-replay-ab-20260904-r4`。两组使用同一 case 2262、同一快照、模型/high reasoning、原生 read/action、World record、工具契约和 system prompt；唯一实验变量是 capability backend。两组首个 provider request 的 SHA-256 都是 `acfb7b41f81b5ba5fc644dbbb63159260136728fdaa2260db9d0ed174c7408e9`。

| 指标 | A：Capability MCP | B：Capability Native |
| --- | ---: | ---: |
| trial | `6vfKmu7` | `h7VVM97` |
| 得分 / 适用规则 | 100 / 62/62 | 100 / 62/62 |
| 结束 | natural stop | natural stop |
| 模型请求 | 30 | 46 |
| HTTP | 30×200 | 45×200 + 1×524（自动恢复） |
| 模型可见工具调用 | 61 | 110 |
| 原生 capability 分发 | 0 | 9 |
| 最终请求中的非空 `reasoning_content` | 20 | 21 |
| token 合计 | 949,310 | 1,699,590 |

两组都主动调用 `get_current_time`，得到 Odoo 运行时 UTC 日期 `2026-09-04`；system prompt 没有日期文本。两组均持续保留正确采购结论：Alpine `$504.73` 的新增采购毛利约 27.40%，低于 27.6%；因此采购 Marble Movers 10 件、单价 `$400.18`、到货 `2026-09-11`，PO 只关联 Spark 的 `S00004`。verifier 确认新增支出 `$4,001.80`，62 条适用规则全部通过。

B 在创建 PO 前遇到一次 provider HTTP 524；session 记录 `auto_retry_start`，随后完成。该错误未造成悬空工具调用、后端错路由、账本损坏或 verifier 缺口，所以不阻断功能验收，但不能记作全程 200。两组主 stdout JSONL 仍有解析告警，报告明确回退到未修改且有哈希的完整 session。

## 失败根因与结论边界

旧 r3 B `KygQwgv` 并非被工具数据说服后改计划：它在相邻回合前已正确推导 Marble 10 件、9 月 11 日到货可服务 9 月 13 日订单；随后三个无关只读结果没有改变供应商事实，它却无新推理地把日期比较说反，并用全部 36 件销售额替代 7 件新增采购覆盖收入计算毛利。

旧请求把正确 thinking 放在 `reasoning`，同时发送空 `reasoning_content`；在携带 tools 的 DeepSeek 调用中，这会切断规定的思考续接。r4 的最终请求中已不存在旧 `reasoning` 字段，并实际保存非空 `reasoning_content`。这解释了自我推翻的具体机制；原生 Pi 对照也存在同样字段形状，其一次 100 分只能证明那条随机轨迹重算正确，不能反证协议缺口。

r4 只证明 MCP/native capability 两条链路都能完成本 case，不证明 native 更省 token、更快或普遍更稳定。Stage 5 的强制 probe 指令相对 Stage 4 改变了模型输入，因此跨阶段分数仍不能作为纯后端因果比较。若以后评估质量稳定性，应保留原任务提示并做多次配对运行；当前不在安全层硬编码 Bench 答案。

独立子 agent 对 r4 原始请求、时钟调用、后端闭包、异常、账本和 verifier 再次复核，最终结论为 `ACCEPT`。
