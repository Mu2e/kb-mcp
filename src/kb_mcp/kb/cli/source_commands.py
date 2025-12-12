"""Source management commands."""

import json
import sys

from .. import add_source, list_sources


def cmd_source_list(args):
    """List all sources."""
    sources = list_sources()

    if args.json:
        print(json.dumps(sources, indent=2))
    else:
        if not sources:
            print("No sources found")
            return

        print("Sources")
        print("=" * 40)
        for source in sources:
            print(f"ID: {source['id']}")
            if source['name']:
                print(f"  Name: {source['name']}")
            if source['description']:
                print(f"  Description: {source['description']}")
            if source['base_uri']:
                print(f"  Base URI: {source['base_uri']}")
            if source['created_at']:
                print(f"  Created: {source['created_at']}")
            print()


def cmd_source_add(args):
    """Add or update a source."""
    try:
        source = add_source(
            source_id=args.source_id,
            name=args.name,
            description=args.description,
            base_uri=args.base_uri,
        )
        print(f" Added/updated source: {source.id}")
        if source.name:
            print(f"  Name: {source.name}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def setup_commands(subparsers):
    """Set up source management commands."""
    # Source command
    source_parser = subparsers.add_parser("source", help="Manage sources")
    source_subparsers = source_parser.add_subparsers(dest="source_command", help="Source commands")

    source_add_parser = source_subparsers.add_parser("add", help="Add or update a source")
    source_add_parser.add_argument("source_id", help="Source identifier")
    source_add_parser.add_argument("--name", help="Source name")
    source_add_parser.add_argument("--description", help="Source description")
    source_add_parser.add_argument("--base-uri", help="Base URI for the source")
    source_add_parser.set_defaults(func=cmd_source_add)

    source_list_parser = source_subparsers.add_parser("list", help="List all sources")
    source_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    source_list_parser.set_defaults(func=cmd_source_list)
