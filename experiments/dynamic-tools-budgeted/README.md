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

真实运行结果将在本次有限实验结束后补充。

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
`trace.json`; headers and credentials are never persisted.
