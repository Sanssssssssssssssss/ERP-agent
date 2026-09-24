本轮确认训练方案存在缺口；不能据此判定 Laya 学不会。新增本地训练诊断，付费 API 0 次，Odoo 0 次。生产路由未接入。

当前训练：多语言 checkpoint，冻结编码器，训练 head、type_emb、scorer 共 14,770,945 参数。十组能力分别做二选一；必选为正、无关为负、不确定不计损失。AdamW、LR 3e-5、batch 16、20 步预热、四轮共 64 次更新。它是监督微调，没有做 RLCD 或端到端业务奖励训练。

核实的缺口：

- 1,944 条历史响应中，本轮实际训练只有 28 个独立节点、6 个业务家族。245 个已判定节点×能力标签中，正例仅 14 个：actions 12、knowledge 2；八组没有正例。actions 还只有一个明确负例。
- 最终选中第 1 轮，只更新 16 步，预热未结束。所有开发轮次通过数都是 0，选模依赖少注入一些组；原权重没有参加候选比较。不能把“选出权重”理解成训练成功。
- 每个标签每轮随机抽一种格式。重放随机种子发现，第 1 轮在正式评测格式下没有任何必选正例；两个 knowledge 正例都落在另一种格式。随机增强没有保证小样本覆盖。
- checkpoint 默认长度 1,024。62 个节点中 54 个的全部能力问题都超过该长度；每节点最长问题的中位数 2,519，最大 6,399 token。没有实际截断，但长输入是否影响效果尚未隔离验证。
- 原 loss 来自每轮不同格式、不同权重的训练样本，不能作为固定验证集的收敛曲线。

本地控制使用已有训练集的四个真实节点：actions、knowledge 各一正一负；保留完整冻结 state。复用实际训练函数与官方 forward，缓存冻结编码器以节省计算。

| 条件 | 优化步数 | 固定格式正确数 | 四种标签/顺序正确数 | 四格式平均 CE |
|---|---:|---:|---:|---:|
| 原权重 | 0 | 2/4 | 8/16 | 0.8023 |
| 只训固定格式 | 128 | 3/4 | 10/16 | 0.9145 |
| 只训固定格式 | 384 | 4/4 | 12/16 | 1.6289 |
| 每节点均衡训练四格式 | 384 | 4/4 | 16/16 | 0.0114 |

固定格式最终 CE 为 0.00041，证明训练链路能拟合。单格式训练仍会让其他格式更自信地犯错；均衡训练修复了这四个节点的格式敏感性。均衡分支每步展示更多格式，总训练呈现量不同；不能解释成相同计算量下的效率提升。这是同节点拟合检查，尚未证明新业务泛化。

检查通过：真实 encoder 回算与缓存输出一致、SDK 与原始 logits 选择一致、权重保存/重载一致；已有路由测试 11/11。没有发现标签索引反转、无梯度或读取旧权重的故障。首次单节点检查中，关闭 dropout 后 train/eval 输出一致，尚未做全池 FP32/BF16 对照。

下一轮先修训练采样和选模：每个已审标签显式覆盖四种格式，验证也覆盖四种格式；原权重作为 epoch 0，固定 CE、必选召回和误注入同时记录。训练步数由收敛检查决定。再补真实业务正负例和困难负例，以业务家族留出；稀缺能力继续走现有编排。输入改为短而明确的当前阶段、已完成事实、待处理动作和缺失信息，单独验证，不能靠裁掉证据压长度。

论坛依据：[作者确认标签偏差需要训练解耦](https://github.com/NandhaKishorM/laya/issues/156#issuecomment-5783798349)；[作者建议把区分事实放在选项内](https://github.com/NandhaKishorM/laya/issues/171#issuecomment-5783800308)；[冻结编码器的可复现实验](https://n.demir.io/articles/testing-and-fine-tuning-laya/)使用 3,965 个拟合样本、992 个验证样本，验证选轮次。其英文分类成绩不能外推到中文 ERP。

另见 [stuntd 作者的实际训练方案](https://www.reddit.com/r/LocalLLaMA/comments/1wnt7kv/stuntd_a_local_jevcompatible_server_on_laya_that/)：按决策点收集样本、训练任务头、校准后回退；演示标签来自规则或 oracle，尚非我们的企业测试。[act_probability 问题](https://github.com/NandhaKishorM/laya/issues/185)是另一输出头的缺陷，本实验不使用该值，不能拿它解释此次能力误选。

证据：[采样与长度审计](../../.runtime/capability-routing-20260924/training-audit.json)、[128 步](../../.runtime/capability-routing-20260924/trainability-v1/)、[固定格式 384 步](../../.runtime/capability-routing-20260924/trainability-fixed-384/)、[均衡格式 384 步](../../.runtime/capability-routing-20260924/trainability-balanced-384/)。各目录保留源码、输入哈希、逐次评估和权重。

复现：在仓库根目录，用独立实验 Python 执行 `-m experiments.tool_routing.training_probe --source .runtime/capability-routing-20260924/reviewed-v3 --output <新的本地目录> --steps 384 --balance-formats`；去掉 `--balance-formats` 即固定格式控制。
