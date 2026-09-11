# Token efficiency pair result

- 范围：同一份 8 件 Nimbus Bureau 草稿报价，完整 baseline/candidate 单对；不是统计证明。
- 配置：同 prompt、`deepseek/deepseek-v4-flash`、high、native/dynamic/controlled、同快照（`169886cd…a8eb1`）；源哈希见 [source-hashes.json](source-hashes.json)。

|版本|轮次|工具|总 token|模型耗时(s)|会话时长(s)|
|---|---:|---:|---:|---:|---:|
|baseline|14|26|160038|68.321|157.088|
|candidate|10|19|93089|45.462|181.641|

- 配对总量 24 轮/45 工具/253127 token；逐轮 fresh、cache read/write、output、reasoning、total 均在 CSV/summary，24 轮核心 usage 完整。
- candidate 比 baseline 少 41.8% 总 token、33.5% 模型耗时；模型耗时不等于整段会话时长。
- 人工审批等待为 75.3s / 124.7s，含恢复后会话时长为 157.1s / 181.6s，不能据此宣称整体提速。
- 两臂均 final STOP、verifier 7/7、ledger 1 条且 `verified`，无额外业务单据。
- 错误：baseline 的不存在 `product.template` 字段与缺 `action_id` 执行路径；candidate 的不存在 `res.partner.team_id` 字段；均未产生额外写入。
- 证据：[pair-rounds.csv](pair-rounds.csv)、[pair-summary.json](pair-summary.json)、[baseline-verifier.json](baseline-verifier.json)、[candidate-verifier.json](candidate-verifier.json)、[target_verifier.py](target_verifier.py)、[instruction.md](instruction.md)。
- 原始 receipts 保留在 `.runtime/token-pair-20260910/{baseline,candidate}/receipts/`，未复制 payload、凭据或审批 token。
