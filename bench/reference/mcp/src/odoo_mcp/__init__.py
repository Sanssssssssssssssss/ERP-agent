"""
Odoo MCP Server - MCP Server for Odoo Integration
"""

def __getattr__(name):
    # Core helpers must be importable without constructing the MCP server.
    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(name)

__all__ = ["mcp"]
