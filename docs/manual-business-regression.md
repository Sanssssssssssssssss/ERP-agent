# 四条手动业务：问题与验证

回退点：`manual-business-baseline-20260930`。四个原会话及其执行、对话请求共冻结 922 个文件；原 Demo 保留。

本轮结果见 [验收报告](manual-business-results.md)：完整业务 3/4 通过；后补修不覆盖旧成绩。

| 分类 | 根因与修复入口 | 回归 |
|---|---|---|
| 取消与诊断 | 采购取消缺少方法契约；策略拒绝被归为未知。`erp/actions.py`、`business_operations.py`、`tools/run_diagnostics.py` | M03、M04；P00001 |
| 派生单据、部分备料 | 同业务已验证创建的制造单不在授权目标；部分分配误报未知。`erp/task_evidence.py`、`business_operations.py` | M07、M08；CC-0980 |
| 范围与恢复 | 无业务也要求选择；核对前的修改提案无明确状态。`app/host.py`、`conversation.py`、`business_status.py` | M01、M09 |
| 业务依据 | 只读面缺 BOM/库存；历史价格日期被当当前承诺。扩充已有只读字段、拒绝替代采购复制过期日期 | M02、M05、M06 |
| 表达与完成 | 技术化回复、运行结束与业务完成混淆。业务结果说明、独立完成检查 | M10；M11、M12 正常保护 |
| 审批与界面 | 名称未投射、异步状态覆盖、卡片先于回复。复用审批、设置、流式消息组件 | `desktop/scripts/approval-presentation-check.mjs`、`renderer-check.mjs` |
| 文件抽取 | 仅 TXT/CSV。统一 XLSX、PDF、图片本机解析；保留原件、哈希、页码、告警 | `tests/test_material_extract.py`、附件队列压测 |

运行入口：

```powershell
# 历史 12 节点；freeze / prepare 离线，paid 为 24 次单轮请求
.venv/Scripts/python.exe -m experiments.agent_regression.manual_business freeze
.venv/Scripts/python.exe -m experiments.agent_regression.manual_business prepare
.venv/Scripts/python.exe -m experiments.agent_regression.manual_business paid
# 修正已发现的旧宿主指令混入；只生成独立离线候选，零模型调用
.venv/Scripts/python.exe -m experiments.agent_regression.manual_business repair_host_context
# 四个隔离业务：每个 attempt 只允许一次启动
.venv/Scripts/python.exe -m experiments.agent_regression.manual_business_live SALE
# 只读工具并发与 10 个附件队列；需要 dev 依赖
.venv/Scripts/python.exe -m experiments.enterprise_validation.manual_pressure
```

冻结目录已存在时不覆盖，已付费分支不自动重跑。模型提出的单轮工具调用只保存、不执行。其他完整业务参数为 `PO`、`DEPOSIT`、`MO`；必须先准备独立数据库，逐项检查 `pending-approvals.json` 后写入操作员决定。

证据保存在 [本次目录](../.runtime/manual-business-20260930/)：`pool/index.json` 为案例索引，`pool/report.json` 为原始 A/B 判定；`live/<场景>/` 包含输入、审批、完整请求、账本、独立数据库回读及用量。`live/frozen.json` 记录完整业务源码。

隔离业务使用一万单据快照重建前置条件，**不是原数据库精确重放**。模拟到货及制造完成须由冻结操作员脚本逐项确认，不连接真实资金或对外邮件。

验收以最终业务状态、安全约束、效率分别判断。工具顺序不是标准答案。中文进度和业务表达须看原始模型文本，界面展示测试不能替代其验收。
