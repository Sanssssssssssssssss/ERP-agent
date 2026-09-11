# 第二阶段验收：完整原生读取表面

更新：2026-09-04。结论：**第二阶段已验收，可以进入第三阶段。** 代码运行检查点为 `60b421ab262bafbae693606462b7c73985b5b528`；验收文档提交和标签位于其后。Compiler 与 C 组均未读取、移植或运行。

## 实现边界

全部 11 个公开读取工具现已由 `odoo_runtime/gateway.py` 和 `odoo_runtime/reads.py` 原生执行：profile、字段 metadata、search/read、实例/模型/catalog、附件、聚合、员工与请假。写入、诊断和其他尚未迁移的能力继续走固定 MCP。

原生连接边界只允许已审核的只读方法和命名参数；身份、公司、语言、transport、数据库头和策略版本均参与缓存隔离。metadata 失败关闭；附件执行大小、Base64、校验和及同请求刷新检查；聚合只在确认方法不存在时兼容回退，权限错误不回退。字段策略覆盖普通读取、schema、附件、聚合和 HR 封装。原生请求、缓存、N+1 与限速状态写入统一本地回执。

当前仍复用固定 MCP 源树中不加载服务和 SDK 的纯函数；这是路线图允许的迁移期边界，第七阶段再做独立安装闭包。没有复制第二套读取实现或增加新框架。

## 三层验收

1. **确定性检查：** 固定 MCP 测试 931 项通过；Harness 测试 24 项通过，1 项既有 Node-only 检查跳过。覆盖字段模式、domain/order、缓存身份与失效、策略、聚合兼容、附件边界、错误传输、N+1 和 backend 回执。
2. **真实 Odoo、无模型：** r8 在两个身份上完成 21 个 case，共 **42/42** 对照通过，覆盖全部 11 个读取工具、正常 ACL/记录规则、拒绝的 search/aggregate domain、schema 标记和附件校验；`llm_calls=0`。来源回执位于 `.runtime/stage2/20260903T170634Z/read-gate-r8/source-receipt.json`，锁定上述代码提交及 `gateway.py`、`reads.py`、汇总文件哈希。员工/请假模块在该基础镜像未安装，因此这里只证明旧 MCP 与原生对缺失模块的等价处理；安装模块后的成功路径留给对应业务 fixture。
3. **真实模型 A/B：** `.runtime/jobs/stage2-full-reads-ab-20260904-r2` 使用同一份 case 2262 快照、同一模型与 high reasoning、相同工具契约和系统提示，串行运行一对。A 为 Python Pi + 固定 MCP；B 为同一 Pi、11 个读取工具原生、其余工具仍走 MCP。两组均自然 `STOP`、100 分、62/62 适用规则通过（另 1 条 NA），无 Harbor/Agent 异常。

| 指标 | A：固定 MCP | B：完整原生读取 |
| --- | ---: | ---: |
| trial | `rXL45hb` | `UG4bWj6` |
| 模型请求 / HTTP 200 / assistant usage | 47 / 47 / 47 | 58 / 58 / 58 |
| 模型可见工具结果 | 125 | 96 |
| 实际分发 | 125 MCP | 40 MCP + 55 native |
| 工具错误 | 15 business | 7 business + 1 protocol |
| JSON-2 尝试 / 错误 | 115 / 9 | 85 / 5 |
| fresh input | 42,977 | 45,033 |
| cached input | 1,634,816 | 2,352,896 |
| output | 132,552 | 136,383 |
| reasoning（已含于 output） | 114,852 | 120,990 |
| token 合计 | 1,810,345 | 2,534,312 |

B 的 55 次原生调用来自 7 种迁移读取工具；本轨迹未调用附件、聚合、员工、请假，不能把真实模型覆盖夸成 11/11。其余 4 种由同提交的真实 Odoo 42/42 gate 覆盖。B 的原生 telemetry 为 cache hit 2、miss 12、N+1 空、rate-limit mode `off`。

## 一致性、例外与解释边界

- 两组快照 SHA-256 都是 `619f33b4b099c9eaa6544496562a36a87864eecd48146303fde73d4bdf9e70bb`，均验证 550 张表和 445 个文件。输入配置 SHA-256 为 `2dfd8cf7d5d80ac40b8b7bc553c989d24a6dbc86a884343e6f5b3578ef6e15d3`；Harbor 归一化配置为 `da253800ab810e0e67827cfc30b4b1c6c437cf27db459e94f36b61d3043418b0`。
- 两组首请求字节一致，SHA-256 为 `8cc049d2095ed99d0994cedcae2d617cdc37b3b6a5175be48d30b91312f84cb4`；模型均为 `deepseek/deepseek-v4-flash`、high reasoning，无轮数和输出 token 上限。
- B 比 session 工具结果少 1 次 backend 分发：模型把仍走 MCP 的 `execute_approved_write` 参数双层编码进 `_raw_arguments`，工具 Schema 因缺顶层 `approval` 在分发前拒绝。它未进入 MCP/native backend，也未请求 Odoo；轨迹随后恢复并满分，不是原生读取回归。
- r1 因启动时遗漏 `PI_ODOO_LAB_ROOT` compose 变量，在创建环境前失败；A/B 都是 0 trial、0 模型调用。失败目录保留，没有覆盖 r2。
- B 的 `pi-agent-odoo-mcp.jsonl` 前 13 行混入不含密钥值的客户端启动文字，报告已明确告警并使用完整、0 坏行且哈希锁定的 `pi-agent-session.jsonl`；请求、backend、native JSONL 均闭合。早期 `.runtime/stage2/.../source-commit.txt` 仍写旧提交，但最终 r8 source receipt、实验清单和配置哈希都锁定 `60b421a`，以最终回执为准。
- 供应商没有可靠美元金额，费用记为未知，不估成 0。单一 A/B pair 只证明累计 Harness 链路可用；B 在本次轨迹中工具/JSON-2 较少，但模型请求、token 和耗时较多，不能据此宣称质量、速度、token 或成本提升。

独立子 agent 已交叉核对 Harbor、session、逐请求回执、backend 分发和 Odoo JSON-2 日志，确认请求/响应、工具 start/end 和 token 拆分闭合；结论为**无阻断，可晋升第二阶段**。

原始会话与业务数据只保存在忽略提交的 `.runtime/`；GitHub 只保存代码、配置模板、来源记录和本结论。
