# 后端清理验收 · 2026-09-30

三批实现完成；两条真实模型业务均通过。回退点：`backend-cleanup-baseline-20260930`（`df00904`）。

| 批次 | 提交 | 处理内容 |
|---|---|---|
| 1 | `93c5561` | 删除旧 Pi 自说明、CLI/TUI 示例及终端依赖；保留 HTML 会话导出。Mem0 改可选依赖，桌面保留开关，默认关闭。 |
| 2 | `6afe66f` | 旧路由上下文和 OpenJev 移至实验目录。生产选择器使用 Laya；模型清单校验安装包文件，桌面转发可信启动配置。 |
| 3 | `704c409` | 产品入口只接受原生后端；MCP 连接由独立 benchmark 入口持有。打包不依赖 benchmark 目录。 |

三批共净减 1,000 行；生产 `src` Python 净减 894 行，其中包含移至实验区的代码，不全部算删除。基础 agent 引擎、动作校验、审批、回读和历史会话保留。

`mcp_odoo_*` 工具名、旧环境变量别名和历史文件名仍作为兼容契约保留。产品没有 MCP transport 导入或连接；改名需单独迁移历史工具调用。独立 `bench/reference` 保留原对照和许可证。

## 真实业务

DeepSeek v4.1 Flash / high；Laya v7 GPU；Mem0 关闭。隔离 Odoo `18209`，两份一万单据快照；每题一次，审批逐项核对。耗时包含审批等待。邮件仅投递本机 Mailpit。

| 案例 | 最终核验 | 模型 / 工具调用 | 新输入 | 缓存输入 | 输出（含 reasoning） | reasoning | 总 token | 耗时 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SALE：S00006 更新参考号并确认 | 6/6；金额、明细不变，未开票/交付；2 个动作 verified | 11 / 13 | 17,642 | 86,528 | 4,879 | 3,423 | 109,049 | 227 秒 |
| MAIL：发票 PDF、审批改稿、发送回读 | 9/9；同一业务/run 续跑，旧稿撤销、3 个动作 verified；仅发送一次，附件哈希一致 | 17 / 28 | 32,296 | 283,776 | 15,790 | 12,261 | 331,862 | 528 秒 |

两题均无 compaction、无缺失用量、无付费重跑；28 次 Laya 决策全部正常，无能力恢复调用。两题通过只证明这些路径；没有据此宣称整体成本下降。

## 自动检查

- 原生测试：1,287 passed、4 skipped、182 subtests；独立 MCP 对照：36 passed。
- 历史 E02/E03 GPU 重放：37/37 选择一致。
- 桌面 typecheck、构建、自检、renderer、默认编排与 Laya 的真实 IPC 检查通过。
- wheel 离开源码目录可导入；无 experiments/MCP/Textual 运行依赖；CPython sidecar 打包通过。
- IPC 的旧模拟模型曾强制要求 `configure_odoo_tools`。已按实际工具面选择，并在缺能力时使用现有恢复工具；两种编排均复验通过。该检查使用本地模拟模型，不计入真实业务成绩。

## 证据与入口

- [SALE 用量](../.runtime/backend-cleanup-live-20260930/SALE/summary.json)、[业务回读](../.runtime/backend-cleanup-live-20260930/SALE/after.json)。
- [MAIL 用量](../.runtime/backend-cleanup-live-20260930/MAIL/summary.json)、[业务回读](../.runtime/backend-cleanup-live-20260930/MAIL/after.json)、[续跑与附件核对](../.runtime/backend-cleanup-live-20260930/MAIL/supplementary-checks.json)。
- 完整请求、响应、trace、审批账本：上述各案例的 `profile/data/runs/`；[冻结输入和源码](../.runtime/backend-cleanup-live-20260930/frozen.json)。
- 检查日志：`.runtime/backend-cleanup-20260930/`；本机已验收选择器配置：同目录 `selector/config.json`。
- 复用入口：[业务实验](../experiments/tool_routing/live_trial.py)、[测试索引](../tests/INDEX.md)。完整日志、数据库与模型留在本机忽略目录。

本轮完成源码及 sidecar 验证；未上传发布包，也未替换用户正在运行的桌面进程。
