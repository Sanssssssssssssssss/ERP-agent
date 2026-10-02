<p align="center"><img src="desktop/assets/workbench.png" width="72" alt="ERP-agent" /></p>
<h1 align="center">ERP-agent</h1>
<h3 align="center">让 AI Agent 真正操作你的 ERP。</h3>
<p align="center">给出业务目标，审查拟议变更，在 Odoo 中核对实际结果。</p>
<p align="center"><a href="#演示">演示</a> · <a href="#开始使用">开始使用</a> · <a href="docs/development.zh-CN.md">文档</a> · <a href="TESTING.md">测试</a> · <a href="README.md">English</a></p>
<p align="center"><a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT" /></a> <a href="https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml"><img src="https://github.com/Sanssssssssssssssss/ERP-agent/actions/workflows/checks.yml/badge.svg?branch=main" alt="Checks" /></a> <img src="https://img.shields.io/badge/Odoo-19-714B67" alt="Odoo 19" /> <img src="https://img.shields.io/badge/Desktop-Windows-467C7A" alt="Windows 桌面" /></p>

## 演示

![ERP-agent：审批销售订单确认并核验结果](docs/media/demo.gif)

> “S00012 未税金额低于 5,000 元就确认。S00009 不处理，不发货、不开票。”

Agent 读取订单，为具体变更请求审批，执行获批动作，再从 Odoo 回读结果。执行台把目标、当前动作、审批和证据放在一起，聊天始终在旁边。

演示压缩呈现了一次真实模型/API 跑测的记录状态，业务发生在隔离 Odoo 测试企业中。[验证记录](docs/ui-integration-20261002.md)分别列出这次桌面业务，以及用于检查整合后端的三道 ERP-Bench 任务。

**销售 · 采购 · 库存 · 制造 · 开票 · 付款 · 退款 · 对账**

## 从业务要求到 ERP 结果

- **说明业务。** 在聊天中查询和沟通，确认业务目标与范围后开始执行。
- **跟上进度。** 直接看到当前动作、执行过程、模型流式消息和已观测单据。
- **审查变更。** 审批展示影响的记录、拟提交值和当前状态，在执行台批准、拒绝或提出修改。
- **检查结果。** 先看核验结论；进入 Trace 查看工具用途、参数、风险、回执与原始记录。

业务状态、审批和动作账本会持久保存，支持中断后恢复。不确定写入必须先核对再继续。工具按当前任务开放，字段访问与逐项审批保持明确。

![Trace：工具用途、风险与结果详情](docs/media/workbench-trace.png)

## 开始使用

需要自己的 **Odoo 19 JSON-2 连接**与**模型服务**。

上图新版桌面可从源码运行。已发布的 [Windows v0.6.0 预览包](https://github.com/Sanssssssssssssssss/ERP-agent/releases/download/v0.6.0/Odoo-Workbench-0.6.0-windows-x64.zip)使用此前的界面，具体见[发布说明](https://github.com/Sanssssssssssssssss/ERP-agent/releases/tag/v0.6.0)。

### Windows 源码启动

需要 Node.js 22+、Python 3.13 和 [uv](https://docs.astral.sh/uv/)。

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

打开**连接设置**填写 Odoo 与模型凭据，然后新建聊天。可附加 CSV、TXT、XLSX、PDF 或图片作为参考材料。设置支持深色外观、减少动画与聊天存储目录。印刷文档在本地提取；不计算表格公式。

可复现环境见[持久 Demo](experiments/demo_odoo/README.md)和[一万单据验证企业](experiments/enterprise_validation/README.md)。示例 [orders.csv](experiments/desktop_workbench/fixtures/bench_2262_orders/orders.csv)只匹配 ERP-Bench 2262 seed。

### 开发检查

```powershell
npm run typecheck
npm run self-check
npx playwright install chromium
node scripts/renderer-check.mjs
npm run check:execution
npm run check:trace
```

后端检查与覆盖索引见 [TESTING.md](TESTING.md)。打包、固定 sidecar 输入与回退命令见[开发指南](docs/development.zh-CN.md)。

## 运行时与验证

桌面使用原生 Python Harness、动态工具、受控业务规程和业务状态记录，直接连接 Odoo。历史 MCP 对照代码保留在研究目录。已接入按身份隔离的 BM25 知识检索；[长期记忆](docs/long-term-memory.md)按需启用、默认关闭。独立向量 RAG 实验未接入桌面。

上述业务类型有本地测试覆盖，具体通过项和限制见[企业验收记录](docs/enterprise-validation.md)、[人工业务结果](docs/manual-business-results.md)与[后端验证边界](docs/backend-release.md)。这些测试不证明生产银行、税务或 HR 集成。已过账发票不代表已付款，Odoo 回读通过不等于基准得分。

[ERP-Bench 与 Harbor](bench/)提供独立评估路径，包含固定任务、快照和评分。Mock 与 renderer 检查验证界面机制；真实 API 跑测和 Odoo 回读验证记录中的业务。

[工具纠错验证](docs/tool-self-debug-20261003.md)逐项记录故障根因、局部 API 决策、完整业务复跑与 token 用量。

## 仓库

| 路径 | 作用 |
| --- | --- |
| [desktop/](desktop/) | Electron 应用、React 界面、IPC 与打包 |
| [src/erp_harness/app/](src/erp_harness/app/) | 会话、执行、审批、材料与业务展示 |
| [src/erp_harness/erp/](src/erp_harness/erp/) | 原生 Odoo 读写、策略与核验 |
| [src/erp_harness/](src/erp_harness/) | Agent 运行时、模型适配、上下文与工具 |
| [bench/](bench/) | ERP-Bench、Harbor 适配器与固定配置 |
| [experiments/](experiments/) | 可复现控制、证据与研究记录 |

## 来源与贡献

原创整合代码采用 MIT。适配组件保留各自许可证及固定来源，见 [sources.lock.json](sources.lock.json)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)和[界面源码声明](docs/licenses/execution-ui-NOTICES.md)。

贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)，历史入口见[实验室记录](docs/history/README-lab.md)。
