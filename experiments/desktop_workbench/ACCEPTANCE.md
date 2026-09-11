# Odoo Desktop Workbench V1 验收记录

日期：2026-09-08。基线 `b4e6461`，回退标签 `desktop-workbench-baseline-b4e6461`。

**桌面实现、离线链路及回归通过；真实模型驱动的销售开票闭环尚未验收。**
本轮付费模型调用为 **0**，隔离 Odoo 仅执行读取和动作预检，未执行 ERP 写入。
automatic approval review 拒绝启动真实模型，要求用户明确同意向已配置的
`https://api.commandcode.ai/provider/v1` 发送测试指令、虚构业务资料和工具结果；该授权仍待答复。

## 验收表

| 范围 | 结果 | 证据与边界 |
| --- | --- | --- |
| Windows 桌面程序 | 通过 | 实际启动 portable EXE，使用仅含 System32/Windows 的 PATH，并设置无效的开发 Python/host 覆盖；内置 Python 正常启动本地主机 |
| 会话与多业务页 | 通过 | 实际 UI 新建、改名、搜索、归档、业务创建确认、同会话两个业务页；重启后恢复；切换和重复点击不改变执行目标 |
| 审批与续跑 | 通过离线链路 | 实际 UI 批准、拒绝；暂停后没有多余模型请求；批准通知进入同一 Pi 会话，再自然结束；脚本模型不执行写入 |
| 停止与恢复 | 通过 | 实际 UI 停止、关闭重开；保留取消/中断终态，不自动重放；账本不确定写入进入待核对状态 |
| 审批边界 | 回归通过 | 错误 session/business/run/action、重复审批、过期、前态变更及多项审批；全部批准前不恢复运行 |
| Trace 与用量 | 通过离线核对 | 运行→轮次→工具→结构化回执；缺失用量为 null；统计按当前运行聚合；本轮模型用量是合成测试值 |
| 独立 Odoo 回读 | 通过读取和回归 | 真实 Odoo 19 字段核对；客户、订单、发票、行项目和关联出库；失败标记 stale/unknown，恢复后更新当前事实，不修改历史运行证据 |
| 视觉与键盘 | 通过 | 实际 EXE 检查 1280×800、1600×1000、1920×1080；长目标独立滚动、无页面横向溢出；设置弹窗焦点约束与返回；空、失败、待审批、完成及 Trace 状态 |
| 桌面权限与密钥 | 检查通过 | renderer 隔离、IPC 方法及来源限制、禁止外部导航/新窗口；DPAPI 保存密钥，无明文回退；执行或等待审批时禁止替换连接设置 |
| Native 边界与打包 | 通过 | 打包导入检查 `BUNDLED_NATIVE_IMPORT_OK`；无 MCP SDK/odoo_mcp 导入；未打包 Compiler、旧仓库环境、密钥或运行日志 |
| Python 回归 | 123 passed、1 skipped、47 subtests | `.runtime/mvp-fix-regression-R9pld4/results.xml`；WSL 缺 Node 导致的单个跳过已在 Windows 运行相同 native extension 检查，输出 `NATIVE_EXTENSION_OK` |
| 前端与桌面检查 | 通过 | typecheck、build、renderer-check、self-check；实际桌面页面错误列表为空 |
| 真实模型与业务结果 | **未验收** | 冻结 ERP-Bench 2262 小场景已准备；待明确授权后运行至自然结束，再执行独立 verifier；不可将离线结果当成该项通过 |

## 最后一轮离线结果

完整摘要见 [OFFLINE_RESULTS.json](OFFLINE_RESULTS.json)。运行 `r_1ec6f71e3d9948e685ca27d87988fcbd`
共 **5 轮、4 次工具调用**，自然结束，总墙钟 **29.892 秒**（含审批等待和进程开销）。
本地脚本产生固定用量，仅检查记录和聚合，不证明真实成本或 token 节省。

