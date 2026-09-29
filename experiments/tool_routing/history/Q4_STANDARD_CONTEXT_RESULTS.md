# 选择器输入与提示实验 · 2026-09-28

核对出两个输入缺口：选择器没有收到宿主已有的阶段完成契约；历史失败摘要丢失了HTTP403原因。共享组装器已补齐，两项合并重放由0/4变为2/4，仍有选项顺序敏感。候选暂不晋级。

|同一组6个历史节点，各交换选项顺序|正例选中 /6|多选 /6|输出tokens|
|---|---:|---:|---:|
|v5：原提示与真实工具说明|6|2|13,968|
|v6：选项明确绑定能力组|6|2|21,728|
|v7：匹配未完成需求与候选工具|6|1|37,790|

v6没有收益。v7自由生成11/12，固定思考读分也是11/12，但输出为v5的2.7倍；没有证明是更好的生产配置。原始失败及此前v3的12/12固定思考读分均保留，后者不是自由生成成绩。

扩展8个案例/能力组合、16次：执行、账务和知识检索12/12；附件与诊断0/4，总计12/16。其中知识案例是历史真实检索探针。v8只补阶段契约和未解决历史失败原因，保持prompt、选项与业务事实不变；附件、诊断子集变为2/4，两种顺序仍不一致。未重跑其余案例，不将子集改分外推成整组通过。

从trace反推的缺口：

- **完成语义**：附件题把已验证的PDF生成误当成邮件发送，真实message_post动作仍为approved。宿主已有SMTP完成契约，原先未传给选择器；现复用completion_target_instruction，未知阶段不补猜。
- **失败依据**：mail.mail读取HTTP403被简化成失败标记，选择器看不到权限原因。现在保留有界历史错误，标为解决状态未知；已由账本覆盖的审批错误仍收起。
- **选择目标**：原标签衡量“有合法用途”，不全是“不可缺少”。附件可由基础读取替代，发送时runtime还会核对并提供PDF；mail.mail读取被拒也不等于account.move.message_post写入受阻。原标签、原分数不改，另留口径审查，不能直接按业务失败解释。
- **阶段状态**：输入有目标、账本和观察，没有独立的宿主阶段完成核验结果。复杂制造和采购题反复推断进度，是已观察到的开销来源；不能为了省token捏造完成标记。

阈值：v7的28次判定，在0.1至0.95均为正例选中12、漏选4、多选1；升至0.999后漏选变9、多选变0。v8子集在0.1至0.95也均为2/4。暂保留0.5。这是未校准的两选项相对读分，不是正确概率；两种选项顺序不是独立样本。

本轮新增44次GPU生成＋44次固定思考读分。生成输入244,918、输出135,310（reasoning135,178）；读分另输入380,184。使用Qwen3.5-4B Q4_K_M、GPU全层卸载，付费API和Odoo调用均0。56项离线检查及指标自检通过，源码/请求/标签哈希均核对，包括旧版源码归档。

这些是开发可见历史节点的局部实验，没有完成业务验收或生产worker晋级。阶段契约与错误投射已维护在共享入口；新prompt的成本回归、可选能力标签口径和选项顺序敏感仍需解决。

[输入契约](E:/GPTProject2/erp-harness-refactor/experiments/tool_routing/CONTEXT_CONTRACT.md) · [共享组装器](E:/GPTProject2/erp-harness-refactor/src/erp_harness/app/routing_context.py) · [完整对照与用量](E:/GPTProject2/erp-harness-refactor/.runtime/q4-standard-context-20260928-v8-recovery/comparison.json) · [标签口径审查](E:/GPTProject2/erp-harness-refactor/.runtime/q4-standard-context-20260928-v8-recovery/label-review.json) · [修复前完整输出](E:/GPTProject2/erp-harness-refactor/.runtime/q4-standard-context-20260928-v7-extra/probe-results.jsonl) · [修复后完整输出](E:/GPTProject2/erp-harness-refactor/.runtime/q4-standard-context-20260928-v8-recovery/probe-results.jsonl) · [此前报告](E:/GPTProject2/erp-harness-refactor/.runtime/q4-standard-context-20260928-v8-recovery/previous-report.md)
