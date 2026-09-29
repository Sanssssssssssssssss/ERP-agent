# 中断恢复

现场：消息 7456 已创建，发票 32、PDF 589、联系人 516 对应正确；邮件通知 3 报错。当前证据不支持重发。另有两条聊天请求绑定了业务，但缺少传给 worker 的状态文件；历史进程版本未知。

修复：缺失状态传递明确报内部交接失败；宿主检测加载后源码变化。工作区提供“核对当前状态”，未决写入只读核对；通过后“继续剩余步骤”沿用原会话和账本，并传入最新回读。恢复不授予新审批。

| 验证 | 结果 / 证据 |
|---|---|
| IR01 状态传递失败 | 候选正确说明内部交接失败，不再让用户反复选业务 |
| IR02 通知异常 | 候选识别消息已创建、通知失败，指出核对入口，不自动重发 |
| API 范围 | 两节点各 A/B 一次，共 4 POST；执行工具 0；无自动重试 |
| 用量 | 新输入 1,472；缓存输入 80,640；输出 2,682（含 reasoning 1,415）；总计 84,794 |
| 离线保护 | 全量后端 1,273 通过、4 跳过、182 子测试；最终相关保护 156/156（含新增边界）。桌面 typecheck、构建、自检与浏览器交互检查通过 |
| 隔离续跑 | 原消息 7456 核对通过，同 run/session 续跑完成；新增 2 模型请求、2 次只读工具，消息数量维持 7，无新增邮件动作 |
| 续跑用量 | 新输入 40,477；缓存输入 39,296；输出 2,066（含 reasoning 1,243）；总计 81,839，无 compaction |

[冻结来源](../.runtime/interrupted-recovery-20260930/frozen.json) · [模型请求与响应](../.runtime/interrupted-recovery-20260930/model/) · [生产回读](../.runtime/interrupted-recovery-20260930/fresh-status.json) · [案例约束](../experiments/agent_regression/interrupted_recovery.json)

[隔离续跑结果](../.runtime/interrupted-recovery-20260930/live/summary.json) · [最终 Odoo 回读](../.runtime/interrupted-recovery-20260930/live/after.json) · [完整恢复 trace](../.runtime/interrupted-recovery-20260930/live/profile/data/runs/r_b3cab4bdcc694b48acdc942e25ae6832/) · [浏览器检查](../.runtime/interrupted-recovery-20260930/renderer.log)

实验边界：SMTP 队列由测试操作者在隔离库修复；Agent 只核对并收尾。复制账本及 TaskEvidence 的连接身份仅作测试夹具映射，原库与原账本未改。准备阶段一次队列调用未改变异常状态，一次 worker 启动因夹具身份未完整映射而中止（0 模型请求）；证据均保留。修正夹具后付费续跑一次，无付费重跑。原业务邮件仍需处理其真实 SMTP 配置。

回退点：`984eabb`。恢复修复在 `fix/interrupted-recovery`，本轮未发布新版安装包。

使用：关闭并重新打开更新后的工作台，选择原业务，查看状态。邮件通知故障须先核查 SMTP/队列；点击“核对当前状态”不会修复 SMTP 或重发。核对通过后继续剩余步骤。完整日志保留原 run，恢复另记事件。