| 轮次 | 工具 | 次数 | 模型耗时 | 未缓存输入 | 缓存命中 | 输出（含推理） | 推理子集 | 总计 | 结果 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `get_current_time` | 1 | 0.330 s | 60 | 40 | 20 | 5 | 120 | 读取当前时间 |
| 2 | `mcp_odoo_read_record` | 1 | 0.006 s | 60 | 40 | 20 | 5 | 120 | native 读取虚构客户 |
| 3 | `configure_odoo_tools` | 1 | 0.006 s | 60 | 40 | 20 | 5 | 120 | 激活动作工具组 |
| 4 | `mcp_odoo_validate_write` | 1 | 0.006 s | 60 | 40 | 20 | 5 | 120 | native 预检并暂停等待审批 |
| 5 | 无 | 0 | 0.196 s | 60 | 40 | 20 | 5 | 120 | 验证已收到审批通知，自然结束 |
| 合计 | | **4** | **0.544 s** | **300** | **200** | **100** | **25** | **600** | 未执行 ERP 写入 |

`mcp_odoo_*` 为历史兼容工具名称，当前调用直接进入 `odoo_runtime`，没有 MCP 传输。
总计为未缓存输入 + 缓存命中 + 输出；推理已包含在输出中，不重复加总。

其他记录保留原始结论：

| 运行 ID 前缀 | 状态 | 轮次 / 工具 | 说明 |
| --- | --- | --- | --- |
| `r_ef5840` | failed | 0 / 0 | 早期 worker HOME 初始化失败；已修复并增加可重试回归 |
| `r_bf9bce` | completed | 5 / 4 | 首次离线审批续跑 |
| `r_a32b92` | interrupted | 4 / 4 | `host_restarted`，不改写为成功 |
| `r_7180f9` | failed | 4 / 4 | `approval_rejected`，拒绝终结账本 |
| `r_142155` | completed | 3 / 2 | 带延迟的读取，用于运行中切换业务验收 |
| `r_428a14` | cancelled | 0 / 0 | `execution_stopped`，用量未知 |

## 交付物与复核

程序：`desktop/dist/Odoo-Workbench-0.1.0-portable.exe`。未签名，内含 Python，
无需全局 Node/Python/WSL；Odoo 与模型服务仍需在连接设置中配置。
构建输出、验收 profile、原始回执和截图均被 Git 忽略。构建步骤见 [desktop/README.md](../../desktop/README.md)。

最终 EXE 的哈希与尺寸已在实际启动后核对。

| 项目 | 值 |
| --- | --- |
| 文件大小（字节） | 126995648 |
| SHA-256 | `2e358b84c629e6f2b1368a0a05539786d3f1e9fb6352986578245393450cc037` |
| 视觉证据 | `.runtime/desktop-acceptance/final-1280x800.png`、`final-1600x1000.png`、`final-1920x1080.png`、`final-trace-1600x1000.png` |

## 修正与维护边界

- worker 固定 native/dynamic/record/host approval；按 business 隔离 Pi 会话，按 run 隔离动作账本和回执。
- 审批恢复发送明确通知，并恢复动态工具组与回执序号；已批准不等同于已执行。
- 统一处理初始化失败、取消、EOF、超时、拒绝和过期；未知写入保持待核对，禁止自动重发。
- 异步响应绑定当前会话/业务/运行；切换清除旧页内容；重复选中同一页保持 Trace 与业务详情。
- 当前 Odoo 回读独立存储；历史运行、用量和原始工具观察不因刷新而改写。
- V1 为单 Windows 用户、单 Odoo 账号 ACL、一个 host 同时一项运行，支持销售开票一种业务类型的多个实例。
- 当前核验是明确的 Odoo 事实检查，不是通用目标评分或完整 ERP-Bench 评分；真实业务签字必须有真实运行和独立 verifier 证据。

后续修改从页面、桌面壳、host、业务投影各自入口调整，复用 Pi/native runtime；
不另建模型循环或提前引入插件框架。严格的真实业务验收项保持未签署。
