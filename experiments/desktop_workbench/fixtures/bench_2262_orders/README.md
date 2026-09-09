# 2262 客户订单材料（派生 subset）

这组材料从本仓库的 ERP-Bench 任务 `2262_easy_26_buy_only_net_30_no_adjacent_data` 提取，来源是：

- `bench/tasks/2262_easy_26_buy_only_net_30_no_adjacent_data/instruction.md`：四个客户的原始需求、相对交期、税前预算上限和 30 Days 付款条件。
- `bench/tasks/2262_easy_26_buy_only_net_30_no_adjacent_data/environment/scenario_data.json`：种子主数据中的商品名称和商品代码。

`orders.csv` 是为桌面阅读和粘贴准备的转换材料，不是 ERP-Bench 原始附件。它不是从 PDF、Excel 或用户上传文件复制出来的；本任务目录没有这类原生附件。CSV 使用 UTF-8 BOM，便于 Windows Excel 打开；表头采用中文，列依次为客户、商品名称、商品编码、数量、交期天数、税前预算上限 USD、付款条件。

该种子环境的既有销售单、采购单和制造单列表为空，所以这四条是提示中的待创建客户需求，不是从既有订单记录导出的历史单据。

本材料不包含 `solution/optimal_plan.json`，也不提供售出价格、供应商分配、推荐采购量或其他应由 Agent 查询和决策的答案。商品代码来自种子公开主数据；订单数量、交期和预算沿用 `instruction.md` 题面整数。种子里的金额可能带小数，不能把转换表的题面整数当成最终 Odoo 金额；Agent 执行前仍须读取 Odoo 核对。

这四行只是 2262 的销售订单输入 subset：共 4 个客户、36 件商品。完整 benchmark 还要求读取内部备注，利用现货、采购和供应商约束，创建并确认销售单和采购单，维护 SO→PO 追溯，满足新支出毛利约束，并创建和过账关联发票，最后由 benchmark verifier 检查终态。因此单客或单笔桌面运行不能宣称通过完整 2262。

当前桌面尚未实现拖入该文件的导入入口。可以把 CSV 内容作为对话材料粘贴给 Agent，但 Agent 仍需在 Odoo 中独立读取和核对客户、商品、库存、供应商和付款条件，再按审批流程执行。

例如可以先粘贴：`请先整理 Nimbus Bureau 的 8 件 Open-Plan Noise Barrier Wall，6 天内交付，税前预算上限 5927 美元，付款条件 30 Days；先列出待创建销售单和需要补问的信息，不要自动执行。` 这只是单客 subset，不能替代完整 2262 任务的采购、追溯和终态核验。
