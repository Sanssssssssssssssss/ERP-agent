# Laya 按需追加与制造日期 SOP

已接入 runtime。仅判断未开放的候选组；A 加入、B 不加入。已开放组保留至本次 run 结束，审批续跑恢复，新的 run 从基础工具开始。新增工具只追加到末尾，恢复入口也不重排。必需依赖、审批、ACL 和未知写入保护保留。

制造日期语义只补 SOP：预测／实际日期、工单日期传播、UTC、未来开工恢复、完工回读。不修改 System Prompt，不加入过去计划日期的一刀切禁令。

112 项测试＋9 个子检查通过。原 v7 权重、问题和投射代码不变，无训练。真实历史 80 个状态做本地 GPU 回放，无主模型 API 或业务写入：

|案例|原集合变化|追加后变化|GPU 选择次数|最终组数|
|---|---:|---:|---:|---:|
|E01|6|3|13/13|3|
|E02|29|5|23/42|8|
|E03|17|4|22/25|8|

后续观察沿用历史，不能当闭环业务通过或远端缓存实测。E02/E03 最终仍积累全部8组；追加机制消除撤下/重加，但误加、工具面变宽仍需单独验证。暂不据此重训或宣称成本下降。

三个真实日期节点已冻结完整请求及候选：MD01 规划、MD02 错误恢复、MD03 历史成功保护。仅替换生产 builder 生成的制造 SOP 步骤，逆替换还原原请求；模型行为尚未付费验证。

配置：[config.json](../../../.runtime/laya-additive-20260929/config.json)。原 bundle 未改；旧配置有源码哈希校验，当前源码应配新配置。回退原件：[before](../../../.runtime/laya-additive-20260929/before)。证据：[GPU 回放](../../../.runtime/laya-additive-20260929/gpu-replay/summary.json)、[日期节点](../../agent_regression/manufacturing_dates.json)。

复现：`.venv/Scripts/python.exe -m experiments.tool_routing.laya_additive_replay --trial .runtime/laya-prefix-live-20260929 --config .runtime/laya-additive-20260929/config.json --output <新的本机目录>`。这只运行本地 GPU，不执行 ERP 工具。
