# Odoo Agent

Windows 桌面工作台：会话、业务意图确认、销售、采购与开票工作区、逐项动作审批、
Odoo 状态回读、运行 Trace。模型循环复用 Python Pi `CodingSession`，
实际工具使用 `odoo_runtime`；不启动或导入 MCP。

0.4.0 是历史验收记录：已接通普通 Pi 会话与公开文本流，完整客户写入 Demo 当时仍待复跑；当前 0.5.0 的真实结果见 [拓扑验收](../experiments/desktop_workbench/TOPOLOGY_ACCEPTANCE_050.md)，问题定义见 [Demo 复盘清单](../experiments/desktop_workbench/DEMO_READINESS_REVIEW.md)。

0.5.0 增加 `sale_invoice`、`sale_purchase_invoice`、`purchase` 工作区类型，以及 CSV/TXT 材料导入和 Odoo 单据下载。真实模型复测及 ERP-Bench 材料核对见 [拓扑验收](../experiments/desktop_workbench/TOPOLOGY_ACCEPTANCE_050.md)。正式发票 PDF 需经审批生成；尚未生成时阻止把 Proforma 当正式发票下载。

材料入口只接受 UTF-8 CSV/TXT：单文件不超过 2 MiB、20000 字符、200 行；每会话最多 10 个材料，每条消息最多绑定 3 个。
单据可下载真实 Odoo PDF 或中文 UTF-8 BOM CSV；下载文件不超过 32 MiB，CSV 单据最多 100 条明细。PDF/CSV 均只导出当前业务已观测记录；ERP 写操作仍逐项审批。

## 使用

解压 `dist/win-unpacked` 后运行 `Odoo Workbench.exe`；必须保留同目录资源。
也可使用构建出的 portable EXE。应用内含 Python 和本地界面，用户无需安装
Node、Python、WSL，也无需启动前端服务器。Odoo 和模型服务由连接设置指定。

1. 打开连接设置，填写模型、模型服务地址、Odoo 地址/数据库/账号及对应密钥。
   网络连接使用 HTTPS；本机调试允许 localhost HTTP。密钥保留空白表示不更换。
   顶部连接状态可查看 Odoo 只读认证结果、地址、数据库、最近检查时间和耗时。
   模型“已配置”只表示配置齐全；不会为检查连接而发送模型请求。
2. 新建会话，可以先问能力或讨论范围。明确业务目标后，审阅提案并点击“创建业务工作区”。这一步只建立本地记录。
3. 点击开始执行。每项 ERP 写操作单独展示目标、参数和操作前状态，确认后继续。
4. 在执行台查看当前动作、业务阶段与基础核验；运行结束后点击“读取最新状态”。
   在单据与文件查看真实记录、来源回执、Odoo 入口并导出业务报告；已导出文件在重启后仍可定位和打开。在运行详情查看每轮调用与用量。
5. 后续消息可以讨论整个会话或选定业务的上下文。普通发送进入会话模型；需要执行时先审阅相应提案，再通过业务工作区明确启动。

同一业务的新运行会从真实历史配置回执恢复已启用的工具组；当前运行回执优先。
新业务或没有可验证配置的历史使用基础工具组，写操作仍逐项审批。续跑机制及成本检查见
`../experiments/desktop_workbench/TOKEN_COST_REVIEW.md`。

连接详情显示当前数据目录。会话与回执保存在当前 Windows 用户的应用数据目录；密钥通过 Windows DPAPI
加密。Trace 和 Pi 会话包含业务数据，请按业务数据管理这些本地文件。
升级与备份请保留完整 profile，包括 `data`、`settings.json` 与 `Local State`；仅复制加密设置文件不能保证解密。使用相同 `--user-data-dir` 才能继续原有会话。
模型停止和业务核验分开显示；未知 token 不计作零，推理 token 是输出的一部分。
独立回读保存在业务工作区中，不改写历史运行证据。这里的核验检查并非完整 ERP-Bench 评分。
执行台显示来自单据的订单、开票、付款及出库状态；未观测到的事实不推测。
阶段证据可以定位原始运行与工具，独立回读有自己的观察时间。查看历史证据不会改变当前执行状态。
连接检查只在启动、保存设置或手动检查时发起；执行期间保留最近结果，避免干扰运行和取消。

V1 支持同一会话的多个销售业务工作区，主机同时执行一项任务。异常中断不自动
重放写操作；不确定的写入进入待核对状态，可只读核对对应动作。核对成功只解除不确定状态，
不会自动恢复模型执行或宣布业务完成。当前版本为未签名的 Windows 试用构建。

## 开发与构建

在本目录执行：

```powershell
npm ci
npm run typecheck
npm run self-check
node scripts/renderer-check.mjs
```

开发启动使用仓库根目录的 Python 3.13 虚拟环境：

```powershell
py -3.13 -m venv ..\.venv
$py = (Resolve-Path ..\.venv\Scripts\python.exe).Path
& $py -m pip install -e ..\agent
& $py -m pip install -r .\requirements-host.txt
$env:WORKBENCH_HOST_ROOT = (Resolve-Path ..).Path
$env:WORKBENCH_PYTHON = $py
npm run dev
```

构建的 Python 依赖以 `requirements-host.txt` 固定；可在新的 Windows Python 3.13
虚拟环境内安装该文件，并用 `WORKBENCH_SITE_PACKAGES` 指定其 site-packages。
默认位置是 `..\.venv\Lib\site-packages`；Python 压缩包缓存于 `..\.runtime\cache`。
准备脚本核对依赖版本和官方 CPython 压缩包 SHA-256，复制当前仓库源码及许可证。
不会打包 `.env`、历史实验数据、Compiler、MCP SDK 或开发环境的可执行程序。

```powershell
$env:WORKBENCH_SITE_PACKAGES = (Resolve-Path ..\.venv\Lib\site-packages).Path
npm run prepare:sidecar
npm run dist:portable
# 或 npm run dist:installer
```

打包后忽略开发时的 Python/host 路径覆盖。使用独立验收 profile 启动可以保留
日常会话：`"Odoo Workbench.exe" --user-data-dir=E:\path\to\acceptance-profile`。

## 修改入口

| 需求 | 入口 |
| --- | --- |
| 页面与交互 | `src/renderer/App.tsx`、`styles.css` |
| 基础组件与视觉变量 | `src/renderer/main.tsx` 的 Radix Theme、`styles.css` 的 `:root` |
| 桌面权限、进程和密钥 | `src/main/`、`src/preload/` |
| 会话、运行和审批 | `../workbench/host.py` |
| 普通会话与提案 | `../workbench/conversation.py` |
| 公开文本事件 | `../integration/stream_events.py` |
| 销售事实与回读核验 | `../workbench/sale_view.py` |
| UI/host 数据契约 | `src/shared/protocol.ts` |
| 模型循环接入 | `../integration/pi_odoo_runner.py` |

增加业务类型时先新增其事实投影与核验规则，再扩充工作区页面；不复制模型循环。
验收依据见 `../experiments/desktop_workbench/PLAN.md`、`ACCEPTANCE.md`、`UI_REFINEMENT.md`
和 `PRESSURE_AND_LIVE.md`。0.3.0 的正式四子页、作用域回归、原生桌面和真实业务验收
以 `PRODUCTION_ACCEPTANCE.md` 为准。
