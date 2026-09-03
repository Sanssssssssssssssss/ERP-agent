# Pi Agent for Python

[English](README.md) | [**简体中文**](README.zh-CN.md)

**一个可读、可测试的 Pi 编程 Agent 行为 Python 实现。**

Pi Agent for Python 是一个非官方项目，派生自
[Tau](https://github.com/huggingface/tau)，并按
[Pi 0.84.1](https://github.com/badlogic/pi-mono) 的可观察行为进行验证。它不是 Pi 或
Tau 的官方发行版。

[![CI](https://github.com/Sanssssssssssssssss/pi-agent-python/actions/workflows/ci.yml/badge.svg)](https://github.com/Sanssssssssssssssss/pi-agent-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pi-agent-python)](https://pypi.org/project/pi-agent-python/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-python)](https://pypi.org/project/pi-agent-python/)
[![License](https://img.shields.io/github/license/Sanssssssssssssssss/pi-agent-python)](LICENSE)

![Pi Agent for Python TUI](docs/assets/pi-agent-tui.svg)

_截图由实际 Textual 应用使用确定性、脱敏的夹具数据生成。_

## 三分钟安装与运行

Pi Agent 需要 Python 3.12 或更高版本。从 PyPI 安装正式发行版：

```bash
uv tool install pi-agent-python
# 或：pipx install pi-agent-python
```

在首次发布到 PyPI 之前，可直接安装当前源码：

```bash
uv tool install git+https://github.com/Sanssssssssssssssss/pi-agent-python.git
```

通过环境变量提供凭据，不要把它写进仓库：

```bash
# macOS 或 Linux
export OPENAI_API_KEY="your-key"

# PowerShell
$env:OPENAI_API_KEY = "your-key"
```

启动 TUI，或无界面执行一次请求：

```bash
pi-agent --provider openai --model gpt-5.4
pi-agent --provider openai --model gpt-5.4 --print "Summarize this repository"
```

运行 `pi-agent --help` 可查看完整 CLI。用户数据保存在 `~/.pi-agent/`，项目资源保存在
`.pi-agent/`。项目不会读取、复制或迁移旧 `.tau` 数据。

## 它是什么

如果你需要带 Textual TUI、print 模式、JSONL 会话、工具、技能、扩展、模型选择和
Pi 兼容 RPC 模式的终端编程 Agent，请直接使用 CLI。

如果只想嵌入部分能力，可以把它作为库使用：

```python
from pi_ai import OpenAICompatibleProvider
from pi_agent import AgentHarness, AgentHarnessConfig
from pi_coding import CodingSession, CodingSessionConfig
```

三个公开模块刻意保持分层，因此自定义前端可以只使用 Agent 循环，不必导入 CLI 或 TUI。

## 架构

```text
pi_coding  CLI、TUI、会话、资源、技能、扩展
    ↓
pi_agent   与 provider 无关的 Agent 循环、工具、事件、会话基础类型
    ↓
pi_ai      provider 适配器、流式响应、思考、用量、重试
```

`pi_agent` 不依赖 Typer、Rich、Textual 或应用配置路径。边界规则见
[架构说明](src/pi_coding/data/docs/architecture.md)。

## 已验证的 Pi 0.84.1 行为

当前验证边界包括：

- 默认编程工具顺序 `read → bash → edit → write`；
- 与 provider 无关的消息、流式响应、思考、用量和工具调用重放；
- Agent 循环生命周期、steering/follow-up 队列、工具执行和取消；
- JSONL 会话重放、重试、压缩、分支摘要、资源和技能；
- 已被明确测试的扩展钩子，以及 print、JSON、RPC 和 Textual 前端行为。

证据记录在 [Pi 0.84.1 parity ledger](dev-notes/pi-0.84.1-parity-ledger.md)，以及
[`test_pi_loop_parity.py`](tests/test_pi_loop_parity.py)、
[`test_pi_session_retry.py`](tests/test_pi_session_retry.py) 等确定性契约测试中。
ledger 还记录了一次有界的完整链路验证；这是集成证据，不是性能排名。

## 当前限制

- 这是行为翻译，不是 Pi 完整 TypeScript UI、协议、客户端／服务端或遥测生态的 Python
  即插即用移植。
- parity 目标固定为 Pi 0.84.1。某项行为被列出，只代表对应契约已有测试，不代表完整覆盖
  Pi 的全部功能。
- 真实 provider 行为仍取决于凭据、模型、端点、配额和上游 API 变化。
- 项目扩展以用户权限执行 Python 代码，需要显式信任，且不构成沙箱。
- 只有确定性检查和一次脱敏的真实 provider one-shot smoke 都通过后，才会发布 `0.1.0`。

## 开发与贡献

```bash
uv sync --dev --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

Linux 和 Windows CI 都执行完整检查。包烟测会分别干净安装 wheel 和源码发行包，导入
`pi_ai`、`pi_agent`、`pi_coding`，再运行 `pi-agent --version` 和
`pi-agent --help`。

提交 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按
[SECURITY.md](SECURITY.md) 使用私密报告流程，不要创建公开 issue。

## 来源与许可证

本仓库保留 Tau 的 MIT 许可证和来源历史；部分代码依据 Pi 的 MIT 许可证翻译其运行时行为。
精确的来源提交和声明记录在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，项目许可证见
[LICENSE](LICENSE)。
