import shlex

from harbor.environments.base import BaseEnvironment

from agents.pi import Pi


class PiComputerUse(Pi):
    _OUTPUT_FILENAME = "pi-computer-use.txt"
    _COMPUTER_USE_EXTENSION_PATH = "/tmp/pi-mono/extensions/computer-use/index.ts"
    EXTRA_CLI_ARGS = ("--extension", _COMPUTER_USE_EXTENSION_PATH)
    EXTRA_ENV_KEYS = (
        "DISPLAY",
        "PI_COMPUTER_USE_CHROMIUM",
        "PI_COMPUTER_USE_DISPLAY",
        "PI_COMPUTER_USE_DISPLAY_NUMBER",
        "PI_COMPUTER_USE_DISPLAY_WIDTH",
        "PI_COMPUTER_USE_DISPLAY_HEIGHT",
        "PI_COMPUTER_USE_ACTION_DELAY_MS",
        "PI_COMPUTER_USE_CLICK_DELAY_MS",
        "PI_COMPUTER_USE_TYPING_DELAY_MS",
    )
    PRE_RUN_COMMAND = (
        "set -euo pipefail; "
        'if [ -n "${DISPLAY:-}" ] || [ -n "${PI_COMPUTER_USE_DISPLAY:-}" ]; then '
        "PI_COMPUTER_USE_HAS_PROVIDED_DISPLAY=1; "
        "else "
        "PI_COMPUTER_USE_HAS_PROVIDED_DISPLAY=0; "
        "fi; "
        'export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/tmp/pi-computer-use-ms-playwright}"; '
        'export PI_COMPUTER_USE_DISPLAY_NUMBER="${PI_COMPUTER_USE_DISPLAY_NUMBER:-99}"; '
        'export PI_COMPUTER_USE_DISPLAY="${PI_COMPUTER_USE_DISPLAY:-:${PI_COMPUTER_USE_DISPLAY_NUMBER}}"; '
        'export DISPLAY="${DISPLAY:-${PI_COMPUTER_USE_DISPLAY}}"; '
        'export PI_COMPUTER_USE_DISPLAY_WIDTH="${PI_COMPUTER_USE_DISPLAY_WIDTH:-1024}"; '
        'export PI_COMPUTER_USE_DISPLAY_HEIGHT="${PI_COMPUTER_USE_DISPLAY_HEIGHT:-768}"; '
        'export PI_COMPUTER_USE_ACTION_DELAY_MS="${PI_COMPUTER_USE_ACTION_DELAY_MS:-80}"; '
        'export PI_COMPUTER_USE_CLICK_DELAY_MS="${PI_COMPUTER_USE_CLICK_DELAY_MS:-80}"; '
        'export PI_COMPUTER_USE_TYPING_DELAY_MS="${PI_COMPUTER_USE_TYPING_DELAY_MS:-12}"; '
        "if xdotool getdisplaygeometry >/dev/null 2>&1; then "
        'echo "Using existing X11 display $DISPLAY"; '
        'elif [ "$PI_COMPUTER_USE_HAS_PROVIDED_DISPLAY" = "1" ]; then '
        'echo "Provided X11 display is not available: $DISPLAY" >&2; '
        "exit 1; "
        "else "
        'Xvfb "$DISPLAY" -screen 0 "${PI_COMPUTER_USE_DISPLAY_WIDTH}x${PI_COMPUTER_USE_DISPLAY_HEIGHT}x24" >/tmp/pi-computer-use-xvfb.log 2>&1 & '
        "sleep 1; "
        'DISPLAY="$DISPLAY" fluxbox >/tmp/pi-computer-use-fluxbox.log 2>&1 & '
        "sleep 1; "
        "xdotool getdisplaygeometry >/dev/null; "
        "fi; "
        'if [ -z "${PI_COMPUTER_USE_CHROMIUM:-}" ]; then '
        'PI_COMPUTER_USE_CHROMIUM="$(find "$PLAYWRIGHT_BROWSERS_PATH" -path "*/chrome-linux*/chrome" -type f | sort | tail -n 1)"; '
        "fi; "
        'test -x "$PI_COMPUTER_USE_CHROMIUM"; '
        'BROWSER="$PI_COMPUTER_USE_CHROMIUM" DISPLAY="$DISPLAY" "$PI_COMPUTER_USE_CHROMIUM" '
        "--no-sandbox --disable-dev-shm-usage --disable-gpu --no-first-run "
        "--disable-default-apps --disable-background-networking "
        '--user-data-dir=/tmp/pi-computer-use-chromium-profile '
        '--window-size="${PI_COMPUTER_USE_DISPLAY_WIDTH},${PI_COMPUTER_USE_DISPLAY_HEIGHT}" '
        "--new-window http://127.0.0.1:8069/web/login >/tmp/pi-computer-use-chromium.log 2>&1 & "
        "sleep 5; "
        'xdotool search --onlyvisible --class "chrom" >/tmp/pi-computer-use-chromium-windows.txt 2>/dev/null || '
        '{ echo "Chromium did not create a visible X11 window" >&2; cat /tmp/pi-computer-use-chromium.log >&2; exit 1; }; '
    )

    @staticmethod
    def name() -> str:
        return "pi-computer-use"

    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && "
                "apt-get install -y bash xvfb xdotool scrot fluxbox dbus-x11"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                ". ~/.nvm/nvm.sh; "
                "export PLAYWRIGHT_BROWSERS_PATH=/tmp/pi-computer-use-ms-playwright; "
                f"test -f {shlex.quote(self._COMPUTER_USE_EXTENSION_PATH)}; "
                "cd /tmp/pi-mono && "
                "npx playwright install --with-deps chromium && "
                'CHROMIUM_BIN="$(find "$PLAYWRIGHT_BROWSERS_PATH" '
                '-path "*/chrome-linux*/chrome" -type f | sort | tail -n 1)" && '
                'test -x "$CHROMIUM_BIN"'
            ),
        )
