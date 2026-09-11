# Pi Agent for Python

[**English**](README.md) | [简体中文](README.zh-CN.md)

**A readable, testable Python implementation of Pi's coding-agent behavior.**

Pi Agent for Python is an unofficial project derived from
[Tau](https://github.com/huggingface/tau) and verified against the observable behavior
of [Pi 0.84.1](https://github.com/badlogic/pi-mono). It is not an official Pi or Tau
release.

[![CI](https://github.com/Sanssssssssssssssss/pi-agent-python/actions/workflows/ci.yml/badge.svg)](https://github.com/Sanssssssssssssssss/pi-agent-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pi-agent-python)](https://pypi.org/project/pi-agent-python/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-python)](https://pypi.org/project/pi-agent-python/)
[![License](https://img.shields.io/github/license/Sanssssssssssssssss/pi-agent-python)](LICENSE)

![Pi Agent for Python TUI](docs/assets/pi-agent-tui.svg)

_Captured from the actual Textual app with deterministic, sanitized fixture data._

## Install and run in three minutes

Pi Agent requires Python 3.12 or newer. Install the release from PyPI:

```bash
uv tool install pi-agent-python
# or: pipx install pi-agent-python
```

Before the first PyPI release, install the current source instead:

```bash
uv tool install git+https://github.com/Sanssssssssssssssss/pi-agent-python.git
```

Set a provider credential without writing it to the repository:

```bash
# macOS or Linux
export OPENAI_API_KEY="your-key"

# PowerShell
$env:OPENAI_API_KEY = "your-key"
```

Start the TUI, or run one prompt headlessly:

```bash
pi-agent --provider openai --model gpt-5.4
pi-agent --provider openai --model gpt-5.4 --print "Summarize this repository"
```

Use `pi-agent --help` for the complete CLI. User data lives in `~/.pi-agent/`;
project resources live in `.pi-agent/`. The project does not read, copy, or migrate
legacy `.tau` data.

## What it is

Use the CLI when you want a terminal coding agent with a Textual TUI, print mode,
JSONL sessions, tools, skills, extensions, model selection, and a Pi-compatible RPC
mode.

Use it as a library when you need only part of the stack:

```python
from pi_ai import OpenAICompatibleProvider
from pi_agent import AgentHarness, AgentHarnessConfig
from pi_coding import CodingSession, CodingSessionConfig
```

The public modules are deliberately separate, so a custom frontend can use the agent
loop without importing the CLI or TUI.

## Architecture

```text
pi_coding  CLI, TUI, sessions, resources, skills, extensions
    ↓
pi_agent   provider-neutral agent loop, tools, events, session primitives
    ↓
pi_ai      provider adapters, streaming, thinking, usage, retries
```

`pi_agent` does not depend on Typer, Rich, Textual, or application configuration
paths. See the [architecture reference](src/pi_coding/data/docs/architecture.md) for
the boundary rules.

## Verified Pi 0.84.1 behavior

The current verified boundary covers:

- the default coding-tool order `read → bash → edit → write`;
- provider-neutral messages, streaming, thinking, usage, and tool-call replay;
- agent-loop lifecycle, steering/follow-up queues, tool execution, cancellation, and a
  single recovery attempt for a provider response with neither visible text nor tool calls;
- JSONL session replay, retry, compaction, branch summaries, resources, and skills;
- extension hooks plus print, JSON, RPC, and Textual frontends where explicitly tested.

Evidence is tracked in the [Pi 0.84.1 parity ledger](dev-notes/pi-0.84.1-parity-ledger.md)
and deterministic contract tests such as
[`test_pi_loop_parity.py`](tests/test_pi_loop_parity.py) and
[`test_pi_session_retry.py`](tests/test_pi_session_retry.py). The ledger also records
one bounded end-to-end chain validation. It is integration evidence, not a performance
ranking.

## Current limitations

- This is behavioral translation, not a drop-in Python port of Pi's complete
  TypeScript UI, protocol, client/server, or telemetry ecosystem.
- The parity target is pinned to Pi 0.84.1. A listed behavior means the documented
  contract is tested; it does not imply complete Pi feature coverage.
- Live provider behavior still depends on provider credentials, models, endpoints,
  quotas, and upstream API changes.
- Project extensions execute Python code with the user's permissions. They require an
  explicit trust decision and are not a sandbox.
- `0.1.0` is not published until both deterministic checks and a sanitized real-provider
  one-shot smoke pass.

## Development and contributing

```bash
uv sync --dev --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

Linux and Windows CI run the full checks. Package smoke tests clean-install both the
wheel and source distribution, import `pi_ai`, `pi_agent`, and `pi_coding`, then run
`pi-agent --version` and `pi-agent --help`.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report
vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not a public
issue.

## Lineage and license

This repository preserves Tau's MIT license and source history. Portions translate Pi
runtime behavior under Pi's MIT license. Exact source commits and notices are recorded
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). See [LICENSE](LICENSE) for the
project license.
