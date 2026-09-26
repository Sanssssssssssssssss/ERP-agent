2026-09-26：Laya 接入实验三条业务均通过。主模型为 `deepseek/deepseek-v4.1-flash` / high，Mem0 关闭；源码冻结在 `1860b55`，运行前后哈希一致。

| 业务 | 最终 Odoo 检查 | 模型请求 | 模型工具调用 | 总 token |
|---|---|---:|---:|---:|
| SALE | 指定销售单确认；金额、客户、明细不变，无开票或交付 | 9 | 11 | 78,498 |
| E01 | 销售交付、采购收货各完成 4 件；各保留 6 件欠单，无开票 | 17 | 22 | 223,758 |
| E06 | 指定产品退货 2 件；58.76 元贷项、退款与指定银行流水核销 | 49 | 83 | 1,809,739 |

合计 75 次模型请求、116 次模型工具调用、2,111,995 token：新输入 185,849，缓存输入 1,891,712，输出 34,434（含 reasoning 21,254）。完整三条无缺失用量、无 compaction。16 项写入均有宿主审批和 verified 账本回执；使用三份独立数据库副本，无对外邮件。

Laya 每条只参与前三轮：共 9 次决策及发布；之后三条均由主模型 configure 接管。E01 第三轮预发布 actions，其余八次保留基础工具。每次决策含两次本地标签一致性推理，无付费裁判。此次验证证明这三个业务及兜底可运行，不能证明全面替换编排或减少调用。

仍有两处观察：E01 在已发布 actions 后重复 configure；E06 明细删除参数一次不合法，校验拒绝后自行修正。已冻结真实截面 [R02](../../.runtime/laya-integrated-20260926/R02/manifest.json)、[R03](../../.runtime/laya-integrated-20260926/R03/manifest.json)，未追加 API 对照。E06 的 83 次工具调用中，42 次读记录、8 次查字段；本轮未调整业务执行策略。

SALE 首次启动有 4 次 ConnectError，无有效模型回复、无业务工具执行。保留未知用量；检查数据库未变后，在新 profile 手动续跑并通过。网络故障没有复现出确定原因，[原证据](../../.runtime/laya-integrated-20260926/SALE/)与[恢复记录](../../.runtime/laya-integrated-20260926/network-recovery/infrastructure-recovery.json)完整保留。未自动重复失败业务。

相关离线检查 146 passed、35 subtests passed；加强后的真实会话发布测试 3 passed。覆盖同轮工具可见与可执行一致、审批重启后主模型接管、未决写入保护及路由失败兜底。两项变更同时发生（V4.1 与 Laya），历史 token 差异不作因果结论；未将含人工审批等待的墙钟时间当模型速度。

启用与回退见 [README](README.md)：接入默认关闭，清除 worker 的 `ERP_LAYA_MODEL`、`ERP_LAYA_PYTHON` 即恢复原编排。代码在 `1860b55`，依赖、权重和完整日志留在本机忽略目录。

[汇总与证据哈希](../../.runtime/laya-integrated-20260926/results.json) · [SALE 回读](../../.runtime/laya-integrated-20260926/network-recovery/SALE/after.json) · [E01 回读](../../.runtime/laya-integrated-20260926/E01/after.json) · [E06 回读](../../.runtime/laya-integrated-20260926/E06/after.json)
