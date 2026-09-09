# TOPOLOGY-050 验收

范围：ERP-Bench 2262 的 Nimbus 材料与补货衍生场景；不是全 Bench 评分，输入仅 UTF-8 CSV/TXT。

|批次|结果|模型轮次 / 工具|耗时|
|---|---|---:|---:|
|首次业务执行|failed|29 / 54|640.89s|
|销售采购开票续跑|completed|14 / 16|325.10s|
|窗口中断（验收者误关闭）|interrupted|2 / 5|194.39s|
|PDF 空字段修复前（核对后）|interrupted|11 / 10|354.38s|
|正式 PDF 续跑|completed|4 / 4|150.40s|
|中文会话（4 runs）|completed|6 / 2|131.04s|

- 全部 5 个业务 run：60 轮、89 工具；4 个 conversation run：6 轮、2 工具，逐轮数据见 `topology-050-all-rounds.csv`。
- 首次 usage：input 33,116、cache 890,496、output 24,045、total 947,657；第 29 轮 provider 未返回 usage，reasoning 为 null。
- 续跑 usage：input 4,198、cache 731,136、output 4,923、reasoning 2,091、total 740,257。
- 窗口中断来自验收者误关闭；PDF 空字段修复前已核对 Odoo 返回的 `false`，向导未重复创建，后续方法调用被守卫拦截，修复后核对再继续。
- 正式 PDF 续跑前有技术指引，不能视为零干预首轮成功；此前 guard 语义修复也单列保留。
- 最终官方 PDF：`downloads/Nimbus-invoice-INV202600001-official.pdf`，31,922 bytes，pypdf 核验为 Invoice `INV/2026/00001`，不是 Proforma。
- 最终数据库核验：SO 1 张 sale、PO 1 张 purchase、invoice 1 张 posted，金额分别 5,561.76 / 4,643.52 / 5,561.76 USD；attachment 525 为 PDF，mail 0、payment 0，picking 2 张 assigned，未核验付款和实际收发货完成。
- 当前 sale_invoice 收窄目标的 7 项基础 checks 全 passed；此前链式独立回读的 12 项 checks 全 passed，二者保留各自范围。
- 共保存 7 个下载记录（历史 Proforma 与正式 PDF 均保留）；当前可交付为正确的 3 PDF 与 3 CSV。
- 并发读取 50 次、5 并发全部成功，p95 26.28ms；完整逐轮和脱敏汇总见 `topology-050-all-rounds.csv`、`topology-050-all-runs-summary.json`。
- 自动化验收合计 128 tests passed；打包 QA 恢复 5 runs/7 files/1 material 成功；sidecar 1804 hash 与源码一致，未引入 MCP。
- portable `Odoo-Workbench-0.5.0-portable.exe` build exit 0，SHA256 前缀 `B681C566`；原 production profile 已恢复 3 会话，Odoo 18069 connected、无 active run，旧空会话入口恢复，portable/profile 验收通过。
