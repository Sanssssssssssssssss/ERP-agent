# 第一阶段验收记录（2026-09-03）

## 最新授权：功能优先，准备 r3 重跑

用户已允许继续 A/B，并明确暂不以时间和预算作为功能实验的停止条件。r3 将使用同一快照、同一 case 2262、相同模型与 high reasoning，保留原来的 4 个原生读工具范围；两组共同将运行截止设为 86,370 秒、外层设为 86,400 秒（24 小时遗留进程兜底，不是无限）。不改 Agent/MCP 业务逻辑，不改任务或评分，不恢复输出 token/轮数上限；所有异常仍等待测试服务停止。先推送配置，再启动。下面“等待额外预算”的记录保留为上一轮历史状态，不再阻止本轮执行。

## 本轮进展：A 主动中断，B 未启动；修复超时生命周期

用户已授权继续修复普通实现问题，并要求每阶段模型测试及子 agent 复查。目前未晋升阶段一基线，尚未进入阶段二；下面的旧停止记录保留历史事实，不再作为本轮停止规则。

实现检查点 `af8253f0df98d7d4daa973d5850d1cc86d6695ff` 已推送私有 GitHub 实验分支，独立子 agent 已核对代码与真实回执，允许进入首对模型测试（并非阶段晋升）。此前启动被权限审核拦截；用户随后明确允许将 ERP-Bench 合成任务和 Odoo 工具结果发往 `api.commandcode.ai/provider/v1`，用于约定的分阶段 A/B，现恢复执行。两个无模型 fixture 已停止，容器与失败回执保留。

获授权后的首个启动命令因 Windows PATH 含空格导致 Bash 解析失败，尚未创建 job；改用固定 Linux PATH。`stage1-snapshot-ab-20260903-r1` 随后在容器创建前因缺少 `ERP_HARBOR_PROXY` 失败，两组均 **0 次模型调用**，不能当作业务零分。已验证既有 WSL 代理可达授权端点（无认证 GET，HTTP 404），将代理地址补入忽略提交的本地 env，保留 r1 证据，用 r2 运行首对模型测试；模型、数据和预算未变。

`r2` 从推送的 `4fcc5eaa1d72e7b2dcbf745c2a02f262d8a2e38a` 启动。A 的恢复校验通过并产生真实模型请求；运行中只补强离线报告，没有修改被测 Agent/MCP 行为。独立子 agent 随后确认 Harbor 0.22 的取消流程存在风险，故在 **22 分 45.75 秒主动中断 A，并停止其 disposable main；B 没有启动**。A 回执为 `CancelledError`，没有评分器结果，也没有自然结束。容器已由 Harbor 清理，原始任务回执仍保留。没有发生已观测到的超时后写入或评分重叠：本次是依据源码证据提前止损，不能把潜在风险写成已发生的事故。

### 为什么停，以及怎么修

已安装 Harbor 的 `trial/trial.py::_run_agent_phase` 用 `asyncio.wait_for` 取消 Agent；Docker buffered exec 对外层取消没有远端进程终止保证。`trial/single_step.py::_run_agent` 又会捕获超时/非零退出并继续 shared verifier。因此仅有“1,800 秒”的宿主配置，不能保证远端模型/工具已经停止。exec 内层原有 3,600 秒等待也会一起被取消，不是有效后备截止。

最小修复在本仓库共用适配器中，两组同时生效：

- `integration/harbor_agent.py` 使用 GNU `timeout` 包住完整 Python 与 tee 管道。运行阶段预算为 1,770 秒（扣除 MCP 启动等阶段内耗时），到时 TERM，5 秒后仍不退出则 KILL；不新增轮数或输出 token 上限。
- 运行失败或取消时，先等待 Harbor 已有的 `stop_service("main")` 停掉整个测试服务，再返回异常；重复取消也不能跳过这一步。模型请求与 Odoo 工具均停止后，Agent 阶段才结束。
- 失败的 shared trial 不再能执行评分脚本；保留超时/异常和未知分数，不伪装成业务零分或有效基线。正常结束仍保留正常评分路径。Docker 自身停止失败会继续报错，不降级放行。
- 外层仍为 1,800 秒，通常预留 30 秒用于强制退出与容器清理；宿主取消仍可能等待清理完成，不能把它说成整项 job 的硬性墙钟上限。

