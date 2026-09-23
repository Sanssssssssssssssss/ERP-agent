# 单次决策回归

12 个真实 trace 节点，A/B 各一次请求；返回工具意图只保存，不执行。完整请求、推理、原始响应与判定依据在 `.runtime/agent-regression-20260923/`。

```powershell
python -m experiments.agent_regression.freeze
python -m experiments.agent_regression.provenance
python -m experiments.agent_regression.prepare
python -m pytest tests/test_agent_regression.py -q
# 由负责人设置 COMMAND_CODE_API_KEY 后，执行已授权的 24 次请求：
python -m experiments.agent_regression.runner --paid
python -m experiments.agent_regression.runner --summary
```

可用 `--case A01`、`--group tool_contract`、`--arm candidate` 选择子集。每个节点/分支只允许启动一次；失败、取消或用量不明均不补发。HTTP 无超时、无重试、无自动续跑。

`freeze.json` 锁定来源、oracle 和上下文哈希；`cases/*/manifest.json` 的 `boundary` 说明故障传播切点，不能将其当成上游根因的独立证明。A03/B02/B03/C03/D03 是正确保护，D01/D02 是已修问题防回归。B01/C01/C02 的上游问题是当前阶段与历史目标混入；A01/A02 检查工具契约。两个旧 prepared 分支保留其真实来源标识。

`provenance.json` 补充源码版本、根因位置、首次可纠正请求和归档证据哈希。历史补丁版本以当时 source-hashes 为准；未知 commit 不猜测。D01/D02 保留祖先原请求；C02 勘误仅澄清“三类绑定各需唯一”，原 oracle 与通过条件不变。

| 节点 | 检查点 | 根因或保护范围 |
|---|---|---|
| A01 | 确认请求 1 | 首轮裸工具名；名称契约缺口是待验证原因 |
| A02 | 确认请求 7 | SOP 已在请求 3 消费；区分方法与字段写入 |
| A03 | 确认请求 12 | 目录权限误报缺模型；保护正确确认动作 |
| B01 | 开票请求 29 | host 目标串接在请求 1 已出现 |
| B02 | 投递请求 8 | 保护合法发送提案 |
| B03 | 最终追问请求 2 | 保护 SMTP 接受证据与不重发 |
| C01 | 开票请求 39 | 同一范围根因；PDF 拒绝后的恢复点 |
| C02 | 开票请求 41 | 同一范围根因；缺绑定后的交接点 |
| C03 | 资格查询请求 3 | 保护业务选择权 |
| D01 | 已修 B01 分支 | 内部公司联系人不是订单客户 |
| D02 | 已修 C06 分支 | 公司与客户查询范围歧义 |
| D03 | 原 B02 请求 2 | 金额冲突需要澄清；源码 commit 未知 |

候选调用生产 builder，仅替换声明位置；逆替换必须还原完整原请求。这只控制实验输入，**不要求输出遵循 Golden Trace**。正确身份、授权、业务约束和证据决定结果；额外合理只读、措辞或工具顺序变化允许通过人工复核。无法确定的语义标为 `needs_review`，不调用模型裁判。单节点不证明最终业务成功。

`results/*/*/` 保存请求、原始 SSE、意图和结构检查。`summary.json` 单列 fresh/cache/output、reasoning 子集、请求数、工具意图、耗时与未知值；这些数字不参与业务正确性打分。最终业务回归由负责人另行执行。
