# 第五阶段阶段性验收：原生能力与受控 SOP

更新：2026-09-05。结论：**5.1 原生能力与 5.2 受控 SOP 已验收；第五阶段尚未整体冻结，5.3 动态工具仍待实现。** 5.1 运行代码检查点为 `5a73ee4d9b16a9251a861952a89725022f50059e`，5.2 为 `3e8258ee37cb950599f1f84a6b1da1ba90890830`。只做了 A/B；C 组和 Compiler 均未读取、移植或运行。

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

## 5.2：受控 SOP 验收

runner 在 `sop_mode=controlled` 时只增加 `list_odoo_sops` 与 `get_odoo_sop` 两个只读工具，显式提供 11 份仓库内审核 SOP；项目 resources、skills 和 extensions 仍关闭。SOP 是数据与操作指导，不是授权。发票过账改为 allowlisted `account.move.action_post`，费用审批要求发现当前版本的真实业务方法；二者均禁止直接写 `state`。业务输入不进入 SOP 调用日志。

报告器使用 SOP 与 Odoo 工具共享的单调序号，要求非空调用号、start/end 一一闭合、`list → get → 首次副作用` 的完成顺序；缺日志、孤立完成、悬空开始、缺调用号或顺序错误都会使 controlled trial 不能记为 natural end。实现修复后，根 Harness 为 **65 passed、1 个 Node-only skip**；相关 MCP workflow prompt 为 **16 passed**，独立 Linux `/tmp` 的完整 MCP 回归为 **933 passed**。Pi 上游全量额外得到 1952 passed、4 skipped；17 个失败来自本干净仓库未搬入的示例扩展、未安装的可选 `socksio`/`uv` 和源码运行版号，不涉及本次修改，也未被记为全量通过。

真实 Job：`.runtime/jobs/stage5-controlled-sop-ab-20260905-r1`。A、B 使用同一 case 2262、提交、快照、模型/high reasoning、原生 read/action/capability 与 World record；配置唯一变量为 `sop_mode=off/controlled`。两边快照回执均为 `169886cd1c4fa78290816fe57288f0d00b68f596b15be7eaa7236acc324a8eb1`、550 张表、445 个文件。

| 指标 | A：SOP off | B：SOP controlled |
| --- | ---: | ---: |
| trial | `xk9BQU3` | `eTbxtvW` |
| 得分 / 适用规则 | 100 / 62/62 | 100 / 62/62 |
| 结束 | natural stop | natural stop |
| 模型请求 / HTTP | 38 / 38×200 | 38 / 38×200 |
| 模型可见工具调用 | 100 | 84 |
| token 合计 | 1,690,592 | 1,533,682 |
| SOP 调用 | 0 | 4 |
| SOP 收据与顺序 | 不适用 | 有效 |

B 先在序号 1→2 成功列出 SOP，在 7→8 读取本题后续开票所需的 `invoice_approval_chain`。第一次读取 `safe_write_review` 因缺少 `model`、`operation` 在 9→10 被参数门禁拒绝；模型随后自行纠正，在 93→94 成功读取 `safe_write_review(sale.order, create)`，首次副作用 `execute_approved_write` 在 119→120 才开始并验证成功。SOP 收据没有悬空或孤立事件。B 的一次 MCP 分发是尚计划留到第七阶段处理的 `health_check`，不属于当前已迁 read/action/capability 集合，报告无后端错路由。

独立子 agent 对配置、canonical session、HTTP、工具、SOP、动作账本、World 与 verifier 回执给出 `ACCEPT`。本次只能证明受控 SOP 可发现、可读取、写前顺序成立且没有损害此单题正确性；不能证明 SOP 提高得分、token 效率或总体可靠性。首次缺参也说明 SOP 使用并非完全无摩擦。下一步是独立的 5.3 动态可见工具集实验，不把本结果与动态选择混为一次改动。
