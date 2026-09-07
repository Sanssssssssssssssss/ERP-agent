# 第七阶段验收结果：无 MCP 原生 Harness 通过

更新：2026-09-08。结论：**Stage 7 已验收（ACCEPT）。** 被真实模型验证的实现检查点为 `7644f1a03d0e038603f494e246992b1015f6b4b7`。当前产物应称为**不含 Compiler 的 Odoo 原生 Harness**；Compiler 未读取、移植或运行，5.3 动态工具继续暂停，生产形态保持 static。

## 已完成的实现

- 原生启动直接构造固定的 41 个 Odoo `AgentTool`，不经过 `McpToolSet`、`tools/list`、`tools/call` 或 MCP prompt/resource。
- Odoo 业务纯函数和 `odoo_renames.json` 已从固定的 mcp-odoo 1.3.2 来源抽取到 `odoo_runtime/_odoo_core`，保留 MIT 来源；原生可达模块不再导入 `odoo_mcp`。
- `mcp` 与 `mcp-types` 变为 Pi 的可选依赖。Stage 7 使用独立的 36-wheel 原生环境，不继承系统 site-packages，也不安装 `mcp`、`mcp-types` 或 `odoo-mcp`。
- A 的历史 MCP 启动路径仍保留；B 使用显式 `runtime_mode=native`，不启动 sidecar、不读取 MCP URL，也没有静默 fallback。
- token、HTTP、工具路由、World、SOP、动作账本和 verifier 报告继续复用既有 infra。

## 确定性门禁

- 根测试：`81 passed、1 skipped、32 subtests`；Ruff import/format 检查通过。
- 干净 Linux 原生环境：38 个 distribution，三个 MCP distribution/import spec 均不存在；原生源码可加载 41 个工具及 34 条 rename 数据。
- 41 个 Odoo 工具及完整 44-tool 请求契约与第六阶段一致，契约哈希均为 `c80cc8a1bdebe426b74f725c4ec721280b7410c6f8e1e7a3f48ec40d17a221dd`。
- 原生 wheel SHA-256 为 `e83adf1b3c2d034fa6f904cbe223495995e6d291fea1a51ac124d26396f99580`；发布的干净 Linux wheelhouse SHA-256 为 `d70852fc7232c817347df41f23c74aadbe96613dd49b291caa4f99f6f14bed1e`。

## 同题 B-only 真实 API 验证

按用户要求未重跑 A，只运行此前第六阶段用过的 native B：case `2262_easy_26_buy_only_net_30_no_adjacent_data`，冻结快照、模型、reasoning、system prompt、工具契约、static 工具和 controlled SOP 均保持一致。Job 为 `.runtime/jobs/stage7-odoo-native-b-20260907-r1`，trial 为 `d89WFKx`。

| 指标 | Stage 6 同题 B | Stage 7 无 MCP B |
| --- | ---: | ---: |
| 得分 / 规则 | 100 / 62/62 | 100 / 62/62 |
| 终止 | natural STOP | natural STOP |
| 模型请求 / HTTP | 38 / 38×200 | 55 / 55×200 |
| token | 1,713,326 | 2,380,338 |
| Agent 时间 | 18 分 12 秒 | 32 分 30 秒 |
| Job 总时间 | 23 分 55 秒 | 38 分 12 秒 |
| 工具调用 | 101 | 110 |
| MCP 调用 | 1 | **0** |
| 动作账本 | 16 verified | 11 verified |

Stage 7 的原生运行收据为 `verified`：MCP distributions 为空，`mcp`、`mcp_types`、`odoo_mcp` 均不可导入，MCP 进程、8000 端口和 PID 文件均不存在。三个执行后端全为 native；68 次读取、23 次动作、8 次 capability 调用没有后端 mismatch 或 backend error，11 个动作全部 verified。请求/响应 gap、未报告失败调用和未完成 dispatch 均为 0；World、SOP、动作账本及快照完整性均有效。

工具名继续保留 `mcp_odoo_` 前缀，以锁住与前一阶段完全相同的模型契约；它只是兼容名称，不代表运行时经过 MCP。终端中 Harbor 的历史注册名同理，最终判定依据是运行收据和实际 dispatch 后端。

本次单样本与 Stage 6 同分，但 Stage 7 多用 667,012 token（+38.9%），Agent 时间多 14 分 18 秒（+78.6%）。这是一次随机模型轨迹，足以证明无 MCP 部署可用，不能据此声称效率改善或退化；若研究性能，需要另立重复样本实验，而不是污染本阶段迁移验收。

## 验收边界

独立复核已核对真实运行的 session、HTTP、原生收据、工具路由、World、动作账本和 verifier，结论为 `ACCEPT`。原始 `pi-agent-odoo.jsonl` 与前阶段一样不能由旧适配器解析，报告明确回退到完整 `pi-agent-session.jsonl`；55 个模型响应、55 个 HTTP 回执及请求/响应 gap=0 证明观测没有缺失。离线报告器也已修正，不再把自然 STOP 前已恢复的工具错误误标为终局 `diagnostic_failure`。

Stage 7 完成的是 Odoo 19、static 工具、受控 SOP、原生读写/能力/知识及完整观测的部署闭环。没有验收动态工具 5.3，没有加入 Compiler，也不承诺没有 Odoo 服务端配合时的远端严格 exactly-once。
