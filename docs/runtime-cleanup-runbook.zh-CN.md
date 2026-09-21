# Runtime 局部修复操作手册

基线：`88ac1039622d93860a0e2c6afa754440ac9509fd`。本手册记录待执行步骤；参数记录缺陷已离线复现，其余为优化候选，尚未实施或证明节省 token。

**1. 固定基线，每项单独提交。**

在仓库根目录执行以下检查；工作区有其他修改时先隔离。本轮先做 A、B，再考虑 C、D。

```powershell
git status --short
git rev-parse HEAD
git switch -c fix/runtime-local-cleanup-20260919
```

HEAD 与上述基线不同，应重新核对改动位置和对照版本。每项日志放入 `.runtime/runtime-cleanup/<项目>/<A或B>/<运行编号>/`，记录源码 SHA、配置和结果；每次使用新目录。

**2. 按下面的范围修改和验证。**

| 项目 | 最小修改 | 局部验收条件 |
| --- | --- | --- |
| A：参数与记录一致 | [odoo_tools.py](../integration/odoo_tools.py) 的 direct-read 在解析 World 身份前调用一次 `normalize_read_arguments`，身份、receipt、执行共用规范化参数；原始模型请求仍保存在日志。 | 省略 instance、JSON `null`、字符串 `"null"` 均读取默认实例并产生可回读 receipt；合法命名实例身份正确；不存在的实例和非法参数仍失败。正常对照中 `healthy=true`、`projection_enabled=true`。 |
| B：无命中不凑字段 | 删除 [reads.py](../odoo_runtime/reads.py) 中 `_field_candidates` 在 BM25 无命中时回退通用字段排名的 4 行代码。 | 无命中仍报 unknown field 并提示 `get_model_fields`，不带无关 `Valid candidates`；有命中保留真实候选；受限字段不出现在建议中。候选不能自动替换业务字段。 |
| C：静态规则只维护一份 | 将 [host.py](../workbench/host.py) 的重复静态规则逐句归入 [conversation.py](../workbench/conversation.py) 的 `CONVERSATION_POLICY`，补齐采购规则后缩短每轮 instruction。 | 采购缺信息、明确实时查询、带材料三类请求仍保留相应行为；业务上下文、材料的不可信标记、逐动作审批和对话只读边界完整。 |
| D：删除无效参数 | 删除 [worker.py](../workbench/worker.py) 两个 command 函数未使用的 `repo` 参数，同步两处 host 调用及现有测试。 | 生成命令的 native/dynamic/record、审批暂停及续跑参数一致；`Popen(cwd=self.root)` 和环境隔离保留。 |

A 的旧复现脚本在 `.runtime/code-simplification-audit-20260919/instance_null_route_repro.py`，它断言的是旧缺陷。保留原证据，把修复后的正向断言加入现有 receipt 测试；不能把旧脚本“仍通过”当成修复通过。此修复也不处理入口 schema 拒绝的 `relevance="null"`，无需放宽 schema。

**3. 只跑本项相关离线检查。**

使用已按 [README](../README.zh-CN.md) 配置的项目 Python 环境。在根目录设置：

```powershell
$py = '.\.venv\Scripts\python.exe'
$env:PYTHONPATH = "$PWD;$PWD\agent\src"
# A、B：补入上述回归断言后执行
& $py -X utf8 -B -m unittest tests.test_relation_receipts tests.test_world tests.test_tool_retrieval
```

C 执行 `& $py -X utf8 -B -m unittest tests.test_workbench_conversation tests.test_workbench_host`；D 只执行 `& $py -X utf8 -B -m unittest tests.test_workbench_host.WorkbenchHostTests.test_worker_contract_native_dynamic_world_and_clean_environment`。检查退出码为 0，再进入下一步；模块缺失先修测试环境，不能当作业务失败。本手册编写时未执行这些修复后的检查。

**4. 对会改变模型可见内容的 B、C 做局部 A/B。**

- B 从 2071 的字段错误轮取样，至少含一个无命中和一个有命中对照；C 使用上表三类对话。固定之前的完整请求，A 保留基线，B 仅替换本项涉及的工具回包或规则文本，保留工具调用与结果配对。
- 沿用已验收的 provider、模型、high reasoning 和超时配置。每个样本先各调用一次，只观察下一轮输出及工具参数；写入调用先检查提案，不向业务数据库执行。保留请求、响应、usage、工具参数和对比结果；只有出现失败或差异不明确时补同样的配对。
- B 检查能否使用真实字段或正确请求 schema，无新幻觉及 ACL 泄漏；C 检查是否问必要业务信息、按需只读查询且不擅自执行。一次配对不能证明稳定的成本改善。
- 分别记录新输入、缓存输入、输出（含 reasoning）、模型调用、错误及耗时；reasoning 不重复相加，缺失 usage 标为未知。入口 schema 拒绝也计入错误，不能只统计后端日志。

**5. 验收、回退与业务回归。**

每项离线检查通过后，按适用范围完成局部 A/B，再单独提交，记录“改了什么、验证结果、调用/token、日志目录”四行即可。A、D 以机制正确和兼容为准；B、C 以业务行为保持为前提，成本收益单独报告。

出现记录丢失、实例混淆、ACL/审批退化、新错误字段或业务行为变差，停止后续步骤并用 `git revert` 撤销该项提交，保留日志；继续使用上一个通过检查的提交。代码回退不会撤销 Odoo 已发生的写入，未知写入仍须核对，不能自动重发。

局部通过后，再在隔离 Odoo 环境选有关联的简单与复杂业务回归，保留配置、完整 trace、`reward.txt` 和 `verifier_details.json`。局部检查、读回成功和正式业务得分分别报告。

保留现有审批与执行前复核、BusinessFacts/write guards 分工、schema 大枚举摘要、原始日志回读、对话/执行隔离以及当前依赖；本轮无需新增工具、planner 或通用业务抽象。
