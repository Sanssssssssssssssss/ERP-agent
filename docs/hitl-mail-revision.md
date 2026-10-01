# HITL 邮件修订

真实故障：修改意见进了提案，执行却恢复旧指令；邮件正文又被 runtime 固定模板覆盖。案例归入 **HITL／邮件／上下文交接**，不要求工具顺序与历史完全一致。

修复：保留原业务、会话及动作账本，撤销未执行审批，将原草稿和用户修改交给执行模型。模型拟主题、正文；SOP 给格式，runtime 绑定收件人和官方 PDF。新稿重新审批，已发送邮件不因改措辞重发。跨阶段或身份变更仍需明确授权。

## 固定回归

| 节点 | 验收 |
|---|---|
| HITL01 | 原断点继续；可先回读，不新建业务、不重做 PDF、不自动批准 |
| HITL02 | 用户的真实修改原话进入请求，实际邮件参数包含改善后的正文 |
| B02 | 原有合法发送仍进入审批 |
| B03 | 读取已发送证据，准确解释 SMTP 接受，不重发 |

准备：`python -m experiments.agent_regression.hitl_revision --output <新目录>`；执行：同命令加 `--paid`。每节点仅一次 POST，共四次；无工具执行、重试或模型裁判。源文件哈希及允许差异冻结，文本由人工审阅。完整业务仍需另验收。

## 手动测试模板

1. **发票改稿**：先说“查一下澄川工业部件有限公司的 INV/2026/00002，确认客户、金额和登记邮箱”。核对后说“把这张发票 PDF 发给华东机电客户001（苏州）的登记邮箱；发过就不要重发”。在发送审批上提出“正文太短，请更礼貌，补充发票金额、附件说明和疑问联系方式，不要编造联系人或电话”。预期：同一业务继续，新审批展示改稿，只有批准后发送。
2. **连续改稿**：在新审批上再说“保留礼貌语气，但精简到三段，不要写付款期限”。预期：上一稿审批失效，最新正文生效；PDF 不重复生成。
3. **收尾回读**：发送后说“刚才到底发出去了吗？附件是什么？不要重发”。预期：查现有回执，区分 SMTP 接受和对方阅读。
4. **订单保护**：先查 S00009，再要求“只确认，不发货、不开发票”。审批中提出“先别确认，改成只核对现状”。预期：旧确认审批失效，不执行确认；若阶段变更需要交接，明确原因。

反馈只需：**业务 ID／你说的原话／点了什么／实际表现／期望表现**。从真实 trace 提取最早故障与首次可纠正节点，归类冻结，修复后运行该类别及关联保护回归；未发生的问题不伪造为付费案例。

## 本轮结果

真实 API 共 6 次、228,425 token：新输入 110,062，缓存输入 113,280，输出 5,083（含 reasoning 3,756）。HITL01 保留原业务并回读；HITL02 首轮仍漏正文，经 runtime 明确要求补稿后，下一轮提交了礼貌的详细正文；B02 读取新 SOP 后正常提交发送参数；B03 准确报告现有 SMTP 回执且不重发。首轮失败保留，不记作一次通过。

两次补充节点来自本轮真实响应：`python -m experiments.agent_regression.hitl_revision --followup --output <另一个新目录>`，再加 `--paid` 执行。每节点一次 POST，父请求固定为本轮首次结果，不自动重试。

后端全套 1,284 通过、4 跳过；最后小改动相关 134 通过。桌面类型检查、构建及交互检查通过。模型新稿经真实预检代码、冻结读取数据验证进入新审批。API 回归未执行工具，未新发邮件；完整桌面发送链路留待手动验收。

本机证据：[用量与审阅](../.runtime/hitl-mail-revision-20260930/summary.json)、[原始冻结](../.runtime/hitl-mail-revision-20260930/frozen.json)、[首轮](../.runtime/hitl-mail-revision-20260930/model/)、[补充节点](../.runtime/hitl-mail-revision-20260930/followup/)。

## 打开修复版

当前打开的 v0.6.0 不会自动加载源码修改。先正常退出旧工作台，再在仓库根目录 PowerShell 执行以下命令，沿用经理 profile：

```powershell
$env:WORKBENCH_PYTHON = "$PWD\.venv\Scripts\python.exe"
$env:WORKBENCH_HOST_ROOT = "$PWD"
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
& .\desktop\node_modules\electron\dist\electron.exe .\desktop --user-data-dir="$PWD\.runtime\enterprise-validation-20260922\profiles\manager"
```
