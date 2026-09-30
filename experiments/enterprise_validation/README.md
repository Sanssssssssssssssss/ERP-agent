# 标准验证企业

测试收件箱：[http://127.0.0.1:18080](http://127.0.0.1:18080)。Mailpit 持久保存邮件和附件，不向外部邮箱投递；模拟客户不需要注册邮箱。配置入口：`python experiments/enterprise_validation/manage.py mail`，可重复执行，不重发历史邮件。初始化企业时也会配置。恢复旧数据库快照后按需重新执行 `mail`。SMTP 只供 Docker 内部访问，网页只绑定本机。实现参考 [Mailpit 官方部署说明](https://mailpit.axllent.org/docs/install/docker/)。

独立 Odoo 19 Community / PostgreSQL 16，端口 `18079`，数据库 `erp_harness_enterprise_v1`。澄川工业部件有限公司与澄川机电贸易有限公司；人民币、中文科目模板。全部资料为合成数据。

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe experiments\enterprise_validation\manage.py seed --source-count 100
.\.venv\Scripts\python.exe experiments\enterprise_validation\manage.py snapshot --name baseline-100-v2
.\.venv\Scripts\python.exe experiments\enterprise_validation\manage.py status
```

小样本验收后扩量：`seed --source-count 10000`，再 `snapshot --name baseline-10000`。固定来源单为 50% 销售、30% 采购、20% 制造；自动派生库存、账务和后续 Agent 新增单据分别统计。再次执行按业务键复用，不重复新增来源单。业务基准日固定为 2026-09-21 09:00（上海）；实际审计时间由 Odoo 记录。

`restore --name baseline-10000` 验证快照校验和后，停止该独立服务，重建**该专用数据库及其附件目录**。该目录内快照之后的多余附件一并清除；遇到目录符号链接拒绝删除。仅用于复位实验。旧 demo、ERPBench 和其他卷不受影响。

凭据及结果位于 `.runtime/enterprise-validation-20260922/`：`accounts.json` 为角色登录资料；`connection.json` 使用业务经理；`fixtures.json` 提供六个尚未完成的模型目标和独立已完成样例；`seed-report.json` 提供计数。管理员仅初始化使用。凭据不可提交。

六个任务涵盖部分收发货、两级制造缺料、客户收款、供应商付款、客户退货退款及供应商退货退款。银行数据为本地合成流水；没有连接真实银行或税局。`Paid` 标签不作为银行核销证据，验证分录余额和匹配关系。

桌面岗位使用 `profiles/<role>`：制造公司为 `sales/purchase/warehouse/production/finance`，贸易公司加 `trade_` 前缀；`manager` 可跨公司。管理员不用于 Agent。已创建的设置使用系统加密，Mem0 关闭。

员工组织：执行 `python experiments/enterprise_validation/staff.py`，首次备份后安装原生 `hr`，按固定外部 ID 建立档案。重复执行复用记录，不覆盖后续人员调整。经理刷新 Odoo 后打开“员工”，选中两家公司。

| 岗位 | 工业公司员工 | 贸易公司员工 |
|---|---|---|
| 业务经理 | 陈澄 | 陈澄（跨公司任职） |
| 销售 | 林悦 | 苏禾 |
| 采购 | 陈航 | 陆远 |
| 仓储 | 周宁 | 程安 |
| 生产协调 | 许川 | 叶青 |
| 财务 | 沈清 | 顾言 |

全部为模拟人物：11 人、12 份任职档案，各自关联既有账号。经理统办业务并维护员工；其他业务权限沿用原配置。系统管理员的原生档案保留。登录名、密码不变。核验和名单位于 `.runtime/enterprise-validation-20260922/staff-20260930/`；回退快照为 `before-staff-20260930`。

新审批在工作台状态与运行事件中保存 `decision/decided_at/decided_by`，覆盖批准、拒绝、提出修改。`decided_by` 记录当时的桌面 Odoo 账号、数据库与地址，标记 `human_authenticated=false`；桌面尚无独立人员登录，不能将共享账号当成自然人认证。旧审批不补造身份，权限过滤留到后续。源码宿主重启后生效，既有打包程序需下次发布。

连续演示沿用经理账号：查询订单 → 确认 → 分批交付 → 开票 → 修改邮件草稿 → 批准发送 → 回读邮件与收款状态。按阶段继续同一业务并核对原单，不引入多员工审批流程。中国税务开票暂未连接；[百望官方沙箱](https://e.baiwang.com/)和[第三方 Odoo 对接说明](https://www.chinaodoo.com/en/documentation/zh_CN/yuanding/guides/finance/mkl_einvoice.html)可作为后续选型入口，未实测接入。

```powershell
# 仓库根目录，先确保 desktop 已构建。
$env:WORKBENCH_PYTHON = "$PWD/.venv/Scripts/python.exe"
$env:WORKBENCH_HOST_ROOT = "$PWD"
& .\desktop\node_modules\electron\dist\electron.exe .\desktop --user-data-dir="$PWD/.runtime/enterprise-validation-20260922/profiles/manager"
.\.venv\Scripts\python.exe -X utf8 experiments/enterprise_validation/evaluate.py data
```

验收入口：`retrieval_check.py prepare/run/concurrency/lifecycle --output <本机.runtime目录>`；顺序执行。`live.py freeze` 固定六条业务输入、两题回归配置和源码/安装包哈希；配置已授权的 `LLM_BASE_URL/LLM_API_KEY` 后执行 `live.py E01` 至 `E06`，每题只允许一次。ERPBench 使用 `bench_launch.sh 2003/2156`，WSL PATH 需包含现有 Harbor 环境。原始日志、请求、用量、审批及前后快照均在本机实验目录；总状态只看 `docs/enterprise-validation.md`。

六题各自从 `baseline-10000` 恢复后开始，结束保存 `after-e01` 等快照。查看旧题的当前业务单据前先恢复其对应快照；原始 trace 可直接查看，不依赖当前数据库状态。

八题结束后运行 `python experiments/enterprise_validation/report.py`，离线重算用量和成本门槛，生成本机 `acceptance-results.json`。Harbor 的嵌套评分兼容问题使用现有 reward adapter 处理，原始评分与异常文件保留；该命令不调用模型。

`audit_payments.py <live/E03绝对路径> --fixtures <fixtures.json绝对路径>` 补查逐笔付款与银行匹配；具体参数以 `--help` 为准。原请求在 `live/E01/profile/data/runs/*/requests/`；模型会话、审批、Odoo调用与SQLite动作账本在相邻目录。两道旧题位于 `bench/jobs/`，兼容评分位于 `bench/normalized/`。完整日志、凭据和快照均不上 Git。

`audit_business.py --restore-and-collect` 会串行恢复基线及五个结束快照，按只读事务采集原单、贷项、分录和工位证据，最后恢复 `baseline-10000`。该模式持有与真实业务相同的排他锁；不带参数仅比较已经采集的JSON。补查结果位于 `business-supplement/audit.json`。
