# E02 耗料预检修复

结果：单轮真实模型恢复通过；新快照 E02 全流程 **9/9 通过**。采购并收货 24 件原料，完成 8 件半成品、4 件成品；BOM、来源、工作中心、库存和实际时间顺序全部通过独立回读。

修复位于 `src/erp_harness/erp/business_operations.py`：完工预检按 Odoo 19 规则计算实际领料；未 picked 的数量不算耗用，混合明细只累计已 picked 项并换算单位。保留原耗料策略；缺准备时返回可纠正错误。生产文件改动 17 行；没有修改 Laya、SOP 或审批逻辑。

证据：完整运行第 38 轮再次跳过耗料准备；预检拦截，仅发生读取 RPC，未发送完工方法。第 39 轮模型改调 `set_qty_producing`。半成品成功完工后，成品阶段主动执行该准备步骤。16 个动作均经审批和回读，无未决写入、自动重放或 selector fallback；工具 schema 前缀保持。

| 指标 | 本轮完整 E02 | 原通过版 v7 |
|---|---:|---:|
| 模型 / 工具调用 | 61 / 100 | 55 / 86 |
| 新输入 | 180,479 | 922,282 |
| 缓存输入 | 2,875,776 | 1,741,952 |
| 输出（含 reasoning） | 31,778 | 33,901 |
| reasoning | 20,288 | 24,406 |
| 总 token | 3,088,033 | 2,698,135 |
| 缓存占比 | 94.1% | 65.4% |
| 墙钟（含审批等待） | 17.58 分钟 | 18.18 分钟 |

总 token 增加 14.45%，新输入减少 80.43%。单次比较不证明因果；上一轮中断结果不用于计算节省。另一次局部模型请求为 54,314 token（新输入 260、缓存 53,888、输出 166，其中 reasoning 63），不执行模型返回的工具。完整运行 compaction 为 0，用量无缺失。

验证：137 tests + 19 subtests 通过；真实旧失败状态预检零写入；局部模型及完整业务各一次。新观察到的字段猜测错误已保留原请求与修正节点；未扩大修复范围。

运行入口：`python -m experiments.agent_regression.consumption_recovery` 准备局部分支，显式 `--paid` 发送一次；已有运行目录禁止覆盖或重跑。

证据位于 `.runtime/e02-consumption-fix-20260929/local/` 和 `.runtime/e02-consumption-live-20260929/`，包含冻结源码、完整 trace、`comparison.json`、`recovery-evidence.json` 和 `E02/after.json`。回归索引为 `experiments/agent_regression/manufacturing_consumption.json`；修改前源码和测试位于 `.runtime/e02-consumption-fix-20260929/before/`。旧失败库保留，全部实验进程已结束。
