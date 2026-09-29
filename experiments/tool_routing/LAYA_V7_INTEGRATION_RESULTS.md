# Laya v7 接入验证（2026-09-29）

业务 2/2 通过；E01 成本待审，当前保留为显式启用的候选。

链路：当前任务＋账本＋World → v7 原始投射 → GPU Laya → runtime 补齐 SOP／未决动作依赖 → 主模型使用当轮工具。审批与回读沿用原链路。

| 业务 | 结果 | 模型请求 | 工具调用 | 主模型总 token | Laya 中位耗时 |
|---|---|---:|---:|---:|---:|
| SALE | 通过 | 9 | 11 | 73,508 | 0.53 秒 |
| E01 | 通过 | 18 | 21 | 299,167 | 0.83 秒 |

SALE 两项写入、E01 四项写入均逐项审批并 verified；独立数据库检查通过。E01 每条处理4件、余6件欠单，无开票。两个业务各一次，无付费重跑；Mem0关闭，V4.1 Flash/high。

SALE 参考旧 V4 Flash：10请求、86,661 token；模型版本不同。E01 同 V4.1 历史 Laya 链路：13请求、18工具、145,139 token；本轮多106.1%，不能晋级成本验收。单次比较不能证明因果。

E01 第5轮开始多选，工具面从23增至46个；随后多次变化。新输入138,931、缓存153,472、输出6,764（其中reasoning3,720），compaction0。历史参考新输入15,570、缓存125,312。主模型多了5次请求、3次工具调用；额外工具定义和缓存变化均值得局部验证。完整目标与实时账本始终存在，不能归因为丢了任务。

原始121节点：已知98/106正确，106个已标注判断全部复现；3个未标注临界选择随分批发生变化。扩展138节点：有效138，正确115/133，正类47/60，多选5。历史池包含开发数据，不是新业务泛化证明。

GPU固定每批2个问题，保留完整上下文及原问题。长例从107秒降至3秒；全池中位0.29秒、最长3.90秒。Laya无生成输出、无API费用。SALE/E01本地分类输入分别72,789／305,194 token，独立于上述主模型用量；runtime另有3／5次只读可用性探测。

启用：`ERP_CAPABILITY_ROUTER_CONFIG` 指向本机 `.runtime/laya-v7-integration-20260929/config.json`。停用该变量即可回到原入口；如设置过旧 `ERP_OPENJEV_CONFIG`，也需停用才能回到基础入口。权重未改，原bundle保留；新bundle使用硬链接。

证据：[局部回归](../../.runtime/laya-v7-integration-20260929/validation.json)、[完整业务与用量](../../.runtime/laya-v7-integration-20260929/business-results.json)、[SALE回读](../../.runtime/openjev-live-20260928/SALE/after.json)、[E01回读](../../.runtime/openjev-live-20260928/E01/after.json)。沿用已准备的隔离目录，其frozen配置明确指向Laya。

新观察已冻结在 `tests/fixtures/capability_routing/laya_v7_live_observations.json`，待后续候选局部重放；本轮未修改预期答案。
