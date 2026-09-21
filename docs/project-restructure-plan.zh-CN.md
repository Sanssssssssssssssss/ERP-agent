# 完整项目整理与迁移方案

基线：`fd8bcde3d91bfdc80cdbf4114794d0f235e9e717`，已推送。目标是一个完整 ERP Harness：统一后端、完整桌面客户端、独立 benchmark、可验证的安装与升级路径。
状态：方案；尚未搬动代码、执行迁移测试或调用业务 API。按下面的阶段逐项验收，故障定位到本批并回退；不承诺仅靠目录设计就消除运行风险。

**目标目录**

```text
ERP-agent/
├─ pyproject.toml                  一个后端安装入口、依赖与测试配置
├─ uv.lock                        固定 Python 依赖，覆盖 Windows/Linux
├─ src/erp_harness/
│  ├─ app/                        host、runner、普通对话、审批协调、材料与导出
│  ├─ runtime/                    模型循环、会话、事件、恢复、通用工具类型
│  ├─ providers/                  模型适配、流式协议、请求与用量记录
│  ├─ tools/                      工具路由、动态集合、SOP、工具清单资源
│  ├─ context/                    历史投影、压缩、World 证据与回读
│  └─ erp/                        Odoo 连接、读写、业务关系校验、动作账本
├─ desktop/
│  ├─ src/
│  │  ├─ main/                    Electron 生命周期、Python 进程、IPC、密钥和文件权限
│  │  ├─ preload/                 受限的页面通信桥
│  │  ├─ shared/                  现有 UI/host 类型与方法契约
│  │  └─ renderer/
│  │     ├─ App.tsx               页面布局与功能组装
│  │     ├─ features/
│  │     │  ├─ conversation/      会话列表、聊天、提案、材料
│  │     │  ├─ business/          业务工作区、执行阶段与事实
│  │     │  ├─ approvals/         审批列表、决定、待核对状态
│  │     │  ├─ documents/         单据、文件与导出
│  │     │  ├─ traces/            历史运行、证据与用量
│  │     │  └─ settings/          连接与配置界面
│  │     ├─ components/          确实被多个功能复用的展示组件
│  │     └─ styles.css           现有样式与视觉变量
│  ├─ scripts/                   构建、sidecar、桌面与安装包检查
│  ├─ assets/
│  ├─ package.json
│  └─ package-lock.json
├─ tests/                         后端、协议、恢复测试及必要的固定输入
├─ bench/                         任务、评分器、运行适配、配置及历史对照
├─ experiments/                   独立实验、历史报告与补丁
├─ docs/                          开发入口、模块地图、迁移与验收记录
├─ .github/workflows/             后端、前端、打包、历史对照 CI
├─ sources.lock.json              上游来源、版本和迁移映射
├─ THIRD_PARTY_NOTICES.md          来源与许可证；所需许可证随包交付
└─ .runtime/                      忽略的开发日志、构建缓存、验收 profile
```

根 `src/` 是 Python 产品代码，`desktop/src/` 是 TypeScript 产品代码。用户数据继续位于 Electron 的 `userData`，不放进源码或安装目录。功能目录只随实际代码迁入而建立。

**完整运行链路**

```text
React 页面 → preload 白名单桥 → Electron main → JSON 行协议 → Python app.host
                                                           ├─ 普通对话 worker：只读与提案
                                                           └─ 业务 worker：runtime → tools → erp
runtime → providers → 模型服务；context 管理历史视图与回读
状态/公开事件沿原链路返回页面；原始 trace 与动作账本保留在数据目录
bench adapter → 同一后端包的 runner → 隔离的 Odoo 环境
```

保留现有本地子进程通信、React/Electron 和依赖版本。Renderer 负责展示与用户意图；Python 决定业务状态与审批执行，Electron 管理系统权限与密钥。普通对话和执行 worker 继续隔离；桌面人工审批与 benchmark 的隔离环境自动审批配置分别保留。制造 benchmark 通过不扩大桌面业务范围。

