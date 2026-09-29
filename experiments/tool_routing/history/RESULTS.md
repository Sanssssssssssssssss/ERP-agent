**原始调用模板不晋级；不能据此判定 Laya 整体不可用。** 2026-09-24，本地 RTX 4070 Laptop 实测发现标签敏感和状态表达问题。实验分支 `experiment/laya-routing-20260924`，生产基线 `4c2bf7f`；生产代码和依赖未修改。

| 检查 | 结果 |
|---|---|
| 历史池 | 22个run、10个业务家族、365个去重请求；345个动态节点，20个聊天节点排除 |
| 注入决策 | 342/345个节点选满10组，平均额外可见26.72个工具 |
| 历史下一步覆盖代理 | 原active覆盖299/309；候选309/309，但平均多选9.48组；36个异常节点不参与此指标 |
| 独立审阅 | 8个动态节点均选满10组，均超出预先审阅的合理集合；另4个聊天范围控制不参与推理 |
| SDK延迟 | 主池P50 285ms / P95 311ms；冷加载7.89秒；主池推理合计94.49秒 |
| 内存 | PyTorch峰值分配约1.72GiB，不含完整进程/驱动开销 |

例如确认销售单的起始截面，Laya同时选了员工、请假和跨库迁移。`actions`概率0.8843，低于`employee`的0.8856；简单提高统一阈值不能在这个节点保住actions又排除employee。覆盖率提高来自近乎全开，不能算编排改善。历史集合不同本身不判业务失败；这里的关键是明显无关能力和新增schema负担。

最初12次`choice`诊断仍用了布尔词标签，未排除[官方警告的标签偏置](https://github.com/NandhaKishorM/laya/blob/23a17522aa4942da6cce53a995a275760320b691/README.md#honest-limits)。这是首轮实验遗漏，不能把“换类型后仍全选”当成已排除调用设计问题。

后续固定6个开发截面进行诊断，没有重测留出集或调阈值：

- **安装/参数**：权重哈希匹配、strict加载、P(true)读取无取反；官方8语言部门分类在BF16/FP32均8/8。未发现明显加载错误，未证明所有版本数值等价。
- **可见信息**：改为原文目标＋完成条件＋当前能力＋最近回包，六个输入最大611 token，全部完整可见。原题干仍5/6全开；保留原始输入、仅扩大到4096窗口后，actions/employee两个哨兵组仍6/6都选中。信息裁切是缺口，但不是唯一解释。
- **标签因果对照**：只换`noul labels`，其余状态、题干、criteria、阈值不变。原始输入中53/60判断翻转；紧凑输入中59/60翻转。true=A大多全开，true=B全部不选；不能把后者包装成改好了。
- **更清楚的题干**：具体业务意图问题、仅看目标、去掉裸success布尔词都改变了输出，但仍有无关组或漏选。单选先尝试识别主要能力也不稳定：同一紧凑输入下，中性标签保持语义映射、反转选项顺序，6节点中5个改选。单选诊断不等于已完成多组注入。

目前证据支持“当前checkpoint与接口模板存在强标签/顺序敏感”，不支持“只因信息没看到”，也不支持“换标签后即可上线”。实际编码后的完整序列已保存，输入与判定规则可以复查。

累计**461次本地SDK调用、4260项typed判定**；编码器输入3,833,698 token，各问题重复编码共享状态，这是本地计算量。原首轮为345＋8＋12次，其后96次为开发诊断/官方示例检查。**生成式API调用0、API费用0、Odoo调用0**。输出token为0表示不自回归生成；SDK延迟不含外围预处理和发布。

评测池尚有覆盖缺口：实际可选工具调用的正例主要是actions，其余9组无实际调用正例；accounting和diagnostics分别有3次、1次历史配置请求。154个动态节点按业务家族留出；历史曾用于开发，不称全新未见集。主池只有1个纯能力配置响应，另13个配置响应同时读取业务，不能宣称省掉14轮。后续应补真实能力切换轨迹和稳定性验收，再考虑领域适配；当前不接入业务执行。

复现源码：[构建历史池](../build_cases.py)、[本地决策](../laya_probe.py)、[格式诊断](../format_probe.py)、[预先审阅规则](../reviewed_cases.json)。

完整本机证据：

- [主池统计](../../../.runtime/laya-routing-20260924/noul-v1/summary.json) · [逐节点决策](../../../.runtime/laya-routing-20260924/noul-v1/predictions.jsonl) · [冻结问题与版本](../../../.runtime/laya-routing-20260924/noul-v1/frozen.json)
- [审阅节点统计](../../../.runtime/laya-routing-20260924/reviewed-noul-v1/summary.json) · [格式诊断](../../../.runtime/laya-routing-20260924/format-controls-v1/predictions.jsonl)
- [来源与覆盖](../../../.runtime/laya-routing-20260924/dataset/coverage.json) · [完整请求指针与哈希](../../../.runtime/laya-routing-20260924/dataset/cases.jsonl) · [环境版本](../../../.runtime/laya-routing-20260924/environment.txt)
- [紧凑输入与实际可见token](../../../.runtime/laya-routing-20260924/input-controls-v1/predictions.jsonl) · [语义题干对照](../../../.runtime/laya-routing-20260924/semantic-controls-v1/predictions.jsonl)
- [标签对照](../../../.runtime/laya-routing-20260924/label-controls-v1/predictions.jsonl) · [官方示例/精度/选项顺序](../../../.runtime/laya-routing-20260924/route-controls-v1/predictions.jsonl)；两个目录内各有独立runner与冻结输入。
