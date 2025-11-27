#!/bin/bash
# Stop MCP server and tunnel

cd "$(dirname "$0")/.."

# Read PORT from .env file
PORT=$(grep "^PORT=" .env 2>/dev/null | cut -d'=' -f2)
PORT=${PORT:-8443}

echo "Stopping MCP server and tunnel..."

# Stop screen sessions
screen -S mcp-server -X quit 2>/dev/null && echo "Server screen session stopped" || echo "Server screen session not running"
screen -S mcp-tunnel -X quit 2>/dev/null && echo "Tunnel screen session stopped" || echo "Tunnel screen session not running"

# Kill any processes still listening on the port
if lsof -Pi :${PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Killing process on port ${PORT}..."
    lsof -Pi :${PORT} -sTCP:LISTEN -t | xargs kill -9
    echo "Process killed"
fi

# Kill ngrok processes if still running
if pgrep -x "ngrok" > /dev/null; then
    echo "Killing ngrok processes..."
    pkill -x "ngrok"
    echo "Ngrok killed"
fi

echo "Done"
