# OpenJev 接入前验证 · 2026-09-28

v9自由生成24/28：核心对照12/12，扩展12/16；多选0，漏选4，选项顺序分歧2。同一思考改用受限选项argmax读分后25/28，知识漏选消失；附件两次及诊断一次仍错，顺序分歧1。28次来自12个历史请求、14个能力组合，两种顺序不是独立样本。没有修改标签。

改动：补基础工具用途及候选参数schema；保留验证结果的delivery/record_model及invoice_records；明确可选检查也可开放。新增PDF与SMTP证据区分保护。

剩余错点：知识题把建索引误当作三步操作全部完成；附件两次、诊断一次仍把“不是必需”当作“不应提供”。后三次的正标签表示可用性，不能直接算业务失败。知识题的搜索、统计确实尚未完成。

事先门槛：至少26/28、无漏选/未知/顺序分歧。当前未通过。保留旧接口，未接入生产、未启动SALE/E01真实业务，付费API/Odoo调用均0。worker草案移到隔离证据目录，源码中不保留未启用入口。

本地GPU生成输入198,096、输出86,586，推理用量见机器报告；生成约31.7分钟。读分另输入284,654，不增加思考，约99秒；阈值0.1至0.7成绩均25/28。57项离线回归通过，冻结文件哈希全部核对。最终选项读分独立报告，不冒充完整worker验收。

[原请求、输出与哈希](E:/GPTProject2/erp-harness-refactor/.runtime/q4-integration-20260928/v9/frozen.json) · [四个失败的原始trace链接](E:/GPTProject2/erp-harness-refactor/.runtime/q4-integration-20260928/v9/failures.json) · [完整结果](E:/GPTProject2/erp-harness-refactor/.runtime/q4-integration-20260928/results.json) · [回退前源码](E:/GPTProject2/erp-harness-refactor/.runtime/q4-integration-20260928/before)
