# 发票演示

已在本地 Odoo 创建订单 S05029、账单 INV/2026/00006（id 53）和对应贷项 RINV/2026/00002（id 54）。客户为“苏州星港设备（发票演示）”，项目为工业设备调试服务：不含税 2,000 元、模拟税额 260 元、合计 2,260 元。

本轮是固定脚本演示，不是完整 Agent 模型验收。没有调用模型或税务接口。贷项表示账务冲减，不代表现金退款已经支付。

1. [打开 Odoo 账单](http://127.0.0.1:18079/web#id=53&model=account.move&view_type=form)，附件中查看模拟 PDF。
2. [查看对应贷项](http://127.0.0.1:18079/web#id=54&model=account.move&view_type=form)，核对原账单关联及负数展示。
3. [打开本地测试邮箱](http://127.0.0.1:18080)，搜索 `S05029`。一封邮件包含两个 PDF，没有对外投递。
4. 给 Agent 的只读演示指令：`帮我查 S05029，核对账单和贷项的金额、客户及关联关系，看看邮件有没有发送记录。先不要创建或重发。`

所有 PDF 均醒目标注“模拟、非税务发票、不可报销或抵扣”。模拟编号以 DEMO 开头。税务平台状态为 `not_connected`，本地金额和来源核对不能代替税务查验；红字展示不能代替税局红冲。

复核：`python experiments/enterprise_validation/invoice_demo.py --check`。检查原单当前状态、退款关联、文件哈希和 Mailpit 实际附件。证据及 PDF 位于 `.runtime/enterprise-validation-20260922/invoice-demo-20260930/`。已检查两页 PDF 预览，两个附件哈希与 Odoo 及本地文件一致。

不带 `--check` 仅首次准备演示；检测到既有回执或固定外部 ID 即拒绝重复创建、发送。若准备后发送中断，先检查 Odoo 邮件状态及 Mailpit，不能删除回执重跑。队列由初始化管理员投递到限定的本地 Mailpit，不扩大经理的邮件管理权限。

回退点：`before-invoice-demo-20260930`。新报表独立存在，不修改标准开票报表及原业务工具；恢复整个快照会覆盖其后的业务变化。

外部路线：[诺诺 SDK 的专用测试账号联调记录](https://github.com/jellyfrank/nuonuo-sdk/blob/main/docs/sandbox-validation.md)记录了开票、按原单查询及 PDF/OFD 下载；这是维护者的结果，我们尚未接通。需要供应商确认仅产生测试数据的账号和应用授权，不能仅凭网关域名判断是否为沙箱。官网入口：[诺诺开放平台](https://open.nuonuo.com/)。
