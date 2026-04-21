"""MCP (Model Context Protocol) server for vidgen.

Exposes vidgen's core capabilities as MCP tools so that any MCP-compatible
client (Claude Desktop, Continue, etc.) can invoke them directly.

Run as a standalone process:

    python -m app.mcp.server

Or integrate with a parent process via stdio transport.
"""
