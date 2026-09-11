# 桌面细节与状态透明度重构

基线：`593f6a2`，日期：2026-09-08。旧版截图及验收保留在本机忽略目录中。
用户指出业务确认卡、按钮、状态展示粗糙，要求采用成熟开源组件并显示真实 Odoo 状态。

## 实现约束

- 保留会话 → 多业务工作区 → 概览/单据/审批/核验/Trace 的桌面组织。
- 使用 [Radix Themes](https://github.com/radix-ui/themes) 的实际 React 组件和
  [Lucide](https://github.com/lucide-icons/lucide) 图标；参考
  [assistant-ui 的会话实现](https://github.com/assistant-ui/assistant-ui/tree/main/apps/docs/components/docs/assistant)
  的消息与输入区层次。模型与会话继续复用现有 Pi/native runtime。
- 业务意图卡使用 16px 圆角；业务状态、审批和单据面板采用一致的圆角、间距和字重层次。
  主操作清楚，次操作克制，图标保持同一套描边，数字采用等宽数位。
- 动效用于按钮反馈、加载、状态切换；遵从 reduced motion。不得生成假百分比或假成功反馈。
- 顶部区分本地主机、Odoo 认证读取、模型配置；真实只读探测必须返回检查时间、耗时和安全错误分类。
  `health` 仅返回缓存；`check_connection` 执行检查；查看会话不消耗模型 token。
- 业务概览区分 Agent 当前活动、最新工具回执、实际 Odoo 单据状态与独立回读。
  “本轮结束”不等于业务成功；没有观察到的事实标为未知。
- 只读验证 Odoo 对接。本轮不因 UI 验收而调用付费模型或执行 ERP 写入。

## 验收清单

| 用户可见行为 | 功能检查 | 视觉检查 |
| --- | --- | --- |
| 业务意图确认 | 取消/创建只改变本地意图；同会话多页自动切换 | 圆角、文字换行、按钮主次、长目标 |
| 成熟基础组件 | 键盘焦点、弹窗关闭、重复点击、disabled/loading | 按钮/输入框/提示/图标一致，无跳动 |
| Odoo 状态 | 未配置、未检查、连接成功、服务不可用/权限失败；缓存 health 无网络 | 状态文本、最近检查时间、耗时、重试入口 |
| 业务概览 | 当前活动与 run/tool/approval 证据一致 | 当前动作、单据状态、来源与未知提示无需进入 Trace 才能看到 |
| 审批/核验/Trace | 原作用域、审批防重放和历史证据保持；刷新不改写历史 | 卡片、表格、详情层次和非成功状态 |
| 运行中换页/检查失败 | 旧响应不覆盖新业务；检查失败不伪装成在线 | 失败恢复、长文本、窄面板清楚可用 |
| 三尺寸/动效 | 1280×800、1600×1000、1920×1080，无横向页面溢出 | 实际 Electron 桌面截图；reduced motion 与加载反馈 |
| 交付 | 回归、构建、打包、实际 EXE 启动、提交与推送 | 最终包截图对应最终源码 |

## 当前验收结果

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| Python 回归 | 129 passed，47 subtests，1 skipped | `.runtime/mvp-fix-regression-7Z2XbO/results.xml`；WSL 缺 Node 的一项已在 Windows 执行同一检查，`NATIVE_EXTENSION_OK` |
| 前端及桌面检查 | 通过 | typecheck、build、renderer-check、Electron self-check；断线不假在线、历史 Trace 不遮蔽当前运行、连接事件无重复探测 |
| 最终独立 EXE | 通过 | 仅 System32/Windows 的 PATH，无效开发 Python/host 路径覆盖；内置主机启动、原加密配置解密、真实 Odoo 认证成功 |
| 窗口和视觉 | 通过 | 最终 EXE 的 1280×800、1600×1000、1920×1080；页面/grid 均无横向溢出，宽表可独立横向滚动；实际截图已逐张检查 |
| 动效及错误 | 通过 | reduced motion 时活动卡 animation 为 none；最终 EXE 页面错误列表为空 |
| Odoo 真实连接 | 通过 | 正常应用加密配置；本机 Odoo `19.0-20260817`，JSON-2 `res.users.context_get` 认证读取成功；不代表全部模型 ACL 可用 |
| Odoo 故障恢复 | 通过 | 隔离 profile 将地址设为本机不可达端口，显示主机正常、Odoo 不可用；恢复地址后重新认证成功 |
| 当前动作 | 通过 | 原生客户读取、动态工具激活、动作预检、审批暂停可见；运行中禁止额外连接探测 |
| 独立回读 | 通过客户读取 | 两次业务回读均显示 Nimbus Bureau；未观察到订单/发票，因此对应销售核验保持未知 |
| 单据投影 | 离线回归通过 | 多订单、状态顶层字段、发票付款状态/余额/日期、出库状态；新增发票字段符合 Odoo 19 官方模型定义，尚未以本实例真实发票验收 |
| 审批拒绝 | 通过 | 4 轮、4 工具后拒绝，运行以 `approval_rejected` 终止；不重放、不写入 |
| 自然结束 | 通过本地脚本链路 | 3 轮、2 工具、7.655 秒；Pi/native 真实运行，本地脚本响应，不调用付费模型 |
| 批准后恢复 | 本轮未复测实际按钮 | automatic approval review 拒绝授权客户修改；保留离线审批回归与上一版证据，不将本轮拒绝路径算成批准路径通过 |

本轮付费模型请求 **0**，ERP 写入 **0**。原始验收 profile、回执、截图在忽略目录
`.runtime/desktop-refinement/`；上一版记录完整保留。

## 逐轮回执

下表 token 全部是本地脚本固定值，仅验证计数/聚合。每轮未缓存输入 60、缓存命中 40、
输出 20（其中推理 5），总计 120；不能据此判断真实成本或节省比例。

| 场景 | 轮次 | 工具 | 次数 | 模型耗时 | token 总计 | 结果 |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| 审批拒绝 | 1 | `get_current_time` | 1 | 2.994 s | 120 | 读取时间 |
| 审批拒绝 | 2 | `mcp_odoo_read_record` | 1 | 2.009 s | 120 | native 读取客户 |
| 审批拒绝 | 3 | `configure_odoo_tools` | 1 | 2.009 s | 120 | 激活动作工具组 |
| 审批拒绝 | 4 | `mcp_odoo_validate_write` | 1 | 2.008 s | 120 | 预检并暂停；随后拒绝、终止 |
| 纯只读 | 1 | `get_current_time` | 1 | 2.176 s | 120 | 读取时间 |
| 纯只读 | 2 | `mcp_odoo_read_record` | 1 | 2.010 s | 120 | native 读取客户 |
| 纯只读 | 3 | 无 | 0 | 2.011 s | 120 | 返回结果，自然结束 |

审批拒绝场景墙钟 81.777 秒（含人工检查等待），合计 480 个合成 token。
纯只读场景墙钟 7.655 秒，合计 360 个合成 token。
`mcp_odoo_*` 保留历史兼容名称，当前实际执行进入 native runtime，无 MCP 传输。

## 维护与验收边界

- 主题和字体在 `desktop/src/renderer/main.tsx`、`styles.css`；交互保持现有具名组件。
- UI 和 host 共用 `desktop/src/shared/protocol.ts`，未增加第二套执行框架或自动模型探测。
- `run_trace` 更新当前业务的持久化详情；独立 Odoo 回读由“读取最新状态”触发。
  本版没有新增自动回读定时器。
- “本轮结束”仅表示运行终态；销售、发票、付款、出库结果来自单据观察和独立回读。
  真实模型驱动的销售开票闭环及完整 ERP-Bench verifier 仍未在桌面版本验收。

## 最终交付

| 项目 | 值 |
| --- | --- |
| 程序 | `desktop/dist/Odoo-Workbench-0.2.0-portable.exe`，未签名 Windows 试用构建 |
| 大小 | 132896254 字节 |
| SHA-256 | `0f8aa05700961c6cc7255e5ee23fbc0bca93946a5b6068350a457bea52ae10f4` |
| 封装一致性 | ASAR 包版本 0.2.0；`index-DsrRIayV.js` 与 `index-Cnl-c3pH.css` 对应已验收源码 |
| Native 依赖 | `BUNDLED_NATIVE_IMPORT_OK`；未导入 MCP SDK/odoo_mcp；封装无 `.env`、Compiler、运行日志 |
| 实际窗口截图 | `.runtime/desktop-acceptance/v02-1280x800.png`、`v02-1600x1000.png`、`v02-1920x1080.png`、`v02-connection.png` |
| 构造单据视觉检查 | `.runtime/desktop-refinement/overview.png`、`documents.png`、`approvals.png`、`trace.png`；仅测试数据，不能作为真实业务完成证据 |
| 回退 | 原 0.1.0 EXE 与 `593f6a2` 源码基线保留 |
