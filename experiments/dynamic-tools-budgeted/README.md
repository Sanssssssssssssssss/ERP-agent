# Budgeted dynamic tools module slice

## 冻结与本轮计划

- 原生基线：`98479c0926a1fb382c2653fece597577fd105868`，本地标签 `baseline-stage7-98479c0`。
- 实验分支：`experiment/dynamic-tools-budgeted`。不改变 static 默认值，不把 Stage 5.3 的历史 HOLD 改为整体验收通过。
- 实现变量：复用动态控制器，首次 `configure_odoo_tools` 在宿主内部完成可用性探测；模型知道能力组时不必先单独调用 list。发布仍下一轮生效，执行时权限检查保持。
- 比较：同一 provider/model/reasoning、业务指令及原生工具契约，static 44 工具与 dynamic 初始 14 工具。两组均运行当前实验代码；static 所用运行链及固定工具契约未从冻结基线改变。
- 预算：每组最多 5 个模型请求，每请求最多 2048 输出 token，付费执行段总超时 300 秒；关闭 provider/session 自动重试及自动压缩。两组各一次，遇到预算或 provider 失败即保留 partial，不自动续跑。
- 任务：生成指定销售单读取请求预览，并查询旧会计模型的迁移记录。只有模块可用性探测使用固定 fixture；两个报告调用运行仓库原生纯函数。结果参数和自然结束必须同时通过。
- 边界：这是短工具调用片段的真实模型实验，不包含活跃 Odoo 数据库、业务写入或 ERP-Bench 终态评分；单次结果不能代表整题 token 节省或动态工具的普遍业务质量。
- 本地原始请求、会话和结果写入 `.runtime/dynamic-tools-budgeted/`；不复制凭据到实验文档。

离线 review：动态控制器 6 项 unittest、现有 CodingSession 下一轮发布/同轮拒绝回归和探针 self-check 已通过。首次会话测试因临时目录父目录不存在而未执行，补齐目录后通过；未记作实现失败。

## 真实 API 结果（2026-09-08）

结论：**短工具片段两组均通过，dynamic 的观测总 token 少 37.83%；未证明费用或速度改善，也未验收完整 ERP 业务。** 不追加付费样本。

有效对照的执行提交为 `99b3ee9a51243c289103695251092ee96fca669e`，模型为 `deepseek/deepseek-v4-flash`，两组均使用 `high` reasoning，业务指令 SHA-256 为 `7a635f80efea99022f8d4cbf637a0a874056c82ddd2d3e1c634b58d85bacc1c4`。

| 指标 | static（r2） | dynamic（r2） |
| --- | ---: | ---: |
| 片段结果 | passed，自然 stop | passed，自然 stop |
| 实际模型请求 / HTTP 200 | 3 / 3 | 4 / 4 |
| 每轮工具数 | 44 / 44 / 44 | 14 / 14 / 19 / 19 |
| 工具 schema 字节合计 | 98,700 | 42,502 |
| 新鲜输入 token | 1,185 | 9,660 |
| 缓存输入 token | 29,312 | 9,088 |
| 输出 token | 913 | 780 |
| reasoning（已包含在输出中） | 264 | 197 |
| 总 token | 31,410 | 19,528 |
| 片段耗时 | 11.028 秒 | 11.428 秒 |
| MCP 安装/实际导入检查 | 无 / 无 | 无 / 无 |

总 token 减少 `31,410 - 19,528 = 11,882`，即 `11,882 / 31,410 = 37.83%`。工具 schema 字节减少 56.94%，字节不是 token。实际请求回执均包含 `max_completion_tokens=2048` 与 `reasoning_effort=high`，请求/响应文件一一对应。

两组真实调用原生报告函数，生成的请求 body 均满足指定模型、方法、domain、fields 与 limit；模型历史返回正确的 `account.invoice → account.move` 记录，最终回复与结果一致。static 出现一次参数 wrapper 格式错误并恢复；dynamic 尝试了一次本片段未开放的 health_check，收到明确 unrouted 错误后完成目标。工具失败和恢复均保留，不删减轨迹。

### 实现收益与证据边界

- dynamic 本次仍选择 `list_odoo_capabilities → configure_odoo_tools`，因此没有证明新增加的直接 configure 路径减少真实模型轮次；该路径仅已通过离线测试。
- 本片段的较小工具目录减少了模型可见工具信息，但比较包含随机决策轨迹差异，不能把全部 token 差额归因为单一代码改动。
- static 对照运行前已有预试样本，缓存明显更热；dynamic 的新鲜输入反而更多。没有可靠单价和匹配缓存条件，不能把总 token 的降幅当成费用降幅。
- dynamic 多了一次模型请求，耗时略增；这一次没有显示运行速度改善。
- 此处没有实际 Odoo 数据库、审批/写入、故障恢复或 ERP-Bench 终态评分。Stage 5.3 的整体 HOLD 与 static 默认配置继续保持。

### 预试修正、预算与原始依据

首个 static 预试（提交 `4048408963ce109b41194ec4dbc307ffee0c9984`）用了 3 次请求、31,290 token。探针当时错误地要求 `args=None`，拒绝了原生报告函数允许的 args/kwargs 组合，制造额外纠错。虽然最终片段通过，该样本**不用于节省对比**。修正仅删除探针的额外限制，并离线覆盖两种等价参数形式；原始收据不修改。

修正后 static 请求上限设为 3，dynamic 使用剩余 4 次预算；两者均自然结束，未触发预算终止。包括预试在内，本轮总计 **10 次真实请求，全部 HTTP 200，82,228 个观测 token**。有效对照本身为 7 次请求、50,938 token；实验支出和单次运行成本分别列出。

原始结果都在忽略目录 `.runtime/dynamic-tools-budgeted/`：

| 本地 trial / trace.json | 用途 | trace SHA-256 |
| --- | --- | --- |
| `20260908-r1-static/trace.json` | 排除的预试 | `0b0b8f652df57a4803183beda991f76854a95c07a7d6b1d74523093254df7a58` |
| `20260908-r2-static/trace.json` | 有效 static 对照 | `01bc77a9d74aa69e61b254596a36c21c8934e18a4aee31242a2f93feafadb166` |
| `20260908-r2-dynamic/trace.json` | 有效 dynamic 候选 | `8639b19b6b209f3d052acef2ae463fdd640b79eb929620842bafe52edbff65fa` |

各目录保留实际 `requests/`、`session.jsonl`、工具结果、usage 和动态发布回执。运行源代码已本地提交；本轮没有推送远端。

This experiment compares the existing full native 44-tool contract with
`DynamicToolController`'s 14-tool initial snapshot. It uses a real
`CodingSession` and provider when explicitly requested, but starts no Odoo or
MCP service. `search_records` is only a fixed `ir.model` inventory fixture for
capability availability; the two executed tools are the repository's pure
`generate_json2_payload_report` and `lookup_model_history_report` functions.

Run the offline check with the repository runtime:

```powershell
$env:PYTHONPATH='E:\GPTProject2\pi-odoo-harness-lab\agent\src;E:\GPTProject2\pi-odoo-harness-lab'
& 'E:\GPTProject2\pi-odoo-harness-lab\.runtime\stage7-clean-win-final\Scripts\python.exe' experiments/dynamic-tools-budgeted/run_module_slice.py --self-check
```

The real run requires `--run`, a new `--output` directory, and the existing
`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` environment. It keeps each group's
provider calls at five by default and writes only local receipts and
`trace.json`; authentication request headers and credentials are never persisted.
