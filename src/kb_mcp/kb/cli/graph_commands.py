"""Graph knowledge base commands."""

import json
import sys


def cmd_get_node(args):
    """Get a node with all its relations."""
    from ..graph import get_node

    try:
        # Get node by ID or name
        result = get_node(
            id=args.id if hasattr(args, 'id') and args.id else None,
            name=args.name if hasattr(args, 'name') and args.name else None,
            type=args.type if hasattr(args, 'type') and args.type else None,
            include_incoming=not args.no_incoming,
            include_outgoing=not args.no_outgoing,
        )

        if not result:
            if args.id:
                print(f"Node with ID {args.id} not found")
            else:
                print(f"Node with name '{args.name}' not found")
            sys.exit(1)

        if args.json:
            # Convert datetime to string for JSON serialization
            import datetime
            def serialize(obj):
                if isinstance(obj, datetime.datetime):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")
            print(json.dumps(result, indent=2, default=serialize))
        else:
            node = result["node"]
            stats = result["statistics"]

            print(f"Node: {node['name']}")
            print(f"  ID: {node['id']}")
            print(f"  Type: {node['type']}")
            if node['aliases']:
                print(f"  Aliases: {', '.join(node['aliases'])}")
            print(f"  Created: {node['created_time']}")

            print(f"\nStatistics:")
            print(f"  Outgoing relations: {stats['total_outgoing']}")
            print(f"  Incoming relations: {stats['total_incoming']}")
            print(f"  Total relations: {stats['total_relations']}")
            print(f"  Documents: {stats['total_documents']}")

            if result["outgoing_relations"] and not args.no_outgoing:
                print(f"\nOutgoing Relations ({len(result['outgoing_relations'])}):")
                for rel in result["outgoing_relations"]:
                    print(f"  --[{rel['verb']}]--> {rel['target_node']['name']} ({rel['target_node']['type']})")
                    print(f"    Evidence: {rel['evidence_count']}, Confidence: {rel['max_confidence']:.2f}")

            if result["incoming_relations"] and not args.no_incoming:
                print(f"\nIncoming Relations ({len(result['incoming_relations'])}):")
                for rel in result["incoming_relations"]:
                    print(f"  <--[{rel['verb']}]-- {rel['source_node']['name']} ({rel['source_node']['type']})")
                    print(f"    Evidence: {rel['evidence_count']}, Confidence: {rel['max_confidence']:.2f}")

            if result.get("linked_documents"):
                print(f"\nLinked Documents ({len(result['linked_documents'])}):")
                for doc_id in result["linked_documents"][:5]:  # Show first 5
                    print(f"  {doc_id}")
                if len(result["linked_documents"]) > 5:
                    print(f"  ... and {len(result['linked_documents']) - 5} more")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_get_nodes_for_document(args):
    """Get all nodes mentioned in a document."""
    from ..graph import get_nodes_for_document

    try:
        nodes = get_nodes_for_document(args.document_id)

        if not nodes:
            print(f"No nodes found for document {args.document_id}")
            return

        if args.json:
            # Remove ORM objects before serializing
            serializable_nodes = []
            for node in nodes:
                node_copy = node.copy()
                node_copy.pop('node', None)  # Remove ORM object
                serializable_nodes.append(node_copy)
            print(json.dumps(serializable_nodes, indent=2))
        else:
            print(f"Nodes for Document {args.document_id} ({len(nodes)} nodes):")
            print("=" * 80)

            for i, node in enumerate(nodes, 1):
                print(f"\n{i}. {node['name']} ({node['type']})")
                print(f"   ID: {node['id']}")
                print(f"   Mentions: {node['mention_count']}")
                if node['aliases']:
                    print(f"   Aliases: {', '.join(node['aliases'][:3])}")
                    if len(node['aliases']) > 3:
                        print(f"            ... and {len(node['aliases']) - 3} more")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_process_document(args):
    """Extract and process relations from a document."""
    from ..graph import extract_and_process_document

    try:
        print(f"Processing document {args.document_id}...")

        result = extract_and_process_document(args.document_id)

        print(f"\n✓ Extraction complete:")
        print(f"  Relations extracted: {result['relations_extracted']}")
        print(f"  Relations created: {result['relations_created']}")
        print(f"  Relations updated: {result['relations_updated']}")
        print(f"  Errors: {result['relations_errors']}")
        print(f"  Time (extraction): {result['time_extraction']:.2f}s")
        print(f"  Time (processing): {result['time_processing']:.2f}s")
        print(f"  Model: {result['extraction_model']}")
        print(f"  Log ID: {result['log_id']}")

        if result['error_details']:
            print(f"\nError Details:")
            for i, error in enumerate(result['error_details'][:5], 1):
                print(f"  {i}. {error.get('error', 'Unknown error')}")
            if len(result['error_details']) > 5:
                print(f"  ... and {len(result['error_details']) - 5} more")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_extract_all(args):
    """Extract knowledge graph relations from all documents matching filters."""
    from ..graph import extract_all

    try:
        print(f"Extracting graph relations from documents...")
        if args.source_id:
            print(f"  Source ID: {args.source_id}")
        if args.parser_id:
            print(f"  Parser ID: {args.parser_id}")
        if args.force:
            print(f"  Force: Re-processing documents with existing relations")
        if args.limit:
            print(f"  Limit: {args.limit} documents")

        result = extract_all(
            source_id=args.source_id,
            parser_id=args.parser_id,
            force=args.force,
            limit=args.limit,
            batch_size=getattr(args, 'batch_size', None),
        )

        print(f"\nBatch extraction complete:")
        print(f"  Total documents: {result['total_documents']}")
        print(f"  Processed: {result['processed']}")
        print(f"  Errors: {result['errors']}")
        print(f"  Total relations extracted: {result['total_relations_extracted']}")
        print(f"  Total relations created: {result['total_relations_created']}")
        print(f"  Total relations updated: {result['total_relations_updated']}")
        print(f"  Total relation errors: {result['total_relations_errors']}")

        if result['error_details']:
            print(f"\nError Details:")
            for i, error in enumerate(result['error_details'][:5], 1):
                print(f"  {i}. Document {error.get('document_id', 'unknown')}: {error.get('error', 'Unknown error')}")
            if len(result['error_details']) > 5:
                print(f"  ... and {len(result['error_details']) - 5} more")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def setup_commands(subparsers):
    """Set up graph knowledge base commands."""

    # Graph command with subcommands
    graph_parser = subparsers.add_parser("graph", help="Knowledge graph operations")
    graph_subparsers = graph_parser.add_subparsers(dest="graph_command", help="Graph commands")

    # Get node command
    get_node_parser = graph_subparsers.add_parser("get-node", help="Get a node with all its relations")
    get_node_parser.add_argument("--id", help="Node ID (UUID)")
    get_node_parser.add_argument("--name", help="Node name")
    get_node_parser.add_argument("--type", help="Node type (used with --name for filtering)")
    get_node_parser.add_argument("--no-incoming", action="store_true", help="Don't include incoming relations")
    get_node_parser.add_argument("--no-outgoing", action="store_true", help="Don't include outgoing relations")
    get_node_parser.add_argument("--json", action="store_true", help="Output as JSON")
    get_node_parser.set_defaults(func=cmd_get_node)

    # Get nodes for document command
    get_nodes_doc_parser = graph_subparsers.add_parser(
        "get-nodes-for-document",
        help="Get all nodes mentioned in a document"
    )
    get_nodes_doc_parser.add_argument("document_id", help="Document ID (UUID)")
    get_nodes_doc_parser.add_argument("--json", action="store_true", help="Output as JSON")
    get_nodes_doc_parser.set_defaults(func=cmd_get_nodes_for_document)

    # Process document command
    process_doc_parser = graph_subparsers.add_parser(
        "process-document",
        help="Extract and process relations from a single document"
    )
    process_doc_parser.add_argument("document_id", help="Document ID (UUID)")
    process_doc_parser.set_defaults(func=cmd_process_document)

    # Add extract-all to tools subparser (we'll need to modify tools_commands.py)
    # For now, we'll add it as a standalone command under graph too
    extract_all_parser = graph_subparsers.add_parser(
        "extract-all",
        help="Extract relations from all documents matching filters"
    )
    extract_all_parser.add_argument(
        "--source-id",
        help="Filter by source ID"
    )
    extract_all_parser.add_argument(
        "--parser-id",
        help="Filter by parser ID"
    )
    extract_all_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process documents even if they already have relations"
    )
    extract_all_parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of documents to process"
    )
    extract_all_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for parallel processing (default: from config, set to large value like 999999 to disable batching)"
    )
    extract_all_parser.set_defaults(func=cmd_extract_all)
