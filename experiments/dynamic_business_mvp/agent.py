"""Harbor entrant for the one-order dynamic business MVP."""

from pathlib import Path
from typing import Any

from integration.harbor_agent import PiAgentMcpBaseline


class MvpAgent(PiAgentMcpBaseline):
    """Reuse the pinned runner and capture a database baseline before the model."""

    async def install(self, environment: Any) -> None:
        await super().install(environment)
        source = Path(__file__).with_name("verifier.py")
        await self.exec_as_agent(
            environment, command="mkdir -p /tmp/pi-odoo-mvp /logs/agent"
        )
        await environment.upload_file(source, "/tmp/pi-odoo-mvp/verifier.py")
        await self.exec_as_agent(
            environment,
            command=(
                "python3 /tmp/pi-odoo-mvp/verifier.py "
                "--baseline /logs/agent/mvp-baseline.json"
            ),
        )
