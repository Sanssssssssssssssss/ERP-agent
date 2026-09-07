# 第六阶段验收结果：原生知识通过

更新：2026-09-07。结论：**Stage 6 已验收（ACCEPT）。** 原生知识运行代码检查点为 `54370d57a527306098c457e6acbcbcc8dae96a01`；B-only 复验配置检查点为 `0d1f0a51598b5afbef6023a424d34c532835371a`，两者之间只增加文档和运行配置，没有运行时代码变化。第七阶段尚未开始，Compiler 未读取、移植或运行。

## 已完成的实现

`odoo_runtime/knowledge.py` 已把知识索引和检索迁到原生运行时：索引身份覆盖实例、数据库、凭据、公司和字段策略；字段策略在写入缓存前执行；检索命中会按当前 Odoo 权限和内容重新验证，已删除、失权或变化的旧片段不会继续返回；新增或新近符合条件的记录明确要求重新索引。

索引和候选复核都按最多 100 条分页，并固定 `id asc`。任一页失败会在索引写入前终止。该修复解决了底层 read 工具静默把 200 条请求截成 100 条的问题，也覆盖了最多 500 个候选复核时的相同上限。

## 确定性与无模型门禁

- 根测试：`77 passed、1 skipped、32 subtests`。
- 超过 100 个匹配候选的回归：150 条全部重新验证，请求 offset 为 `[0, 100]`，没有 stale 或 error。
- 真实 Odoo capability gate：`.runtime/stage5/20260907T111914Z-75517/capability-gate`，结果 `passed`、`llm_calls=0`、24 个 native capability，知识索引及候选复核均走 native。

## 修复前一版的有效 A/B 证据

检查点 `28af984` 的 r6 已完成两个冻结案例：

| case | A：MCP knowledge | B：native knowledge | 结论 |
| --- | ---: | ---: | --- |
| 2262 | 100，62/62 | 100，62/62 | 双方自然结束；账本、路由和回执完整 |
| 2008 | 97.5，60/61 | 97.5，60/61 | 共同只缺 `po_origin_traceability`；业务约束 55/55 |

case 2008 两边均索引 109 条并命中 Compass Trading Co。两边相同的 97.5 来自模型都只在 PO `origin` 写入 `S00004`，不是 native 知识差异。该轮还证明此前 datetime 对账修复有效：没有 `needs_reconciliation`、`resource_busy` 或动作锁死。

## 最新 r7 为什么暂停

最新检查点 `54370d5` 的第一个最终复验位于 `.runtime/jobs/stage6-knowledge-ab-20260907-r7`：

| 指标 | A：MCP | B：native |
| --- | ---: | ---: |
| trial | `ACw9ZoP` | `fKLeYfA` |
| 得分 | 100 | 0 |
| 规则 | 62/62 | 4/57 |
| 终止 | natural STOP | PROVIDER_ERROR |
| 模型调用 / HTTP | 56 / 56×200 | 11 个模型记录 / 10×200 |
| token | 3,142,045 | 153,076 |
| verified 写入 | 24 | 0 |

A/B 的工具契约和 system prompt 哈希完全一致。B 的 19/19 个 World observations 成功，Odoo JSON-2 没有错误，路由没有 mismatch，动作账本完整但为空；模型尚未开始写入。

B 的 native knowledge 路径正常：索引 10/10、`possibly_truncated=false`，查询 `Clearwater Collective` 命中记录 11，重新验证 1 个候选，`stale=0`、`errors=[]`，stats 为 10 条。此次数据量没有触发新增的超过 100 条分页分支。

直接失败是 provider 连续两次返回 `stop`，内容只有 reasoning，没有可见文本或工具调用。Pi 的既有保护自动续跑一次，第二次仍为空后明确报错：

```text
Provider returned no visible response or tool call after one automatic continuation
```

相同的 provider 行为在早于 `54370d5` 的 r5 已发生过。因此没有证据把 r7 的 0 分归因于知识分页修复，也没有为它修改确定性业务代码。该轨迹保留为 provider 无效样本，不计入业务效果结论。

## B-only 有效复验

用户随后指定不再重复运行 A，只重跑同一 native B。配置 `configs/stage6-knowledge-native-b.json` 使用相同 case 2262、冻结快照、static 工具、controlled SOP、high reasoning 和原生 read/action/capability。Job 为 `.runtime/jobs/stage6-knowledge-native-b-20260907-r8`，trial 为 `jFjUnDb`。

| 指标 | r8 native B |
| --- | ---: |
| 得分 / 规则 | 100 / 62/62 |
| 终止 | natural STOP，有最终可见回答 |
| Agent / Job 时间 | 18.2 分钟 / 23 分 55 秒 |
| 模型请求 / HTTP | 38 / 38×200 |
| token | 1,713,326 |
| 工具调用 | 101 |
| native capability 调用 | 9 |
| 动作账本 | 16 verified |

请求响应 gap、未报告失败调用、后端 mismatch、backend error 和未完成调用均为 0；SOP、动作账本和 World 完整性均有效。知识索引为 10/10；两次检索分别命中 Clearwater Collective 和 Nimbus Bureau，每次重新验证 1 个候选，`stale=0`、`errors=[]`，stats 为 10 条。唯一工具错误是模型调用了未发布的 `mcp_odoo_get_current_time`，随后使用已发布的时钟工具恢复，不影响业务结果。

## 独立复核与结论

独立子 agent 核对 r8 的 session、HTTP、native knowledge、动作账本、World 和 verifier 回执后给出 `ACCEPT`：r8 足以将 r7 B=0 判定为 provider 无效样本。Stage 6 因此完成；没有补跑 A 或 r7 case 2008，没有回退或修改知识代码，也没有触碰 Compiler。