这属于两组共同的 A0 测试基础设施修复，不是原生移植效果。没有改 installed Harbor、Pi 核心、MCP 业务逻辑或 ERP 评分规则。

### 中断 A 的实际调用和费用证据

任务为 `stage1-snapshot-ab-20260903-r2/2262_easy_26_buy_only_net_30_no__TRRuiBq`，来源与快照指纹保存在该 job 的 `experiment.json`。

- **12 条真实 HTTP 请求记录、12 条 HTTP 200 响应头、11 条完整模型响应**；本次未观察到旧的 `400 Content Exists Risk`。HTTP 200 不代表流完整，最后一条请求的用量未知，不是零。
- 已收到的用量：新输入 **26,112**、缓存输入 **201,600**、输出 **77,162**，已知合计 **304,874** token；其中推理 **73,905** 已包含在输出中。供应商未提供可靠金额，费用不估成零。
- **38 次 MCP、0 次原生读取、44 次已完成 JSON-2 尝试**；7 个业务错误结果，0 个协议错误。没有已确认成功的写入；不借用本次轨迹向未来 B 提供解题线索。
- 中文报告在 `.runtime/reports/index.html`；逐次请求、完整会话、token、工具参数和返回均可查看。报告明确标示未回报用量，不覆盖原始任务日志。

### 修复验收与下一步

最终完整离线回归已通过 **931 项 MCP、20 项集成**，1 项历史 C 组 Node 检查跳过（`.runtime/stage1/check-20260903T141019Z`）。额外的 `integration/deadline_gate.py` 使用真实 Harbor/Docker、无网络和无模型的进程测试，不启动 Odoo：内层超时、外层取消均不进入评分；正常短任务进入同一评分脚本并得到测试分数 1。最终回执为 `.runtime/deadline-gates/run-__54foz9/receipt.json`：

- 内层 2 秒截止加 5 秒 KILL：拒绝 TERM 的子进程最后 tick 在 **7.07 秒**，停服务后 Agent 阶段于 **17.25 秒**结束。
- 外层 2 秒取消：最后 tick 在 **12.08 秒**，Agent 阶段于 **12.15 秒**结束，早于内部 20 秒后备截止。额外约 10 秒是 Docker 的停止宽限期，不能漏计。
- 正常路径 **0.33 秒**结束，成功进入同一评分脚本；失败路径均没有该脚本标记和分数。所有末 tick 均早于 Agent 阶段结束。

这些分数仅验证生命周期，不是 ERP 成绩或 C 组实验。夹具准备期间的任务名称格式、Harbor 禁网模式兼容、入口参数配置错误均已修复，失败目录保留，全部零模型调用。禁网最终用 Docker 原生 `network_mode: none`，不依赖本机不支持的 Harbor egress sidecar。

独立子 agent 已完成最终只读验收：检查点 `15c02dd3324b1981bc9dee363106a8923ab695de` 的修复与上述实际回执一致，未发现阻断问题；其要求补充的时间戳与正常评分检查均已通过。这只放行超时清理修复，不代表业务通过。`configs/stage1-ab.json` 已准备新的 r3 名称，**尚未启动**。由于这会重跑已经消耗过的 A，已向用户申请额外一对 A/B 的预算；未得到新预算前只做离线检查和保存。第一阶段仍须满足自然结束及全部 62 条适用规则，不能凭此次基础设施检查晋升；第二至第七阶段不启动。

### 此前通过的第一阶段前置检查

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
