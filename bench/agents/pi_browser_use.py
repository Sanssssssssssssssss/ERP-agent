import shlex

from harbor.environments.base import BaseEnvironment

from agents.pi import Pi


class PiBrowserUse(Pi):
    _OUTPUT_FILENAME = "pi-browser-use.txt"
    _BROWSER_EXTENSION_PATH = "/tmp/pi-mono/extensions/browser-use/index.ts"
    EXTRA_CLI_ARGS = ("--extension", _BROWSER_EXTENSION_PATH)

    @staticmethod
    def name() -> str:
        return "pi-browser-use"

    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                ". ~/.nvm/nvm.sh; "
                f"test -f {shlex.quote(self._BROWSER_EXTENSION_PATH)}; "
                "cd /tmp/pi-mono && "
                "npx playwright install --with-deps chromium"
            ),
        )
