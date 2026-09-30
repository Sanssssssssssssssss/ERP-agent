# 工具能力编排实验

当前接入：Laya v7 在主模型请求前判断需要追加的 capability。同一 run 保留已发布工具及 schema 顺序；候选为空时跳过选择。审批、动作校验和业务回读仍由 runtime 负责。

- [E02 修复与验收](E02_CONSUMPTION_FIX_20260929.md)：9/9 通过。
- [E02 / E03 实跑](LAYA_ADDITIVE_LIVE_20260929.md)：E03 5/5；其中 E02 旧失败已由上项修复。
- [模型回归池](../../tests/fixtures/capability_routing/README.md)。
- [历史报告与命令](history/README-20260929.md)：按报告日期解释，不作为当前启动说明。

| 入口 | 用途 |
|---|---|
| `src/erp_harness/app/capability_routing.py` | 请求前选择、追加发布及失败恢复 |
| `src/erp_harness/providers/selector_service.py` | host 管理本地常驻选择器 |
| `src/erp_harness/providers/laya_worker.py` | Laya 推理与模型清单验证 |
| `laya_context_replay.py` | 冻结真实请求的本地上下文对照 |
| `laya_additive_replay.py` | 追加能力语义的离线重放 |
| `live_trial.py` | 隔离业务验证；付费运行需明确授权 |
| `router.py` | 历史实验入口与模型导出，复用生产推理实现 |

启动前设置 `ERP_CAPABILITY_ROUTER_CONFIG` 指向本机已验收的配置 JSON。配置、GPU 环境、权重和清单需配套；仓库不包含权重。旧名称 `ERP_OPENJEV_CONFIG` 保留兼容，优先使用新名称。未配置时沿用原能力选择路径。

OpenJev 仅保留独立实验与回归入口，正式后端只接受 Laya；其[上下文契约](history/CONTEXT_CONTRACT.md)与 Laya 不混用。训练脚本路径保留，避免破坏冻结清单和历史复现。完整请求、权重、数据库及日志位于忽略的 `.runtime/`；本目录只保存代码、索引和无密钥结果。

发布模型：`python -m experiments.tool_routing.release_bundle --config <旧配置> --output <新目录>`，随后对冻结请求做 GPU 重放。新清单校验安装包内的 runtime 文件，不依赖 experiments 或源码 checkout。桌面启动只从可信启动环境接收 `ERP_CAPABILITY_ROUTER_CONFIG`；不会把任意 Python 路径开放给模型或 renderer。
