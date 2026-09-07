# 第六阶段阶段性结果：实现完成，最终复验暂停

更新：2026-09-07。结论：**Stage 6 当前为 HOLD（等待有效样本），不是代码回退。** 原生知识实现检查点为 `54370d57a527306098c457e6acbcbcc8dae96a01`；第七阶段尚未开始，Compiler 未读取、移植或运行。

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

相同的 provider 行为在早于 `54370d5` 的 r5 已发生过。因此当前没有证据把 r7 的 0 分归因于知识分页修复，也不应为它修改确定性业务代码；但按“两案例且每组自然完成”的验收规则，这次不是有效 B 样本，Stage 6 不能标为 ACCEPT。

## 独立复核与停止点

独立子 agent 结论同样是 `HOLD（等待有效样本）`：不回退、不修代码。最小下一步是在同一提交、同一冻结快照只重跑失败的 native B 一次；若自然结束，再运行 case 2008。按用户约定，本轮已经停在第一次失败报告：没有启动 r7 case 2008，没有进入第七阶段，也没有触碰 Compiler。
