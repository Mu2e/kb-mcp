#!/bin/bash
# Stop MCP server and tunnel

echo "Stopping MCP server and tunnel..."

screen -S mcp-server -X quit 2>/dev/null && echo "Server stopped" || echo "Server not running"
screen -S mcp-tunnel -X quit 2>/dev/null && echo "Tunnel stopped" || echo "Tunnel not running"

echo "Done"