**现有内容归位**

| 来源 | 去向与处理 |
| --- | --- |
| `workbench/`，`pi_odoo_runner.py`、`stream_events.py` | `app/`；共享后端配置，保留对话与执行入口。 |
| `pi_agent`、实际使用的 `pi_coding` 会话实现 | `runtime/`；将循环与会话代码纳入项目，解除 CLI/TUI/扩展启动依赖。 |
| `pi_ai`、模型配置及 provider 支撑代码 | `providers/`；依赖 runtime 的通用消息/工具协议，不导入 ERP 或 UI。 |
| 工具路由、动态工具、SOP；World/投影/压缩 | 分别迁入 `tools/`、`context/`，保留现有 hooks 的时机与顺序。 |
| 原生 reads/actions/guards/facts/store/gateway/capabilities 等 | `erp/`；`_odoo_core` 中使用中的实现与 JSON 数据一起纳入。 |
| `App.tsx` 内的现有功能组件 | 按上面的 features 提取；先保持 DOM、CSS、状态归属、事件订阅和交互一致。 |
| `integration/` 的 Harbor、评分、快照脚本；阶段 configs | `bench/adapters/`、`bench/configs/`；产品包不得反向依赖任务、答案或评分器。 |
| 根 `mcp/` 与历史对照测试 | `bench/reference/mcp/` 及独立对照环境；产品安装与启动不加载它。 |
| 未使用的 Pi CLI/TUI/示例与旧壳 | 解除真实依赖、核对资源与许可证后移出当前产品树，参考版本由基线 Git 提交保留。 |

前端先提取展示组件，再迁移所属状态与订阅；运行选择、审批与取消状态保持一个所有者。共享协议复用 `shared/protocol.ts`，用同组序列化请求/事件样本校验 Python 与 TS；Python/Electron 的实际入参和权限校验继续保留。

**执行顺序：每阶段可拆小批，每批独立提交，通过本批检查才继续**

| 阶段 | 工作 | 通过条件 |
| --- | --- | --- |
| 0：建立基线 | 从 `fd8bcde` 创建独立 worktree；在该基线运行相关现有检查、构建桌面包，保存源码/配置/依赖/产物哈希与固定 trace。 | 记录基线实际通过和失败项；历史 v0.5.4 安装包不能代替当前源码基线。准备独立验收 profile。 |
| 1：后端入包 | 增加根 pyproject，迁移自身后端命名空间；更新 Electron host、worker、benchmark、资源加载与打包入口。Pi 暂作显式安装的依赖。 | 新后端 wheel 可在仓库外启动；开发与打包入口均可用；相关 Python/IPC 检查通过。不能靠旧 checkout 或 PYTHONPATH 补齐缺失代码。 |
| 2：前端整理 | 提取 App 现有功能模块、整理展示辅助函数；保留 main/preload/shared 的权限分工。 | typecheck、self-check、renderer-check 通过；再用真实 Electron→Python 通信加 fake provider/Odoo 走通业务，覆盖桥接 mock 无法证明的链路。 |
| 3：融合运行内核 | 将 Pi 的 provider/loop/session/context 实现按职责迁入；以现有 hooks 配置替代外部修改 `session._harness.config`；逐项解除无关 coding 依赖。 | 固定输入回放中的模型请求、工具发布、事件、审批暂停及恢复顺序等价；旧会话可读，资源关闭语义保持。之后移除 Pi 安装依赖和旧源码目录。 |
| 4：归档与构建统一 | 移动 bench/configs/MCP 对照，更新 CI、文档、来源与许可证。桌面和 bench 消费同一 SHA 的后端 wheel，依赖按平台锁定。 | wheel 内仅有产品代码与必要资源；工具清单/模型重命名表可加载；Windows 桌面与 Linux bench 的安装冒烟通过。 |
| 5：交付验收 | 构建候选桌面包，测试旧 profile 副本、全新 profile、完整 UI 路径，再跑真实业务。 | 下列验收全部有记录；失败回退当前批次。PR 合入 main 前现有四项必需 CI 均通过。 |

