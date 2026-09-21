"""Historical MCP entrant; never installed in the desktop/backend wheel."""
import asyncio
from pi_mcp import McpToolSet
from erp_harness.app import runner

if __name__ == "__main__":
    runner.McpToolSet = McpToolSet
    asyncio.run(runner.run(runner.arguments()))
