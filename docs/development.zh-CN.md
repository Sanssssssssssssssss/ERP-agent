# 开发与运行

先读 `src/erp_harness/app/host.py` 和 `desktop/src/renderer/App.tsx`。

| 目录 | 职责 |
| --- | --- |
| `src/erp_harness/app` | 桌面 host、普通对话、业务 runner、材料与导出 |
| `runtime`、`providers`、`context` | 会话循环与持久化、模型协议、上下文与原始证据回读 |
| `tools`、`erp` | 工具编排、Odoo 读写、审批前复核与动作账本 |
| `desktop/src/renderer/features` | 会话、业务、审批、单据、trace、设置；`useWorkbench` 持有跨功能状态与唯一订阅 |
| `desktop/src/main`、`preload`、`shared` | 系统权限与密钥、受限 IPC 桥、通信类型 |
| `bench` | 任务、评分、适配器、配置及历史 MCP 对照；不进入产品 wheel |

开发环境：Python 3.13、uv、Node.js 22。

```powershell
uv sync --locked
npm --prefix desktop ci
$env:WORKBENCH_HOST_ROOT = $PWD.Path
$env:WORKBENCH_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
npm --prefix desktop run dev
```

离线检查：

```powershell
uv run --locked pytest -q tests --ignore=tests/test_native_reads.py --ignore=tests/test_capabilities.py --ignore=tests/test_knowledge.py --ignore=tests/test_baseline_fixes.py
npm --prefix desktop run typecheck
npm --prefix desktop run self-check
node desktop/scripts/renderer-check.mjs
```

MCP 对照在独立虚拟环境安装根项目、`bench/reference/mcp`、`mcp==2.0.0` 和 `mcp-types==2.0.0`，只运行上面排除的四个模块。原始试验记录中的旧目录对应关系见 `sources.lock.json`；旧 CLI/TUI 项目由基线提交保留。

发布构建使用独立依赖环境；Windows sidecar 和 Linux benchmark 安装同一 wheel。Python 嵌入包下载、依赖版本、失败时保留旧 bundle 的检查仍执行。

```powershell
uv build --wheel
$env:UV_PROJECT_ENVIRONMENT = "$PWD/.runtime/sidecar-env"
uv sync --locked --no-dev --no-install-project
Remove-Item Env:UV_PROJECT_ENVIRONMENT
$env:WORKBENCH_SITE_PACKAGES = "$PWD/.runtime/sidecar-env/Lib/site-packages"
npm --prefix desktop run prepare:sidecar
npm --prefix desktop run dist:portable
npm --prefix desktop run package-check
node desktop/scripts/ipc-check.mjs
```

`WORKBENCH_BACKEND_WHEEL` 可指定本次 wheel；sidecar manifest 与 benchmark 的 `backend-wheel.sha256` 必须一致。构建、离线通过与真实业务通过分别记录。

用户数据仍在 Electron 的 `userData`。停止验收实例后复制完整 profile，包括 `data`、`settings.json` 和 `Local State`；使用 `--user-data-dir=<副本>` 验证升级。源码回退不撤销 Odoo 写入，未知写入只做核对，不自动重发。存储名称、应用身份和 IPC 通道继续兼容已有记录。
