# 公司订单记录检索实验交接

起始提交：`76e1c0e`。本目录是交接骨架，尚未实现检索器。

## 目的

验证销售订单、订单明细、客户三类关联记录的只读检索接线。这里的样例是开发示例，不是 holdout，也不代表真实数据、性能或准确率。

旧仓库 `E:/GPTProject2/erp-agent-odoo/backend/app/tools/rag_search.py` 可参考 txtai hybrid、持久化索引和来源封装；旧发票业务评分、MiniLM 的中文效果、按全量文件签名重建都不能直接视为本实验合格方案。

当前 `odoo_runtime/knowledge.py` 与 `reads.py` 的权限和回读边界必须保留。document 操作文档 RAG 独立存在，本次不修改、不接入。

## Fixtures 约定

- 每行一个 JSON 对象，顶层含 `schema_version`、`synthetic`、`record_key`、`database`、`company_id`、`model`、`record_id`、`write_date`、`fields`、`text`、`source_records`。
- `record_key` 是 `database/model/id` 的稳定键；订单主记录使用 `sale.order`，`fields.name` 保存 SO 编号。
- `fields` 保存状态、日期、币种、金额和客户结构化信息，供精确金额、日期、统计路由使用。
- `text` 是不用 LLM 的中文模板文本，仅用于关键词和向量语义检索。
- `source_records` 必须列出订单自身、客户和每条明细的 model/id/write_date，便于子记录变化追踪。
- 样例记录全部是 `synthetic: true`；查询的 `trusted_scope` 必须同时含 `database` 和 `company_id`，不能从查询文本推断权限。
- `queries.example.jsonl` 的 `kind` 为 `semantic`、`exact` 或 `aggregate`；后两类只验路由，不纳入 semantic Recall，aggregate 的空 `expected_record_keys` 不表示统计结果为零。

## 交给下一位 Codex 的开发任务

先建立只读导出和记录级持久索引，支持关键词+向量、Top3/Top20、中文检索、更新和删除。精确金额、日期、统计走结构化查询。先用离线 snapshot 验证权限分区；真实 record-rule 和回读必须单独验收。不要自动接 Agent，也不要宣称企业级完成。

建议先从以下 prompt 开始：

> 先读本 README，首轮从本地样例开始，在此目录实现离线 snapshot 导入/导出与记录级持久索引；保留 company scope；关键词+向量返回可追溯来源；验证 Top3/Top20、更新/删除、重启保留和中文查询；精确条件走结构化查询；不需要连接真实 Odoo，不要接 Agent。完成后在此目录提交必要测试和实际测量记录，不改生产配置。

## 交付验收

- 每个命中都能由 `record_key` 和 `source_records` 追溯到订单、客户、明细及各自更新时间。
- 公司范围隔离：company 1 查询不得返回 company 2；离线 snapshot 结果不能冒充真实 record-rule 验证。
- 更新和删除后不保留旧记录；重启索引仍可恢复。
- 覆盖 Top3/Top20 和中文检索；延迟、token 仅在实际测量后报告。
- `queries.example.jsonl` 中的 hard negative、跨公司近似词和空结果用例都要保留。
- 大规模数据、索引、模型缓存和评估输出放在被忽略的 `local/` 下。
