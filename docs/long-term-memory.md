# 长期记忆

后端实现：`src/erp_harness/memory`。Mem0 2.1.0、本地 Qdrant、中文/英文向量与 BM25。客户、产品、库存、订单仍由 Odoo 保存。

- 业务开始召回；已识别的字段/工具契约错误触发补充召回。每次最多 3 条，整项任务最多 6 KiB。
- 正常结束后独立进程学习，最多一次模型请求。使用工具回执，不学习私有 reasoning、审批 token 或最终回答中的成功宣称。
- 记忆绑定实际数据库、凭据、用户、公司和工具契约。历史经验不能批准写入、覆盖当前事实或证明业务正确。
- 未知写入、等待审批不进入成功经验。提炼结果须引用已有回执；语义正确性仍需当前业务核验。
- 相同内容去重，默认 30 天过期。召回失败时业务继续。学习用量单列。

桌面「连接设置 → 启用长期记忆（Mem0）」控制召回与自动学习，默认关闭。保存后后续执行生效，已有记忆保留；运行中或等待审批时不能切换。已启动的后台学习仍会完成。环境变量 `ERP_MEMORY_MODE=off` 可强制关闭。

启用后首次学习会准备约 235 MB 的本地向量模型及 BM25 文件；可提前准备：

```powershell
.venv\Scripts\python.exe -m erp_harness.memory setup <桌面数据目录>\memory
```

CLI 设置 `ERP_MEMORY_DIR` 后启用；`ERP_MEMORY_MODE=off` 可关闭。普通对话目前不自动提炼，业务 runner 负责学习。

查看记录：业务日志中的 `memory-events.jsonl`、`memory-learning.json`；后者指向 `memory/jobs/<id>` 的来源、提炼请求、完整响应、用量、候选和最终状态。`memory/scopes` 保存隔离的经验库。备份时连同整个桌面 profile 保存。

失败的学习不会自动重复付费请求。未发送请求的任务可用 `python -m erp_harness.memory learn <job目录>` 重试；已有请求须先核对回执。

采用 Mem0 的应用触发方式：[add/infer](https://docs.mem0.ai/core-concepts/memory-operations/add)、[后台集成示例](https://docs.mem0.ai/integrations/hermes)。当前实现提供经验积累；没有自动修改提示词、训练模型或宣称业务成绩会提升。
