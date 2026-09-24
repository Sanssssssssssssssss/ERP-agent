本实验只评估：在主模型请求前，由 Laya 建议注入哪些 capabilities。生产后端不接入；不预测工具参数，不执行工具，不授予写权限。

历史池已扩展至 1,944 个响应。最新对照采用具体选项、完整决策头和历史计划输入：[论坛调优结果](FORUM_TUNING.md)。此前结果：[scorer 微调](TUNING.md)、[原始模板](RESULTS.md)；[测试入口](../../tests/fixtures/capability_routing/README.md)。

最新维护：复核池扩到 62 个节点，增加原始完整请求与同摘要能力选择的 24 次真实 API 对照，以及修正标签后的本地训练。见 [本轮审查与结果](REVIEWED_RESULTS.md)。旧训练器默认拒绝弱标签，显式复现才使用 `--allow-reference-labels`。

当前主模型会自己调用 list/configure，再在下一轮拿到工具。Laya 可以前置能力选择，但包含业务读取的混合轮不能直接删掉。首轮用本地多语言 checkpoint，每个 capability 一个 `noul` 问题；一次 `system_one(state, questions)` 批量输出十组概率。0.5 只是固定实验阈值，不代表已校准。

运行入口：

```powershell
# 冻结本轮复核池；输出目录必须不存在
.venv/Scripts/python.exe -m experiments.tool_routing.model_comparison freeze --output .runtime/routing-reviewed-new
# 已授权后运行 12 节点 × 2 分支；每分支一次 POST，返回工具不执行
.venv/Scripts/python.exe -m experiments.tool_routing.model_comparison paid --output .runtime/routing-reviewed-new
# 修正标签后的独立本地训练；不调用付费 API
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.train_reviewed --source .runtime/routing-reviewed-new --output .runtime/routing-head-new
.venv/Scripts/python.exe -m experiments.tool_routing.model_comparison summary --output .runtime/routing-reviewed-new --laya .runtime/routing-head-new
# 原项目 Python：从真实 trace 构建；不调用 API/Odoo
.venv/Scripts/python.exe -m experiments.tool_routing.build_cases --self-check
.venv/Scripts/python.exe -m experiments.tool_routing.laya_probe --self-check
# 独立实验 Python：真实本地 Laya 推理
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.laya_probe
# 独立审阅节点；不覆盖主池
.venv/Scripts/python.exe -m experiments.tool_routing.build_cases --reviewed --self-check
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.laya_probe --dataset .runtime/laya-routing-20260924/dataset-reviewed --output .runtime/laya-routing-20260924/reviewed-noul-v2
# 已跑首轮后，开发节点的格式/顺序诊断；结果目录禁止覆盖
.runtime/laya-routing-20260924/venv/Scripts/python.exe -m experiments.tool_routing.format_probe
```

依赖、权重、下载版本、完整回放和结果均在 `.runtime/laya-routing-20260924/`。Laya 上游锁定 `23a17522aa4942da6cce53a995a275760320b691`；多语言权重锁定 `e4e9ddf21a7b1903b7acffd8814ad4307bf63a67`。只安装到实验 venv，生产依赖不变。新结果目录不得覆盖已有运行。

数据按整条业务家族划分 dev/heldout，重复网络请求合并但保留来源。原请求、响应和 schema 均保留路径与 SHA256。模型只读 `input.state`：请求发生前的原文片段；oracle、历史下一步、后续结果不进入模型。片段裁剪与模型实际 token 截断分别记录。留出数据已有历史开发接触，不冒称全新未见 benchmark。

评测分三项：

- **注入相关性**：所需新增组是否漏选、多注入多少组/工具、base-only/无新增组、复合能力。历史下一步仅作覆盖代理；独立审阅允许多个合理注入集合，不要求 Golden Trace 相同。
- **边界**：conversation 固定工具合同不参与动态路由；建议不能改变审批、身份校验或未知写入保护。此旁路实验没有验证生产发布与执行，只输出建议。
- **开销**：冷启动、暖态 P50/P95、本地输入 token、额外可见工具数，以及纯编排/混合编排响应数量。保持历史 active 的预加载只可能增加 schema；不能包装成 schema 节省。未做闭环对照前，不报告主模型调用或业务总成本下降。

`reviewed_cases.json` 是子 agent 独立审阅的12个旧回归截面，其中8个动态节点、4个conversation负控制；不是365主池里已经审核的12题，也不是人类签字验收。

依据：[BFCL多轮评测](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html)覆盖缺工具、缺参数、多步骤；[ToolSandbox](https://github.com/apple-aiml-research/ToolSandbox#evaluation)按状态与里程碑允许不同轨迹；[Anthropic工具搜索](https://www.anthropic.com/engineering/advanced-tool-use)说明延迟与schema开销需要共同测量。

[Laya官方源码](https://github.com/NandhaKishorM/laya/tree/23a17522aa4942da6cce53a995a275760320b691)说明多语言默认1024 token、head256，支持显式提高上下文长度，且出厂概率未充分校准。原始实验沿用默认窗口；完整决策头实验按实际状态长度分配窗口，保留输入摘要的省略标记。它是本地非生成式分类接口，不能按聊天 completion API 调用，也不能因为输出结构固定就认为决策一定正确。
