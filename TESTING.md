# 测试导航

先按能力找入口，再看原案例的冻结条件。**索引存在不代表测试已通过。**

| 能力 / 问题 | 离线保护 | 真实模型与业务证据 |
|---|---|---|
| 9 月 30 日四条手动业务、取消/派生制造/审批/文件抽取 | [问题分类与入口](docs/manual-business-regression.md)、[材料](tests/test_material_extract.py) | [12 个真实节点](experiments/agent_regression/manual_business.py)、[四条完整业务](experiments/agent_regression/manual_business_live.py)、[结果](docs/manual-business-results.md) |
| HITL 修改意见、邮件改稿、断点续跑 | [宿主](tests/test_workbench_host.py)、[续跑](tests/test_interrupted_recovery.py)、[邮件](tests/test_invoice_mail.py)、桌面交互检查 | [HITL01/02 与 B02/03 分类清单](experiments/agent_regression/hitl_revision.json)、[入口及手动模板](docs/hitl-mail-revision.md) |
| 中断、待核对、聊天不知道状态 | [恢复测试](tests/test_interrupted_recovery.py)、[宿主](tests/test_workbench_host.py)、[状态回读](tests/test_business_status.py) | [IR01/IR02 原因与约束](experiments/agent_regression/interrupted_recovery.json)、[本次结果](docs/interrupted-recovery.md) |
| 审批、授权范围、写入依据 | [动作](tests/test_actions.py)、[阶段](tests/test_stage_contract.py)、[证据](tests/test_task_evidence.py) | [A/B/C/D 节点](experiments/agent_regression/cases.py)、[结果](experiments/agent_regression/RESULTS.md) |
| 员工岗位、跨公司、人事隐私 | [Odoo 事务与在线权限检查](experiments/enterprise_validation/access_check.py)，不调用模型 | [边界、结果与回退](docs/enterprise-access.md) |
| 发票、邮件、附件 | [投递](tests/test_invoice_mail.py)、[开票条件](tests/test_invoice_eligibility.py)、[文件导出](tests/test_document_export.py) | [S01499 及历史故障](experiments/agent_regression/README.md)；邮件创建、SMTP 接受、实际送达分别判断 |
| 本地模拟发票、贷项、实际邮件附件 | [只读复核入口](experiments/enterprise_validation/invoice_demo.py)：`--check` | [演示步骤和边界](docs/invoice-demo.md)；未接税局、不调用模型 |
| 采购、库存、制造、收付款、退款 | [企业动作](tests/test_enterprise_actions.py)、[供需](tests/test_supply_context.py) | [E01–E06 企业验收](experiments/enterprise_validation/README.md)、[全部 300 道 ERPBench](tests/INDEX.md) |
| SOP、工具参数、Laya 能力注入 | [SOP](tests/test_sops.py)、[Laya](tests/test_laya_integration.py)、[路由状态](tests/test_host_routing_state.py) | [R01–R18](experiments/agent_regression/README.md)、[选择器数据来源](tests/fixtures/capability_routing/README.md)、[实验结果](experiments/tool_routing/README.md) |
| ERPBench 猜字段、无效参数、采购来源容量与结束漏核验 | [实际参数与 API 边界](tests/test_bench_tool_regression.py)、[执行诊断](tests/test_run_diagnostics.py)、[采购分配](tests/test_purchase_allocation.py) | [43 个实际故障与正常对照](experiments/agent_regression/bench_recovery_cases.json)、[根因修复、纠错及用量](docs/bench-root-cause-20261002.md)、[字段自我纠错审计](docs/field-self-debug-20261003.md) |
| 故障原因与 self debug 信息保留 | [44 工具 × 17 类故障，无缺口豁免](tests/test_self_debug_audit.py)、[真实上下文与单次 API 边界](tests/test_self_debug_recovery.py)、[后台与跨实例](tests/test_capability_failures.py)、[可信业务拒绝与未知发送](tests/test_typed_action_failures.py) | [44 个节点的 API 检查、用量及完整业务复跑](docs/tool-self-debug-20261003.md)、[新业务根因与测试绑定](experiments/agent_regression/self-debug-fresh-20261003/bindings.json)；离线注入与下一次决策分别报告 |
| 搜索、中文检索、大查询回包 | [企业检索](tests/test_enterprise_knowledge.py)、[工具检索](tests/test_tool_retrieval.py) | [固定检索与并发检查](experiments/enterprise_validation/retrieval_check.py) |
| 上下文、缓存、记忆、模型协议 | [运行时测试](tests/runtime)、[长期记忆](tests/test_long_term_memory.py) | [全部实验入口](tests/INDEX.md)；训练集与留出集分别统计 |
| 桌面、Trace、文件上传、IPC | [桌面检查入口](desktop/scripts/self-check.mjs)、[宿主材料](tests/test_workbench_materials.py)、[Trace](tests/test_trace_inspector.py) | [浏览器交互检查](desktop/scripts/renderer-check.mjs)；不代替真实业务验收 |

[完整能力索引](tests/INDEX.md)列出每个测试套件、桌面检查、实验入口、固定模型节点、选择器数据集及 300 道基准题。文件保留原位置；历史 MCP 参考单列。

日常离线检查（不调用模型）：

```powershell
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m experiments.test_index --check
npm --prefix desktop run typecheck
npm --prefix desktop run self-check
```

新增故障的维护顺序：

1. 保存实际 trace 的根因位置和首次可纠正请求；完整请求、工具定义、ID、哈希保存在 `.runtime/<实验>/`。
2. 在 `experiments/agent_regression/` 保存无密钥案例清单；标明能力、故障层、允许替换位置和固定业务约束。人工边界留在离线测试。
3. 修改生产实现，用真实代码生成候选；显式启动所需的局部 API 分支。返回工具只保存，不执行；失败不自动补跑。
4. 每个案例与合计分别报告请求数、新输入、缓存读取、输出（含推理）、推理子集、压缩与总 token；缺失值和费用保持未知。短报链接到请求、响应、动作账本及 Odoo 回读，区分模型决策、工具契约、宿主保护和业务核验；不要求 Golden Trace 一致。
5. 执行 `python -m experiments.test_index` 更新索引。CI 检查是否遗漏新文件；不要搬动冻结证据。

日志约定：仓库只放源码、清单和简短结果；完整上下文、凭据、数据库、模型权重留在本机忽略目录。历史报告按其源码哈希解释，不能当作当前版本通过。
