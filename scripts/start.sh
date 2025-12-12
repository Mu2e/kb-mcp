#!/bin/bash
# Start MCP server and ngrok tunnel in screen sessions

set -e

cd "$(dirname "$0")/.."

# Read PORT from .env file
PORT=$(grep "^PORT=" .env 2>/dev/null | cut -d'=' -f2)
PORT=${PORT:-8443}

# Check if ngrok tunnel already exists
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o 'https://[^"]*\.ngrok-free\.dev' | head -1)

if [ -n "$NGROK_URL" ]; then
    echo "Ngrok tunnel already running: $NGROK_URL"
    echo "Update .env if needed: BASE_URL=$NGROK_URL"
elif ! screen -list | grep -q "mcp-tunnel"; then
    echo "Starting ngrok tunnel in screen session 'mcp-tunnel'..."
    screen -dmS mcp-tunnel ngrok http https://localhost:${PORT}
    sleep 3

    # Get the new ngrok URL
    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o 'https://[^"]*\.ngrok-free\.dev' | head -1)
    if [ -n "$NGROK_URL" ]; then
        echo "Tunnel: $NGROK_URL"
        echo "Update .env if needed: BASE_URL=$NGROK_URL"
    else
        echo "Warning: Could not get ngrok URL. Check logs with: screen -r mcp-tunnel"
    fi
else
    echo "Ngrok screen session exists but tunnel not available. Check logs with: screen -r mcp-tunnel"
fi

# Check if server is already running
if lsof -Pi :${PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Server already running on port ${PORT}"
elif screen -list | grep -q "mcp-server"; then
    echo "Server screen session exists. Attach with: screen -r mcp-server"
else
    echo "Starting MCP server on port ${PORT} in screen session 'mcp-server'..."
    screen -dmS mcp-server kb-mcp
    echo "Server started. Attach with: screen -r mcp-server"
fi

echo ""
echo "To view sessions:"
echo "  screen -r mcp-tunnel  # View ngrok tunnel"
echo "  screen -r mcp-server  # View server logs"
echo "To stop:"
echo "  ./scripts/stop.sh"
