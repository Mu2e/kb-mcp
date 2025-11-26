#!/bin/bash
# Start MCP server and ngrok tunnel in screen sessions

set -e

cd "$(dirname "$0")/.."

# Read PORT from .env file
PORT=$(grep "^PORT=" .env 2>/dev/null | cut -d'=' -f2)
PORT=${PORT:-8443}

# Start ngrok in screen session if not already running
if ! screen -list | grep -q "mcp-tunnel"; then
    echo "Starting ngrok tunnel in screen session 'mcp-tunnel'..."
    screen -dmS mcp-tunnel ngrok http https://localhost:${PORT}
    sleep 2
fi

# Get ngrok URL
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o 'https://[^"]*\.ngrok-free\.app' | head -1)
if [ -n "$NGROK_URL" ]; then
    echo "Tunnel: $NGROK_URL"
    echo "Update .env if needed: BASE_URL=$NGROK_URL"
else
    echo "Could not get ngrok URL. Check logs with: screen -r mcp-tunnel"
fi

# Start server in screen session
if screen -list | grep -q "mcp-server"; then
    echo "Server already running. Attach with: screen -r mcp-server"
else
    echo "Starting MCP server on port ${PORT} in screen session 'mcp-server'..."
    screen -dmS mcp-server test-mcp
    echo "Server started. Attach with: screen -r mcp-server"
fi

echo ""
echo "To view sessions:"
echo "  screen -r mcp-tunnel  # View ngrok tunnel"
echo "  screen -r mcp-server  # View server logs"
echo "To stop:"
echo "  ./scripts/stop.sh"
