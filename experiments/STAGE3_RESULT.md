# 第三阶段验收：World 观测状态与语义回执

更新：2026-09-04。结论：**第三阶段的 World 记录核心已验收，可以进入第四阶段；上下文投影不晋升为默认能力。** 运行代码检查点为 `da229bf12475c2aef38a8dc027c0f2ed66a972a6`，r2 实验配置检查点为 `883148e`。Compiler 与 C 组均未读取、移植或运行。

## 已验收边界

`odoo_runtime/world.py` 以追加式 JSONL 保存身份隔离的读取观测、字段覆盖、关系引用、错误分类、RPC 证据、版本和失效信息；区分未知、未读、隐藏和确认为空。局部读取不会伪装成完整记录，乱序旧 generation 不进入当前状态，写动作无论成功或失败都会使受影响目标失效。恢复会重新校验回执，报告按实际合并语义重算并拒绝缺失、损坏或不闭合的 World。

`world_mode=record` 只在模型上下文外记录，不改变模型输入。`world_mode=project` 只会用可追溯摘要替换更早的相同读取结果，保留最新原文和工具调用/结果配对；任何不确定情况都保持原文并 fail-open。runner 与 Harbor 的默认值仍为 `off`，不会无意启用实验投影。

## 三层验收

1. **确定性检查：** Harness 为 **33 passed、1 项既有 Node-only skip、29 个 subtests passed**；完整固定 MCP 套件为 **931 passed**。覆盖重复读、局部覆盖、身份隔离、关系、版本冲突、乱序、删除/外部更新失效、恢复、投影配对、报告重算和真实 `CodingSession` 请求变换。
2. **真实 Odoo、无模型：** `.runtime/stage3/20260903T212439Z-1163` 在管理员与受限身份上完成 **42/42** 对照，`llm_calls=0`、`world_external_update=true`。两个 World 分别记录 21 和 23 条观测，均 healthy；快照为 550 张表、445 个文件，17 个回执文件内 API key 精确匹配为 0。`source-commit.txt` 锁定 `da229bf`。
3. **真实模型 A/B：** `.runtime/jobs/stage3-world-ab-20260904-r2` 使用同一份 case 2262 快照、同一模型/high reasoning、工具契约和系统提示，串行运行。A=`native+record`，B=`native+project`；唯一 Harness 开关差异是 `world_mode`。两组均自然 `STOP`、100 分、62/62 条适用规则通过（另 1 条 NA），无 Harbor/Agent 异常。

| 指标 | A：record | B：project |
| --- | ---: | ---: |
| trial | `xxUQwap` | `BATuXxK` |
| 模型请求 / HTTP 200 | 49 / 49 | 65 / 65 |
| 模型可见工具 / 实际分发 | 121 / 47 MCP + 74 native | 166 / 64 MCP + 102 native |
| World 观测（成功 / 失败） | 74（65 / 9） | 102（85 / 17） |
| World 当前 records / relations / invalidations | 97 / 72 / 26 | 140 / 94 / 33 |
| 投影调用 / 被替换消息 | 0 / 0 | 65 / **0** |
| fresh input | 51,360 | 51,866 |
| cached input | 1,800,704 | 2,559,104 |
| output | 153,903 | 172,142 |
| reasoning（已含于 output） | 137,595 | 152,902 |
| token 合计 | 2,005,967 | 2,783,112 |

两组 World integrity 均为 valid/healthy，请求、响应、usage 和工具分发闭合；`maxTurns=null`、`maxOutputTokens=null`。两组都验证相同快照 SHA-256 `169886cd1c4fa78290816fe57288f0d00b68f596b15be7eaa7236acc324a8eb1`，包含 550 张表和 445 个文件。供应商未给可靠价格，费用保持 unknown。

## 400 修复与实验解释

r1 的 B 在第 23 个请求收到外部 `400 Content Exists Risk`。该次请求前的 `get_model_fields(res.company, max_fields=100)` 返回了包含已由固定上下文二分定位为风险内容的 `chart_template` 大枚举；同时 23 条 projection 回执全部为零，证明 World 没有改动那些请求。报告的 records 50/51 则是离线重算把 `stale_generation` 目标误计为当前状态，运行时合并本身正确。

共同根修复把 `max_fields` 参数在 MCP schema、MCP 实现和 native 实现三处统一限制为 1..30；它控制默认 ranked schema 路径，显式 `field_names` 在合法参数范围内仍完整返回，`relevance=null` 的显式全量能力保留。报告只排除实际未进入当前 World 的 `stale_generation` records。r2 的 A、B 均跨过第 23 个请求且全部 HTTP 200，支持“已知 schema 风险得到缓解”；由于完整轨迹是随机的，这不是旧/新请求的固定重放，不能宣称风险永久消失或证明 r1 只有一个可能原因。

B 的 65 条 projection 回执均为 `compacted_call_ids=[]`、`compacted_messages=0`、零字节变化，实际请求也没有 World projection 内容。因此本对实验**没有测到投影收益**；B 更多的轮次和 token 是独立模型轨迹差异，不能归因于 World。第三阶段只晋升记录核心，project 继续显式 opt-in，默认保持 off。单一满分 A/B 也不支持性能、成本或普遍质量优劣结论。

两组主 stdout JSONL 都有解析告警，但完整 session、HTTP、usage 和 dispatch 回执闭合，报告明确回退到记录了 SHA-256 的完整 session，属于非阻断观测问题。原始会话和业务数据只保存在忽略提交的 `.runtime/`。

独立子 agent 两次复核实现、测试和 r2 原始回执，结论均为无阻断；它明确要求只验收 record core，并保持 project 为非默认实验能力。
