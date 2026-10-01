# 模拟企业权限与开票接入

2026-09-30。经理统办两家公司业务；员工按岗位操作。两家公司沿用原一万单据计划，用于跨公司隔离，不要求日常演示跨公司流转。

| 岗位 | 允许直接写入的核心单据 |
|---|---|
| 销售 | 销售订单及明细 |
| 采购 | 采购订单及明细、原生采购收货链路 |
| 仓储 | 库存收发货及明细 |
| 生产 | 制造、工单、BOM、生产所需库存操作 |
| 财务 | 会计凭证、发票及付款 |
| 经理 | 保留两家公司原有全部业务权限 |

沿用 Odoo 原生读取权限、公司规则及人事隐私字段控制；跨模块必要只读查询保留。14 条全局记录规则限制明确加入“澄川模拟员工业务边界”组的十个账号，不能被另一条组规则放宽。经理未加入限制组。后台自动关联单据仍可能使用 Odoo 原生 sudo，本配置不承诺所有模块、所有方法均无权限提升路径。

采购确认以采购员身份生成库存移动；必须保留该依赖。过滤由 Odoo 服务端执行，直接 RPC 也受约束。Harness 应使用对应岗位 profile 的凭据，经理凭据不会因为提到某员工姓名而自动降权。桌面审批的人员认证与授权过滤尚未实现，当前保留账号留档。

安装：`python experiments/enterprise_validation/access.py`

验证：`python experiments/enterprise_validation/access_check.py`。411 项本地 Odoo 检查、26 项在线 RPC 检查通过。两家公司分别验证销售确认、采购确认、收货、交付、发票过账和制造确认；事务回滚。覆盖拒绝跨公司/伪造公司上下文、越岗写入、人事私密读取及自授管理员权限；不是 411 个完整模型任务。

证据：`.runtime/enterprise-validation-20260922/access-20260930/{before,setup,checks,http-checks}.json`。未调用付费模型。业务数量仍为 5000 销售、3000 采购、2000 制造、32 会计凭证。

回退：停用名称以“澄川岗位写入 · ”开头的 14 条规则；完整备份为 `before-access-20260930`。回退规则即可解除新增限制，无需覆盖随后产生的业务数据。

## ChinaOdoo 接入：缺依赖，尚未安装

[供应商说明](https://www.chinaodoo.com/en/documentation/zh_CN/yuanding/guides/finance/mkl_einvoice.html)列出 `mkl_einvoice`、`mkl_sale_invoice_number`、`mkl_invoice_print`、`mkl_tax_bureau_integration`。本地均未安装；公开下载目录及 GitHub 搜索未取得可安装包，API 地址与密钥需供应商提供。

[合规手册](https://www.chinaodoo.com/en/documentation/zh_CN/yuanding/user_book/china-l10n.html)面向 Odoo 19 Enterprise；我们的 Community 兼容性需核验实际模块 manifest，不能仅凭页面断言支持或不支持。[演示入口](https://erp2.chinaodoo.com/web/demo_login)需要登录，未验证开票沙箱。

继续接入需要：四个模块及依赖包/安装授权、Odoo 19 Community 支持说明、测试平台地址和凭据、供应商指定的测试企业及票种。拿到后在独立数据库安装，验证申请去重、状态回查、正式文件下载、失败与红冲；通过后再并入当前企业。未向真实税局发送任何请求。
