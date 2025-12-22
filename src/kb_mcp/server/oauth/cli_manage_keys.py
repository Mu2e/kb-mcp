#!/usr/bin/env python3
"""CLI tool for managing MCP API keys."""

import sys

from .api_keys import ApiKeyManager
from ...config import get_api_keys_file


def main():
    """Main CLI entry point."""
    # Get API keys file from environment or use default
    # Use DATA_DIR env var if set (e.g., /data for Cloud Storage mount), otherwise "data/"
    api_keys_file = get_api_keys_file()
    manager = ApiKeyManager(api_keys_file)

    if len(sys.argv) < 2:
        print("Usage: kb-mcp-manage-keys <command> [args]")
        print()
        print("Commands:")
        print("  generate <username> [description]  - Generate a new API key")
        print("  list                               - List all API keys")
        print("  revoke <api_key>                   - Revoke an API key")
        print()
        print(f"API keys file: {api_keys_file}")
        sys.exit(1)

    command = sys.argv[1]

    if command == "generate":
        if len(sys.argv) < 3:
            print("Error: username required")
            print("Usage: kb-mcp-manage-keys generate <username> [description]")
            sys.exit(1)

        username = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else ""

        api_key = manager.create_key(username, description)
        print(f"Generated API key for {username}:")
        print()
        print(f"  {api_key}")
        print()
        print("IMPORTANT: Save this key now - it will not be shown again!")
        print()
        if description:
            print(f"Description: {description}")
        print(f"Username: {username}")

    elif command == "list":
        keys = manager.list_keys()
        if not keys:
            print("No API keys found.")
            print()
            print("Generate a key with:")
            print("  kb-mcp-manage-keys generate <username> [description]")
        else:
            print(f"Found {len(keys)} API key(s):")
            print()
            for api_key, info in keys.items():
                print(f"Key: {api_key[:15]}...{api_key[-8:]}")
                print(f"  Username: {info['username']}")
                print(f"  Created: {info['created']}")
                if info.get("description"):
                    print(f"  Description: {info['description']}")
                print()

    elif command == "revoke":
        if len(sys.argv) < 3:
            print("Error: API key required")
            print("Usage: kb-mcp-manage-keys revoke <api_key>")
            sys.exit(1)

        api_key = sys.argv[2]
        if manager.revoke_key(api_key):
            print(f"API key revoked: {api_key[:15]}...{api_key[-8:]}")
        else:
            print(f"API key not found: {api_key[:15]}...{api_key[-8:]}")
            sys.exit(1)

    else:
        print(f"Unknown command: {command}")
        print()
        print("Available commands: generate, list, revoke")
        sys.exit(1)


if __name__ == "__main__":
    main()

