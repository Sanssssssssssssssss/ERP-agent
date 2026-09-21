# ERP-agent

Native ERP Agent Harness

[English](README.md) · 简体中文

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Checks](https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml)

ERP-agent 用于起草 Odoo 业务动作，在写入前请求审批，并核对结果记录。本仓库包含 Windows 桌面工作台、原生 Odoo 19 运行时及其背后的 ERP-Bench 研究 Harness。

**Windows 预览版 · v0.5.4 · Odoo 19**

![ERP-agent](docs/media/hero.svg)

## Windows 预览版（0.5.4）

[下载未签名 Windows 预览版](https://github.com/Sanssssssssssssssss/ERP-agent/releases/download/v0.5.4/Odoo-Workbench-0.5.4-windows-x64.zip) · [Release 说明](https://github.com/Sanssssssssssssssss/ERP-agent/releases/tag/v0.5.4)

1. 下载并解压，然后运行 `Odoo-Workbench-0.5.4-portable.exe`。
2. 在连接设置中填写自己的 Odoo 19 和模型配置。
3. 新建会话，说明业务目标，可选地附加 CSV/TXT 文件。

这是未签名预览版，需要用户自己的 Odoo 19 实例和模型服务。示例 [`orders.csv`](experiments/desktop_workbench/fixtures/bench_2262_orders/orders.csv) 只匹配 ERP-Bench 2262 seed 数据，不是通用 Odoo 成功样例。

![执行台](docs/media/workbench-overview.png)

实际 UI 截图使用合成数据；下方流程图是示意图。

<details>
<summary>审批、单据和 Trace 视图</summary>

![审批视图](docs/media/workbench-approvals.png)

审批卡片展示拟执行动作、操作前状态和逐项决定控件。

![单据视图](docs/media/workbench-documents.png)

单据视图展示已观测的 Odoo 记录和导出动作。

![Trace 视图](docs/media/workbench-trace.png)

Trace 展示运行、工具和用量记录；截图使用合成演示数据。
</details>

![流程示意图](docs/media/workflow.svg)

流程图是示意图；截图展示实际 renderer 界面。

[快速开始](#windows-开发快速开始) · [边界](#边界) · [仓库地图](#仓库地图) · [来源与贡献](#来源与贡献)

## 能做什么

- 用 `odoo_runtime` 中的原生 Odoo 能力运行 Python Pi 风格会话循环。
- 提供 Windows Electron 工作台，覆盖销售、采购、开票、审批、状态回读和运行 Trace。
- 按工作台限制接收 UTF-8 CSV/TXT 材料，把已观测单据明细导出为 UTF-8 BOM CSV；客户发票只有在 Odoo 已生成并可读的正式 PDF 附件存在时才提供 PDF 下载。
- ERP 写操作逐项审批，记录操作前状态、结果和独立回读。
- 保留研究阶段使用的 ERP-Bench 数据集和 Harbor 集成。

当前工作台业务投影包括 `sale_invoice`、`sale_purchase_invoice` 和 `purchase`。原生运行时已有受控 SOP 工具及 knowledge index/search/stats。独立的 `experiments/company_records_rag` 向量实验没有接入 Agent 或工作台。ERP-Bench 独立负责评分；工作台回读不等于 ERP-Bench 得分。

桌面 worker 使用 `native` 执行、`dynamic` 工具、`controlled` SOP 和 `record` 业务状态记录。当前源码整合了桌面底座与后续 benchmark 后端改动；上方 v0.5.4 下载仍是此前发布的预览版。固定来源与离线检查见[后端整合及验证边界](docs/backend-release.md)。Benchmark 的 `TaskEvidence` 需要宿主显式配置，桌面 worker 尚未启用。

## Windows 开发快速开始

需要 Windows、Node.js 22 或更新版本、Python 3.13 和 uv。

```powershell
git clone https://github.com/Sanssssssssssssssss/ERP-agent.git
cd ERP-agent

uv sync --locked

cd desktop
npm ci
$env:WORKBENCH_HOST_ROOT = (Resolve-Path ..).Path
$env:WORKBENCH_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe).Path
npm run dev
```

打开工作台连接设置，填写模型服务和 Odoo 19 JSON-2 连接信息。网络连接使用 HTTPS；localhost HTTP 用于本地开发。应用不会为了检查配置而发送模型请求。

在 `desktop/` 下可运行：

```powershell
npm run typecheck
npm run self-check
npx playwright install chromium
node scripts/renderer-check.mjs
```

`desktop/package.json` 提供 Windows portable 和 installer 命令。打包还需要 `desktop/scripts/prepare-sidecar.mjs` 和 `desktop/requirements-host.txt` 所描述的固定 sidecar 输入。
先构建后端 wheel，并准备不含开发依赖的打包环境。具体构建、验证和回退命令见[开发指南](docs/development.zh-CN.md)。

## 边界

- 已验收的 Stage 7 原生运行路径不启动或导入 MCP；固定 MCP 源码仅作为来源和历史对照实验保留。
- 原生工具清单保留部分 `mcp_odoo_*` 兼容名称；`src/erp_harness/tools/router.py` 会在调用原生 adapter 前去掉该标识，不经过 MCP transport。
- Odoo 写入需要明确审批。不确定写入进入回读/核对状态，不会静默重放或标记完成。
- PDF 导出针对已观测的 Odoo 单据；预付款发票或已过账发票都不代表银行付款已经发生。
- 制造计划/确认、银行、对账、工资、HR、OCR 和企业级全文档检索不属于当前工作台的覆盖承诺，除非具体来源和实验另有说明。
- 工作台接收 CSV/TXT 材料，不宣称完整企业文档索引或生产部署能力。

## 仓库地图

| 路径 | 作用 |
| --- | --- |
| [`desktop/`](desktop/) | Windows Electron 工作台、界面契约和打包脚本 |
| [`src/erp_harness/app/`](src/erp_harness/app/) | 会话、审批、存储、材料和业务投影宿主 |
| [`src/erp_harness/erp/`](src/erp_harness/erp/) | 原生 Odoo 辅助能力和运行时 |
| [`src/erp_harness/`](src/erp_harness/) | 已融合的会话运行时、模型适配、上下文与工具 |
| [`bench/adapters/`](bench/adapters/) | Harbor、评分、快照和报告适配器 |
| [`bench/`](bench/) | ERP-Bench 任务、schema 和 Harbor 集成 |
| [`bench/configs/`](bench/configs/) | 阶段和基线实验配置 |
| [`experiments/`](experiments/) | 阶段证据、限制和历史研究记录 |
| [`sources.lock.json`](sources.lock.json) | 固定来源提交、许可证、树和 bundle 哈希 |

旧实验入口保留在 [`docs/history/README-lab.md`](docs/history/README-lab.md)，其中相对仓库的链接已按归档位置修正。

## 来源与贡献

本仓库包含自写 Harness 代码和 `sources.lock.json` 中固定的来源材料。重新分发组件前请查看 [`LICENSE`](LICENSE)、[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 和 [`docs/licenses/pi-agent-NOTICES.md`](docs/licenses/pi-agent-NOTICES.md)。贡献方式见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，安全问题见 [`SECURITY.md`](SECURITY.md)。
