# Q4 错选复查 · 2026-09-28

上一轮规则未稳定泛化。另取4个真实历史失败节点，各交换选项顺序；Qwen3.5-4B Q4_K_M、GPU、thinking开启，结果1/8。相同节点旧版直接读分为4/8；提示与解码均不同，不能归因于thinking单一因素。标签、原始事实和历史成绩未改。

|节点|应该选择|本次结果|从输入与模型输出定位的问题|
|---|---|---|---|
|E06 供应商退货退款，0033|actions：58.76元贷项仍是草稿，需要推进过账|漏选2/2|固定的`not_granted_by_disclosure`被当成权限拒绝；另一顺序的理由支持展示，最终字母却表示隐藏|
|E02 制造补料，0015|actions：采购创建已approved，尚未执行|漏选2/2|把非授权声明当拒绝；混淆账本覆盖完整、目标done与实际完成|
|S01499 确认后，0006|不再增加actions；当前不含发货、开票|误选2/2|第4条消息读到draft，第15条已验证sale；回放未清楚表达时序，模型把旧读数当当前冲突，主张重新确认|
|E03 收款核销后，0032|无需新增actions，必要时基础工具回读|误选1/2|两笔核销均已有证据；一种顺序理由认为无需动作，最终字母却选择展示|

## 局部对照

|只改变的因素|同一子集修改前→后|边界|
|---|---|---|
|删除顶层固定权限提示，保留真实审批账本|1/6→4/6|两个答对结果的理由仍否定展示；不能视为修复验收通过|
|补回原请求的证据时序，保留原始事实|0/2→1/2|剩余错选仍有理由与字母不一致|
|固定8段已生成思考，重算末位分数并取最大值|1/8→3/8|重算与原增量解码有数值差异；仅诊断最终采样，不是完整重新运行|

另一个明确误读：`fact_budget`实际指投射材料大小限制，被理解成“业务预算未获批准”。对应源码：`src/erp_harness/app/routing_state.py:208`。顶层权限提示由同文件105行固定生成；它不是实时ACL拒绝。历史回放的缺陷不能直接代表所有在线World投射。

后续应统一清理输入契约：将静态安全声明放在system，将覆盖率、观察时序、真实审批分别表达；最终决策采用稳定的结构化输出。不能通过删除真实审批事实修正分数。本轮只改实验，未接入生产。

16次生成＋8次末位评分，全部本地GPU。生成输入69,512、输出24,407（其中reasoning 24,359），生成推理累计525秒；末位复查另处理50,219输入tokens。付费API和Odoo调用均为0。一次评分启动因日志路径不匹配在推理前失败，证据保留，修正启动后完成。

这是一组定向失败回归，不能当整体准确率；可选附件/诊断标签争议未纳入本轮主分数。

入口：[q4_failure_replay.py](E:/GPTProject2/erp-harness-refactor/experiments/tool_routing/q4_failure_replay.py)。[结果与用量](E:/GPTProject2/erp-harness-refactor/.runtime/q4-failure-replay-20260928/RESULTS.json)、[原始8次结果](E:/GPTProject2/erp-harness-refactor/.runtime/q4-failure-replay-20260928/probe-results.jsonl)、[权限对照](E:/GPTProject2/erp-harness-refactor/.runtime/q4-failure-replay-20260928/permission-ablation/probe-results.jsonl)、[时序对照](E:/GPTProject2/erp-harness-refactor/.runtime/q4-failure-replay-20260928/chronology-ablation/probe-results.jsonl)、[最终字母复查](E:/GPTProject2/erp-harness-refactor/.runtime/q4-failure-replay-20260928/final-slots/results.jsonl)。同目录保存完整prompt、逐token日志、原请求路径、哈希与冻结标签。
