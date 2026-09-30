"""Historical transport entrypoint. Never installed in the product wheel."""
import asyncio
from pi_mcp import McpToolSet
from erp_harness.app import runner


def arguments():
    parser = runner.argument_parser()
    parser.add_argument('--mcp-url', default='http://127.0.0.1:8000/mcp')
    for name in ('runtime-mode', 'read-backend', 'action-backend', 'capability-backend'):
        parser.add_argument('--' + name, choices=('mcp', 'native'), default='mcp')
    return parser.parse_args()


if __name__ == '__main__':
    asyncio.run(runner.run(arguments(), source_toolset=lambda args: McpToolSet(args.mcp_url)))
