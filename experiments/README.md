# 实验入口

产品实现位于 `src/erp_harness/`，桌面位于 `desktop/`。这里保存实验脚本、固定输入及验收摘要；完整日志和数据放在忽略的 `.runtime/`。

| 目录 | 用途 |
|---|---|
| [tool_routing](tool_routing/README.md) | Laya / OpenJev 能力选择、上下文重放与业务对照 |
| [agent_regression](agent_regression/) | 从真实失败提取的单轮模型回归 |
| [enterprise_validation](enterprise_validation/) | 模拟企业、查询与业务验收 |
| [desktop_workbench](desktop_workbench/) | 桌面交互与端到端实验 |
| [company_records_rag](company_records_rag/) | 企业材料检索对照 |
| [demo_odoo](demo_odoo/) | 隔离演示环境 |

早期阶段记录及脚本保留用于复现；[初始路线图](history/INITIAL_PLAN.md)已归档，其中时间、路径及执行约束属于当时实验。运行旧脚本前核对目标环境，不能把历史成绩当作当前验收。
