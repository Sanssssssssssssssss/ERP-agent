# 第一阶段验收记录（2026-09-03）

## 本轮进展：真实读取通过，待模型 A/B

用户已授权继续修复普通实现问题，并要求每阶段模型测试及子 agent 复查。目前未晋升阶段一基线，尚未进入阶段二；下面的旧停止记录保留历史事实，不再作为本轮停止规则。

- 共用客户端配置，补齐 MCP SDK 参数边界（忽略额外参数、旧版 `query="null"` 转换），使用真实 `Tool.run` 验证序列化契约。原生核心仍不导入 MCP SDK/服务。
- 最新离线回归：**931 项 MCP 测试通过，17 项集成测试通过、1 项历史 C 组 Node 检查跳过**。日志为 `.runtime/stage1/check-20260903T131022Z/`；Harbor 配置校验确认 2 个 entrant、1 个 case、串行执行。
- 真实 Odoo 管理员/受限账号 **28 项对照通过**，另有字段脱敏、记录规则、空值、字段数量的独立断言；模型调用数为 0。
- 从一次 seed 保存数据库、配置实际指定的 filestore 和测试 API key；恢复拒绝覆盖已有数据库/文件，逐项校验内容。已验证 **550 张表、445 个文件**一致，manifest SHA-256 为 `619f33b4b099c9eaa6544496562a36a87864eecd48146303fde73d4bdf9e70bb`。
- 恢复错误定位为数据库默认排序规则差异（源 `C / C.UTF-8`，默认新库 `C.UTF-8 / C.UTF-8`），已按源规则创建；没有跳过指纹检查。两组从相同快照启动、禁用自主 cron、删除镜像中的两个场景构造文件，并在任何模型调用前强制核对恢复回执。
- 安装错误定位为 `--system` 把新版 cryptography 装进 Odoo 的 Python，导致新 Odoo 进程加载旧 pyOpenSSL 失败。现改用 `/tmp/pi-odoo-env`，真实 Odoo shell 已正常完成测试身份配置。仍继承系统包，尚非第七阶段的完全独立发行包。
- 受限用户 API key 的期限要求 datetime，测试夹具已修正；原失败日志保留。只改可丢弃的 `bench_read_gate` 测试库，没有改基准快照中的业务数据。
- 两组共同的 A0 健康投影不向模型展示 MCP 进程局部 N+1/热点计数，原始结果保存在 `health-process-telemetry.jsonl`；权限与安全策略不删除。不能将本轮与旧 60 轮基线直接计算效果提升。

本轮无模型证据位于忽略提交的 `.runtime/stage1/`：`fixture-r6.log`、`read-gate-r6-provision-r2.log`、`read-gate-r6-r2.log`、`read-gate-r6/`。原始快照位于 `20260903T125545Z/snapshot/`，含私有测试密钥，不上传 GitHub。

首对配置为 `configs/stage1-ab.json`：同模型、同 high reasoning、同工具契约、同快照；A 全 MCP，B 仅四个读取工具走原生。每组最多 1,800 秒，无本地输出 token 上限或 60 轮上限；须 62 条规则全通过且自然结束。启动时需设置 `PI_ODOO_LAB_ROOT`、`PI_ODOO_SNAPSHOT` 为 WSL 绝对路径，并将 Harbor 0.22 放到 PATH。每次重跑须另取 job_name，不覆盖旧证据。

## 历史：首次在模型 A/B 前停止

Status: **incomplete, not an accepted native-read baseline**. No paid model
requests were made. Neither A nor B ran the business canary; C and Compiler
were not run. The existing 100-point historical baselines remain unchanged.

## What was implemented

- Opt-in native execution for four read tools, keeping the discovered Pi tool
  names, schemas, descriptions, text serialization, and structured results.
- The native implementation reuses pinned Odoo core helpers without importing
  the MCP SDK/server; a lazy package facade makes that import boundary testable.
  This is not a standalone MCP-free distribution: Pi and the remaining tools
  still have their existing MCP dependencies.
- A closed JSON-2 read-method allowlist, existing field ranking/redaction/cache,
  actual backend dispatch logs, Odoo JSON-2 attempt metadata, and report exports.
- Optional Python turn limit defaults to None; the experimental Python config
  removes its old 60-turn ceiling and retains the 1,800-second timeout.
- Offline checks and a no-LLM live-read gate/provisioning script. The latter is
  NOT validated: execution stopped during snapshot preparation before it ran.

## Checks and failures

1. Initial integration gate: 12 passed, 1 skipped (the old native-Pi Node hook
   unit check; Node is not on this WSL PATH). The read parity table covers 23
   fake-client inputs and full AgentTool output serialization for all four tools.
2. One combined pytest invocation failed during collection because both trees
   use `tests` as an import namespace. Running each suite from its proper root
   fixed the entry point: **931 MCP tests passed**, then **12 integration checks
   passed and 1 skipped**. See `integration/check_stage1.sh`.
3. The first detached fixture stopped during Odoo setup: ExitCode 255,
   OOMKilled false; Docker restarted. This is an environment interruption,
   not a scored agent result. Exact host shutdown causality is not proven.
4. A single recovery attempt used a fresh same-image, same-case container with
   foreground attachment and unchanged 3-CPU/4-GiB limits. Seeding completed.
   The subsequent preparation script nevertheless failed with:

   ```text
   tar: filestore: Cannot stat: No such file or directory
   tar: Exiting with failure status due to previous errors
   ```

   The script incorrectly assumes `/var/lib/odoo/filestore`. Read-only inspection
   confirmed `odoo.tools.config['data_dir'] == '/root/.local/share/Odoo'` and an
   existing `/root/.local/share/Odoo/filestore/bench`. This is our preparation
   script bug. Per the user's stop rule, no further repair or test was attempted.

## Known pending work, not silently repaired

- Derive the snapshot filestore path from the actual Odoo configuration; do not
  hardcode a user-home-dependent location. The current snapshot is incomplete.
- Align native connection defaults before live parity: `build_odoo_client`
  defaults to a 30-second timeout, whereas direct `OdooClient` construction
  defaults to 10. Also the live-gate script currently sets `ODOO_LANG`, while the
  existing core reads `ODOO_LOCALE`. These are source-review findings, not live
  differential results; the current prototype must not be promoted as equivalent.
- Run the real admin/restricted-principal differential checks. Only after they
  pass, complete identical-snapshot A/B deployment configs and run one paid pair.
  Snapshot restore and paid A/B configs have not been implemented yet.
- Keep the remaining MCP server's read-diagnostic counters distinct from native
  reads; they do not automatically observe calls that bypass that server.

## Local evidence and recovery

Base image: `sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d`.
Both disposable fixtures are stopped and retained, not deleted. No source repo,
LLM credentials, scoring rules, or historical job logs were changed.

Ignored local evidence:

- `.runtime/stage1-offline-initial.log`
- `.runtime/stage1-regression.log` (failed combined collection)
- `.runtime/stage1/mcp-regression.log`, `.runtime/stage1/integration.log`
- `.runtime/stage1/fixture-initial-state.json`, `.runtime/stage1/fixture-initial.log`
- `.runtime/stage1/fixture-r2.log`, `.runtime/stage1/fixture-retest-failure.txt`
- `.runtime/stage1/fixture/` (partial dump and test API key; NOT a complete snapshot)

The implementation and this report are saved on an experiment branch. Main keeps
the published plan and the prior baseline; no force-push or history rewrite.
