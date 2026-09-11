# 桌面有界压测与真实业务验收

日期：2026-09-08。起始提交：`95a3b0e5b7201276c43c97884bf349ce210e31ba`。

## 验收范围

本轮用户要求补充细节与压力回归，并在真实小案例运行过程中检查页面。
沿用现有测试、Pi 模型循环、native Odoo 工具和独立 MVP verifier。
压力测试使用离线 bridge / worker；真实业务只运行一个隔离场景。

| 检查面 | 操作与判定 |
| --- | --- |
| 重复操作 | 连续点击发送、创建业务、启动、审批，每项逻辑提交只能接受一次；相反决定不能并发提交 |
| 高频切换 | 至少 50 次会话与 50 次业务切换，包含 A→B→A；迟到响应不能覆盖当前页面 |
| 高频 Trace | 300 个事件突发，保留最终状态，并合并刷新请求；历史轮次不遮蔽当前运行 |
| 主机并发 | 100 次启动、100 次审批、160 次事件写入、100 次读取、100 次 BUSY 检查；单活动运行、正确作用域、可取消 |
| 恢复与异常 | 终态持久化、重开不自动重放；错误、拒绝、未知写入不展示业务成功 |
| 视觉与输入 | 1280×800 和 1600×1000，长文本与 Trace 无页面横向溢出；设置/归档键盘可操作 |
| 真实过程 | 检查启动、工具执行、审批、单据状态、最终 Trace；页面回读与 Odoo 最终状态一致 |

## 唯一真实场景

专用容器 `pi-odoo-desktop-acceptance-20260908`，Odoo `127.0.0.1:18069`，数据库 `bench`。
复用冻结 ERP-Bench 2262 的虚构 Nimbus Bureau / Open-Plan Noise Barrier Wall 数据。
不触及持久 Odoo `:8069`、MCP `:18000` 或其他实验容器。

操作目标：读取客户和产品说明；按当前标价创建一张、数量为 1 的销售订单，
客户参考 `PI-DYNAMIC-MVP-20260908`，30 Days 付款条件和明确承诺日期；确认订单，
创建并过账唯一关联普通客户发票。只允许 Odoo 自动产生该订单的正常待处理出库，
不完成出库，不创建或修改采购、制造、付款或既有库存记录。

使用桌面已配置 `https://api.commandcode.ai/provider/v1` 的
`deepseek/deepseek-v4-flash`。只发送该虚构场景指令和相关工具结果；密钥通过正常
加密设置和主机环境传递，不出现在报告。保留自然结束及约一小时异常超时。

验收前通过容器标准 Odoo 认证方式运行已有只读 verifier：销售、发票、采购、制造、
付款、出库均为 0，既有 stock.move 为 1。基线在忽略目录
`.runtime/desktop-acceptance/fixture/desktop-live-baseline.json`。

本案例使用 `experiments/dynamic_business_mvp/verifier.py` 的 10 条独立业务规则。
这是冻结 ERP-Bench 数据上的 MVP 场景评分，不冒充完整 ERP-Bench 全集成绩。

## 结果

**有界压力回归通过；真实模型驱动的业务案例仍未启动，不能签署该项验收。**

| 检查 | 实测 | 结论 |
| --- | --- | --- |
| 主机并发 | 16 线程，560 次操作；单项压测 0.788 秒 | 仅一个 active run；审批正确作用域且仅接受一次；160 条事件完整；终态重开不重放 |
| 会话突发切换 | 50 次实际 click dispatch，另含 A→B→A；471 ms | 最终会话正确，迟到响应不覆盖 |
| 业务突发切换 | 50 次实际 click dispatch；7 ms | 最终业务正确；此值是突发派发检查，不是 50 次完整加载耗时 |
| 业务逐次切换 | 20 次逐一点击并等待对应标题渲染；698 ms | 正确切换；不将其解释为真实 Odoo 请求吞吐 |
| Trace 突发 | 300 个事件；318 ms | `get_trace=1`、静默 `get_business=1`，最终 trace version 加 1 |
| 重复与相反点击 | 创建、发送、启动、审批、取消各连续点击 5 次 | 各恰好一次 RPC；创建/审批包含相反决定交替点击 |
| 后端完整回归 | 130 passed、1 skipped、47 subtests；20.43 秒 | 跳过的 Node native extension 检查在 Windows 补跑 `NATIVE_EXTENSION_OK` |
| 前端检查 | typecheck、build、renderer-check、Electron self-check | 通过；静态独立复核已关闭发现的问题 |
| 实际桌面 | 创建、改名、业务确认、长目标、单据/Trace 空态、连接弹层；1280×800、1600×1000 | 无页面错误或页面横向溢出；长目标内部滚动；空态不展示虚假完成 |
| 真实 Odoo 连接 | 实际桌面连续 5 次只读认证检查 | 全部已连接；显示 `127.0.0.1:18069 · bench`、账号和检查时间 |
| 真实模型案例 | 0 轮、0 工具、0 模型请求、0 ERP 写入 | 自动审批拒绝“开始执行”；等待用户明确授权目的地、发送内容及测试写入 |

耗时来自一次本机有界检查，不是并发生产容量或性能 SLA。主机用 10 秒异常上限避免
虚拟化/磁盘抖动，取消单独检查在 1 秒内返回。Electron self-check 的无窗口进程在
受限环境出现 GPU 子进程退出提示；实际可见打包程序已启动并完成上述界面检查。

修复来自压力测试与独立复核：审批重复请求；取消重复请求产生停止状态错误；
相反审批/创建决定并发提交；发送失败的迟到回调恢复到其他会话草稿。
去重直接放在现有动作入口，以会话、业务、运行、动作标识约束，`finally` 释放。
未新增框架、运行队列、模型循环或依赖。

### 真实运行未启动的原因

自动审批拒绝了实际桌面的“开始执行”：要求明确同意将案例指令及后续 Odoo 工具结果
发送到 CommandCode/DeepSeek，以及允许测试 ERP 写入。本轮“真实跑一个案例”的请求
未被审批器认定为该目的地和载荷的具体授权。已向用户提出这一项准确授权问题，
没有改用其他调用路径。独立验收 profile 已保存 1 个会话、1 个待执行业务、0 个运行。
当前没有可报告的真实模型每轮用量、自然结束或最终业务评分。

### 可复核依据

- 回归：`.runtime/mvp-fix-regression-ZDVcMy/results.xml`。
- 实际窗口截图：`.runtime/desktop-acceptance/v021-live-prepared.png`、
  `v021-live-1280-connection.png`。
- 独立 profile：`.runtime/desktop-live-acceptance/profile`；配置为正常加密设置副本。
- 最终包重启后恢复同一待执行业务，继续实际操作 20 次子页切换和设置弹窗键盘焦点；
  页面错误为 0，1280×800 的 document scrollWidth 为 1280。
- 最终截图：`.runtime/desktop-acceptance/v021-final-1280.png`；UI 收据：
  `.runtime/desktop-live-acceptance/ui-receipt.json`。
- 程序：`desktop/dist/Odoo-Workbench-0.2.1-portable.exe`，132895027 字节，
  SHA-256 `9412e30743a9008e82e9ef88cfefbad14c6ac6b9efbdbfc5fa2b774f3a58c04c`。
  实际加载 renderer `index-DNlJe2jJ.js`，与最终源代码构建一致。
- UI 运行、账户配置和原始业务基线均留在 Git 忽略目录；不提交密钥或日志。
