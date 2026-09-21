"""Native Pi control, locally isolated with request receipts and no client output cap."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harbor.agents.installed.pi import Pi

from bench.adapters.harbor_agent import (
    MCP_ODOO_URL,
    MCP_ONLY_POLICY,
    _install_task_mcp,
    _start_task_mcp,
)

PI_VERSION = "0.84.1"
NODE_VERSION = "22.23.2"
NVM_VERSION = "0.40.2"
PI_MCP_EXTENSION_VERSION = "1.5.0"
PI_CONFIG_DIR = "/tmp/harbor-pi-agent"


class NativePiMcpBaseline(Pi):
    _OUTPUT_FILENAME = "pi-mcp-baseline.txt"
    EXTRA_ENV_KEYS = ("OPENAI_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")

    def __init__(self, *args: Any, version: str = PI_VERSION, **kwargs: Any) -> None:
        if version != PI_VERSION:
            raise ValueError(f"NativePiMcpBaseline requires Pi {PI_VERSION}")
        super().__init__(*args, version=version, **kwargs)

    @staticmethod
    def name() -> str:
        return "native-pi-odoo-mcp"

    def render_instruction(self, instruction: str) -> str:
        return f"{super().render_instruction(instruction)}\n\n{MCP_ONLY_POLICY}"

    def _build_custom_models_json(self, access: Any, model_id: str) -> dict | None:
        config = super()._build_custom_models_json(access, model_id)
        if config is not None:
            config["providers"]["harbor-endpoint"]["models"] = [
                {
                    "id": model_id,
                    "reasoning": True,
                    "compat": {"requiresReasoningContentOnAssistantMessages": True},
                }
            ]
        return config

    async def install(self, environment: Any) -> None:
        root = Path(__file__).resolve().parents[2]
        bundles = root / ".runtime" / "runtime-bundles"
        node_archive = bundles / f"node-v{NODE_VERSION}-linux-x64.tar.xz"
        nvm_archive = bundles / f"nvm-v{NVM_VERSION}.tar.gz"
        for archive in (node_archive, nvm_archive):
            if not archive.is_file():
                raise RuntimeError(f"Pinned runtime bundle is missing: {archive}")
            await environment.upload_file(archive, f"/tmp/{archive.name}")
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                'export NVM_DIR="$HOME/.nvm"; '
                f'mkdir -p "$NVM_DIR/versions/node/v{NODE_VERSION}"; '
                f'tar -xzf /tmp/{nvm_archive.name} --strip-components=1 -C "$NVM_DIR"; '
                f'tar -xJf /tmp/{node_archive.name} --strip-components=1 -C "$NVM_DIR/versions/node/v{NODE_VERSION}"; '
                '. "$NVM_DIR/nvm.sh"; '
                f"nvm alias default {NODE_VERSION}; nvm use {NODE_VERSION}; "
                f"npm install -g --ignore-scripts @earendil-works/pi-coding-agent@{PI_VERSION}; pi --version"
            ),
        )
        await _install_task_mcp(self, environment)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; . ~/.nvm/nvm.sh; "
                f"mkdir -p {PI_CONFIG_DIR}/extensions /workspace/.pi; "
                f"PI_CODING_AGENT_DIR={PI_CONFIG_DIR} pi install npm:pi-mcp-extension@{PI_MCP_EXTENSION_VERSION}"
            ),
        )
        await self._upload_config_text(
            environment,
            content=json.dumps(
                {
                    "settings": {"toolPrefix": "mcp"},
                    "mcpServers": {
                        "odoo": {
                            "transport": "streamable-http",
                            "url": MCP_ODOO_URL,
                            "lifecycle": "eager",
                        },
                    },
                }
            ),
            remote_path="/workspace/.pi/mcp.json",
            filename="mcp.json",
        )
        await environment.upload_file(
            root / "bench/adapters/native_pi_extension.mjs",
            f"{PI_CONFIG_DIR}/extensions/baseline.js",
        )

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        await _start_task_mcp(self, environment)
        await super().run(instruction, environment, context)
