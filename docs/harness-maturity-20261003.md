本轮完成原生 Harness 的执行边界与诊断修复。五次完整 ERP-Bench 中四次为 100 分；中途一次 2279 得 21.09 分，采购合并约束失败，原记录保留。最新 `e95810e` 的 2279 回到 100 分。完整 Python 回归为 **1716 passed、4 skipped、473 subtests**；之后 `4c28225` 只补充聊天诊断策略，相关 **21 passed、33 subtests**。源码机制、模型理解与官方业务评分分别留证。

冻结标签 `harness-maturity-baseline-20261003` 已推送，仍指向 `374277821e71aa12e5bf23728998fee758799165`，起始产品源码为 `4753300`。工作分支 `codex/harness-maturity-20261003`；回退可从标签另建分支。每次修复的因果请求、源码哈希、普通测试、API 与独审对应关系保存在[回归绑定](../experiments/agent_regression/harness-maturity-20261003/bindings.json)。

| 工程边界 | 已修复及验证 | 证据限制 |
|---|---|---|
| 输入、输出与工具错误 | 拒绝布尔记录编号；保留参数、字段、查询、权限、连接和坏回包的具体分类；部分诊断失败保留未知。 | 普通测试覆盖原生 producer 与路由；未引入通用工具输出框架。 |
| 授权、审批与恢复 | 校验当前身份、业务范围、执行前状态及发送账本；诊断不产生授权；旧审批报告结构保持兼容。 | 未知写入禁止自动重放已做离线控制；完整业务没有触发真实未知写入故障。 |
| 留证、重试与预算 | 正文及 prepared 回执保存失败阻止 POST；发送后记录失败保留当前结果并阻止新请求；逐 attempt 检查预算；拒绝不完整流式终止。 | 重点验证默认 OpenAICompatible 的 Chat/Responses；其他 provider 的 attempt hook 未证明等价。 |
| STOP 与业务核验 | 可选放行字段绑定已确认原话及当前元数据；最终分别保留字段与采购容量证据；疑似拆单给出警告及未核验项目。 | 字段存在、容量通过、动作 verified 或模型 STOP 均不等于完整任务通过。日期关系、报价合并等须各自核验。 |
| 日志、压缩与 self debug | 旧请求安全保留 HTTP 状态；部分读取失败置于长结果前；诊断返回动作计数和关联覆盖；聊天可读宿主选定快照。 | 历史回执不等于当前 ERP；缺关联保持未知；通用超长诊断仍受既有压缩边界限制。 |

Notion zero2Agent 对照保留 **17 页及 27 个源码文件**，检查工具管线、审批、重试、终止、日志与压缩。[笔记审查与来源](../.runtime/harness-maturity-20261003/notion/audit.md)记录版本及全文哈希。页面是固定阅读副本，接口未证明原始 block 完整性，不能宣称覆盖全部课程。改动复用既有校验、账本、回执与 World，没有新增生产模块、框架或依赖。独审也修正了可选采购诊断改变旧审批 prestate 结构的问题。

2279 的真实失败来自四层：模型保留旧 Indigo 采购单又按同一报价另建单；容量工具未检查报价合并；运行时没有宿主绑定的合并规则；STOP 只核对账本涉及的采购单。原评分器拒绝 `po_consolidation_compliance`。新增诊断保持容量判断原义，只提示疑似拆单。局部 API 随后提出取消新单、重新验证旧单数量与来源的方案，工具没有执行。[因果案例](../experiments/agent_regression/harness-maturity-20261003/consolidation-case.json) · [API 回执](../experiments/agent_regression/harness-maturity-20261003/consolidation-api-results.json)

最新完整 2279 使用原任务、原评分器、独立数据库及冻结 wheel，仅运行一次：**32/32 约束、6/6 hygiene、100 optimality**。最终 Indigo PO1 从 12 调至 16，Sierra PO3 为 15，Cosmo PO2 取消；销售单保留，未收发货、未制造。4 次写入 verified，另 1 条旧批准记录未发送。62 次工具调用中 3 次旧字段拒绝随后恢复，无未知写入或重复发送。拆单诊断返回 `no_candidates`，这次正常通过不能证明警告促成纠正。[完整业务与独审索引](../experiments/agent_regression/harness-maturity-20261003/business-results.json)