**最终验收覆盖**

- 前端：会话切换、聊天流、材料导入、提案→建业务→显式开始、逐项批准/拒绝、取消、只读核对、单据与导出、trace/用量；快速切换与重复点击不串业务、不重复启动模型，重挂载不重复订阅；关键页面截图与基线对照。
- 后端：动态工具次轮发布、策略/prestate 复核、未知写入不重发、日志回读身份/hash、异常与用量未知值；桌面仍为单活动业务运行，取消后的状态和进程退出可核对。
- 契约：RPC 方法、请求/结果/错误形状、事件 sequence 与 session/business/run/action ID 的作用域保持；桥接边界不得向页面开放任意 shell、文件路径或明文密钥。
- 数据兼容：旧会话、审批账本、材料、产物索引、运行状态与历史证据能恢复；现有 UI 核验范围、已完成/待核对区分、未测字段的 unknown 语义保持。
- 安装包：保留现有 package-check 的启动检查，并增加带历史数据和真实 IPC 的检查；清除开发路径覆盖后，脱离源码目录启动；无须本机 Node/Python/WSL。portable 必验；发布 NSIS 时另验安装、启动与升级保留数据。
- 真实业务放最后：2003、2071 各一次，对照各自历史得分和规则；再用独立 Odoo 测试数据走一条桌面支持的简单采购业务，人工审批到写后回读。最多先安排这三次完整运行，已有 bench 结果不能代替桌面端到端证明。记录新输入、缓存输入、输出（含 reasoning）、调用数、耗时与全量日志；不默认跑 300 题。
- 付费对照沿用已验收的模型/provider/reasoning 设置，不新增模型运行限制；保留桌面 RPC/进程管理与模型请求各自既有超时语义。业务失败或新增无效轮次先定位；单次成本波动不能直接归因于重构。

**升级与回退**

目录迁移期间保留工具名、提示词、默认能力、IPC 通道、存储格式、文件名、appId、影响 userData 的应用身份及 localStorage 键。源码命名与存量数据兼容分开处理；现有局部修复手册中的功能改动也独立提交。
停止验收实例后备份完整 profile（`data`、`settings.json`、`Local State`）；在同一 Windows 用户的副本上验证密钥解密，不能只复制 settings 文件。初期保持存储格式兼容；若必须变更 schema，先补可验证的数据迁移与恢复步骤。
保留每批提交及最后验收安装包。失败撤销本批代码并恢复匹配的验收包/profile；代码回退不会撤销 Odoo 写入，已经发生或结果未知的写入继续核对。构建失败仍须保留上一份可运行 sidecar。

**开发与发布入口**

迁移后从根目录执行 `uv sync --locked` 安装后端，前端沿用 `npm --prefix desktop ci`、`npm --prefix desktop run dev`；选择同一项目虚拟环境的 Python。产品入口统一到 `erp_harness.app.host`，普通对话与业务 runner 作为受控子进程启动。
发布顺序为 `uv build --wheel` → `npm --prefix desktop run prepare:sidecar` → `npm --prefix desktop run dist:portable` → `npm --prefix desktop run package-check`。准备脚本消费指定的本次 wheel，固定平台依赖后装入 sidecar；manifest 记录源码、wheel、依赖、资源及产物哈希；bench 安装同一 wheel。完整交付包含桌面包、开发安装入口、模块地图、测试/业务验收记录与回退说明。

依据：[前端与打包配置](../desktop/package.json)、[IPC 桥](../desktop/src/preload/index.ts)、[桌面后端启动](../desktop/src/main/host.ts)、[现有验收脚本](../desktop/scripts/package-check.mjs)、[数据存储](../workbench/storage.py)、[CI](../.github/workflows/checks.yml)。
