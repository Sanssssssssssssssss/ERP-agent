# Odoo 业务留档群

已在模拟企业启用官方 `base_automation`。两家公司各设销售、采购、库存、制造、财务，共 10 个私密群；当前对应岗位、跨公司经理和管理员可见。没有修改 Harness runtime。

入口：Odoo → 讨论 → 展开左侧“私聊消息”。私密群显示在这里，名称为“澄川工业 · 销售留档”等。

记录：单据、原生日志中的状态变化、执行账号、日志署名、UTC 时间、原单链接和来源消息 ID。销售订单归销售，采购订单归采购；发票和付款归财务。从启用后开始，不补写历史。

范围：汇总单据 chatter 中实际产生的日志。读取、未被 Odoo 跟踪的字段改动、删除和失败尝试不保证覆盖。桌面 HITL 批准人仅在工作台账本；共享 Odoo 账号不能区分背后的人。频道是查阅入口，原单日志与审批账本仍是依据。

实现：一个自动化规则和一个服务器动作，将六类单据的 `mail.message` 转入对应群；不复制原正文、金额或附件。按来源消息去重；忽略群消息避免递归；转发失败回滚该次转发并写 Odoo 错误日志，不中断业务。

验收：六类单据转发、账号、重复抑制、成员/跨公司/匿名权限、真实销售取消状态变化、转发故障隔离均通过。测试业务改动全部事务回滚，模型 API 调用 0。

复验：`.venv/Scripts/python.exe experiments/enterprise_validation/channel_check.py`（连接模拟 Odoo，测试结束回滚）。安装入口 `channel_setup.py` 拒绝重复安装；成员变动需在 Odoo 同步维护。

回退：设置 → 技术 → 自动化规则，停用“澄川业务日志汇总”；保留已有群消息。安装回执、回滚前数据库备份、测试结果和截图在 `.runtime/enterprise-validation-20260922/channels-20260930/`。

参考：[官方自动化规则](https://www.odoo.com/documentation/19.0/applications/studio/automated_actions.html)、[官方讨论频道](https://www.odoo.com/documentation/19.0/applications/productivity/discuss/team_communication.html)。本机使用 Community 的自动化模块，没有安装 Studio。