历史聊天“为什么失败了”暴露了另一处真实问题：模型把仅列失败/未决项的空列表，说成没有写入回执。增加回执计数和元数据覆盖后，单回包 API 仍误判，失败原样保留。随后仅在普通聊天及运行中查询共用的策略中补充 3 句：报告账本状态、报告覆盖限制、区分最后中断与历史拒绝。单 system 替换的实测已能说明 8 条本地已验证动作、历史关联不完整、当前 ERP 未知，核对前不要重复写入；独审四项要求通过。最新 520 对应模型输出中断，更早是否有业务拒绝仍未知。回答首段单独摘出仍偏绝对，结果附此措辞限制。[聊天分阶段回执](../experiments/agent_regression/harness-maturity-20261003/actual-chat-api-results.json)

模型用量按请求回执去重；输出已含推理，推理列为子集，压缩另列。模型为 `deepseek/deepseek-v4.1-flash / high`，没有单次输出 token 上限。已完成请求用量已知，费用未知。本目标前的基线 3,680,054 tokens 和旧聊天 97,538 tokens 不计入本轮预算。

| 案例 | 请求 | 新输入 | 缓存读取 | 输出含推理 | 推理子集 | 压缩 | 总 tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2010 · 7153a79 · 100 分 | 49 | 181,716 | 2,865,792 | 55,576 | 45,722 | 0 | 3,103,084 |
| 2066 · 7153a79 · 100 分 | 28 | 205,500 | 1,603,712 | 58,926 | 48,852 | 0 | 1,868,138 |
| 2279 · c55f8ee · 100 分 | 19 | 120,719 | 529,024 | 30,109 | 23,992 | 0 | 679,852 |
| 2279 · 1238985 · 21.09 分 | 29 | 225,989 | 1,632,128 | 78,010 | 69,581 | 0 | 1,936,127 |
| 2279 · e95810e · 100 分 | 28 | 106,798 | 1,078,272 | 32,656 | 26,320 | 0 | 1,217,726 |
| 制造缺字段 · 意图 API | 1 | 3,216 | 63,488 | 459 | 344 | 0 | 67,163 |
| 查询域纠错 · 意图 API | 1 | 1,889 | 30,720 | 659 | 231 | 0 | 33,268 |
| 采购合并 · 意图 API | 1 | 1,502 | 106,496 | 20,240 | 19,960 | 0 | 128,238 |
| 历史问题聊天与纠错 API | 5 | 38,917 | 53,760 | 1,897 | 853 | 0 | 94,574 |
| 本目标合计 | 161 | 886,246 | 7,963,392 | 278,532 | 235,855 | 0 | 9,128,170 |

本目标 API 预算 100,000,000 tokens，已用 9,128,170，剩余 **90,871,830**，见[总账](../experiments/agent_regression/harness-maturity-20261003/business-results.json)。付费失败冻结完整上下文、schema、请求/调用 ID 与源码哈希；没有自动付费重试。合成故障仅用于离线检查。

验收证据：[全量测试绑定](../.runtime/harness-maturity-20261003/patchset7b-verification.json)、[业务独审](../.runtime/harness-maturity-20261003/business-patchset7b-2279/private/independent-audit-2279.json)、[聊天策略独审](../.runtime/harness-maturity-20261003/diagnostic-reporting-guidance/independent-review.json)、[诊断 API 独审](../.runtime/harness-maturity-20261003/diagnostic-policy-candidate/api/independent-verdict.json)。4 个本地跳过项为 3 个 Windows 不支持的 POSIX shell 场景及 1 个缺少独立邮件正文夹具的检查。Harbor 嵌套 reward 解析异常单独记录，官方分数直接取自未改动的原评分器。

[最终产品源码 CI](https://github.com/Sanssssssssssssssss/ERP-agent/actions/runs/37120986068) 的 Python、桌面、Linux wheel、历史比较、sidecar/桌面打包五项均通过，源码提交为 `4c28225`。

尚未实测：运行中查询与真实写入同时发生的模型并发路径、真实未知写入故障恢复、其他 provider 的等价重试预算行为。通用自由文本目标不能由字段存在性核验全部覆盖。本轮支持已审查路径的合入评审，不代表所有业务、故障和模型回答均已覆盖。
