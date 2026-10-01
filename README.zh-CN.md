# ERP-agent

**面向真实 Odoo 业务操作的安全型 Agent 工作台。**

ERP-agent 让 Agent 先提出 ERP 业务变更，在写入前暂停并请求人工审批，执行 Odoo 19 操作后再通过独立回读核对最终记录。仓库包含原生 Python Agent Runtime、Windows 桌面工作台、可恢复执行状态和 ERP-Bench 评测链路。

[English](README.md) · 简体中文

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Checks](https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml)
[![Release](https://img.shields.io/github/v/release/Sanssssssssssssssss/ERP-agent?display_name=tag)](https://github.com/Sanssssssssssssssss/ERP-agent/releases/latest)

**Windows 预览版 · v0.6.0 · Odoo 19**

[下载 Windows 预览版](https://github.com/Sanssssssssssssssss/ERP-agent/releases/download/v0.6.0/Odoo-Workbench-0.6.0-windows-x64.zip) · [Release 说明](https://github.com/Sanssssssssssssssss/ERP-agent/releases/tag/v0.6.0) · [企业验证台账](docs/enterprise-validation.md) · [能力测试索引](TESTING.md)

![ERP-agent](docs/media/hero.svg)

> 能够修改业务系统的 Agent，核心问题是控制。什么可以写、什么时候必须审批、中断后怎么恢复、最终状态怎么验证。ERP-agent 围绕这些约束设计。

如果你也在做会修改业务状态的 Agent，可以 Star 或 Watch 仓库跟进后续 Release 和验证结果。

## 为什么做 ERP-agent

很多 Agent Demo 到工具调用就结束了。ERP-agent 重点处理模型决定执行动作之后的工程问题。

| 能力 | 当前实现 |
| --- | --- |
| **受控 ERP 写入** | 支持的写操作先生成拟执行动作，明确审批后才执行，并记录操作前状态和最终回读。 |
| **可恢复执行** | 中断业务可以从持久状态继续，不依赖静默重跑整个流程。 |
| **原生 Odoo Runtime** | 已验收运行路径直接调用原生 Odoo helper，并根据运行状态动态暴露工具。 |
| **业务状态验证** | 工作台区分拟执行动作、执行结果、已观测记录和独立评测。 |
| **Human-in-the-loop** | 审批属于 Runtime 合约的一部分，不只是前端确认框。 |
| **持久记忆** | 提供身份隔离的长期记忆和 BM25 知识检索，长期记忆默认关闭。 |
| **独立评测** | ERP-Bench 与 Harbor 适配器独立于工作台回读链路进行评测。 |
| **桌面操作界面** | Electron 工作台展示审批、业务单据、运行 Trace、导出和恢复流程。 |

当前工作台业务投影覆盖 sale_invoice、sale_purchase_invoice、purchase、inventory、manufacturing、payment、refund 和 reconciliation。

## 实际执行链路

![执行台](docs/media/workbench-overview.png)

上图是使用合成数据的真实 renderer 界面。Runtime 的主链路是

~~~text
用户业务目标
    ↓
Agent 规划业务动作
    ↓
读取当前 Odoo 状态
    ↓
生成拟写入动作
    ↓
人工审批
    ↓
执行已批准动作
    ↓
独立回读 / 核对
    ↓
单据、Trace 和最终回复
~~~

<details>
<summary>审批、单据和 Trace 视图</summary>

![审批视图](docs/media/workbench-approvals.png)

审批卡片展示拟执行动作、操作前状态和逐项决策控件。

![单据视图](docs/media/workbench-documents.png)

单据视图展示已观测的 Odoo 记录和导出动作。

![Trace 视图](docs/media/workbench-trace.png)

Trace 记录运行、工具和用量信息。截图均使用合成演示数据。

</details>

![流程示意图](docs/media/workflow.svg)

## 有验证链路，不只是一套 Demo

仓库包含标准验证企业环境，提供隔离的中文/CNY Odoo 公司对、角色账号和 10,000 份来源文档。[企业验证台账](docs/enterprise-validation.md)记录实际验收结果和仍未通过的 Gate。

ERP-Bench 始终作为独立评分器。工作台回读成功只能证明预期 Odoo 状态被观测到，不会被当成 ERP-Bench 分数。

仓库同时保留 ERP-Bench 固定数据集、Harbor 集成、实验配置、回归测试和开发过程中的来源元数据。

## Windows 预览版

v0.6.0 安装包已经包含整合后的后端和中断业务恢复。

1. [下载未签名 Windows 预览版](https://github.com/Sanssssssssssssssss/ERP-agent/releases/download/v0.6.0/Odoo-Workbench-0.6.0-windows-x64.zip)。
2. 解压后运行 Odoo-Workbench-0.6.0-portable.exe。
3. 在连接设置中填写自己的 Odoo 19 和模型配置。
4. 新建会话，说明业务目标，可选地附加 CSV/TXT 文件。

预览版需要用户自己的 Odoo 19 实例和模型服务。示例 [orders.csv](experiments/desktop_workbench/fixtures/bench_2262_orders/orders.csv) 只匹配 ERP-Bench 2262 seed 数据，不是通用 Odoo 成功样例。

## 工作台当前能力

- 运行 [src/erp_harness](src/erp_harness) 中整合后的 Agent 会话循环、Provider、上下文组装、动态工具和原生 Odoo helper。
- 在已验证业务投影内处理销售、采购、开票、审批、库存、制造、付款、退款、对账、回读和运行 Trace。
- 按工作台限制接收 UTF-8 CSV/TXT 材料。
- 将已观测单据明细导出为 UTF-8 BOM CSV。
- Odoo 已存在对应正式附件时下载客户发票 PDF。
- 支持的 ERP 写操作逐项审批。
- 记录操作前状态、执行结果、已观测状态和 Trace 证据。
- 提供受控 SOP 工具和身份隔离的持久 BM25 知识检索。
- ERP-Bench 与 Harbor 评测链路和操作工作台分离运行。

独立的 experiments/company_records_rag 向量实验没有接入 Agent 或工作台。

## Windows 开发快速开始

需要 Windows、Node.js 22 或更新版本、Python 3.13 和 uv。

~~~powershell
git clone https://github.com/Sanssssssssssssssss/ERP-agent.git
cd ERP-agent

uv sync --locked

cd desktop
npm ci
$env:WORKBENCH_HOST_ROOT = (Resolve-Path ..).Path
$env:WORKBENCH_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe).Path
npm run dev
~~~

打开工作台连接设置，填写模型服务和 Odoo 19 JSON-2 连接信息。网络连接使用 HTTPS；localhost HTTP 用于本地开发。应用不会为了检查配置而额外发送模型请求。

在 desktop/ 下可运行

~~~powershell
npm run typecheck
npm run self-check
npx playwright install chromium
node scripts/renderer-check.mjs
~~~

desktop/package.json 提供 Windows portable 和 installer 命令。打包还需要 desktop/scripts/prepare-sidecar.mjs 和 desktop/requirements-host.txt 所描述的固定 sidecar 输入。

先构建后端 wheel，并准备不含开发依赖的打包环境。具体构建、验证和回退命令见[开发指南](docs/development.zh-CN.md)。

## 仓库地图

| 路径 | 作用 |
| --- | --- |
| [desktop/](desktop/) | Windows Electron 工作台、界面契约和打包脚本 |
| [src/erp_harness/app/](src/erp_harness/app/) | 会话、审批、存储、材料和业务投影宿主 |
| [src/erp_harness/erp/](src/erp_harness/erp/) | 原生 Odoo helper 和 Runtime 能力 |
| [src/erp_harness/](src/erp_harness/) | 整合后的会话 Runtime、模型适配、上下文、记忆和工具 |
| [tests/](tests/) | Runtime、能力、恢复、路由和回归测试 |
| [bench/adapters/](bench/adapters/) | Harbor、评分、快照和报告适配器 |
| [bench/](bench/) | ERP-Bench 任务、schema 和 Harbor 集成 |
| [bench/configs/](bench/configs/) | 阶段和基线实验配置 |
| [experiments/](experiments/) | 验证证据、限制和历史研究记录 |
| [sources.lock.json](sources.lock.json) | 固定来源提交、许可证、树和 bundle 哈希 |

旧实验入口保留在 [docs/history/README-lab.md](docs/history/README-lab.md)。

## 边界

ERP-agent 当前仍是预览版和研究支撑的工程项目，不宣称已经覆盖完整 ERP 生产场景。

- 已验收的原生运行路径不启动或导入 MCP，固定 MCP 源码仅作为来源和历史对照实验保留。
- 原生工具清单保留部分 mcp_odoo_* 兼容名称，src/erp_harness/tools/router.py 会在调用原生 adapter 前去掉该标识。
- Odoo 写入需要明确审批。不确定写入进入回读/核对状态，不会静默重放或直接标记完成。
- PDF 导出针对已观测的 Odoo 单据。预付款发票或已过账发票都不能证明银行付款已经发生。
- 制造、库存转移、付款、退款和对账是在上述本地合成企业中完成验证。
- 真实银行/税务集成、工资、HR、OCR 和完整企业文档索引不属于当前验证范围。
- 工作台接收 CSV/TXT 材料，目前不宣称完整生产部署能力。

## 来源与贡献

本仓库包含自写 Harness 代码和 [sources.lock.json](sources.lock.json) 中固定的来源材料。重新分发组件前请查看 [LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [docs/licenses/pi-agent-NOTICES.md](docs/licenses/pi-agent-NOTICES.md)。

贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。
