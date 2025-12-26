"""Search-related commands (search, similar, search_logs)."""

import json
import sys

from ..search import search, get_similar
from ..logs import get_search_logs


def cmd_search(args):
    """Search for documents using vector similarity."""
    try:
        # Parse filter if provided
        filter_dict = None
        if args.filter:
            filter_dict = json.loads(args.filter)

        # Build kwargs for simple metadata filters
        kwargs = {}
        if args.metadata:
            for meta_pair in args.metadata:
                if "=" not in meta_pair:
                    print(f"Error: Metadata filter must be in format 'key=value', got: {meta_pair}")
                    sys.exit(1)
                key, value = meta_pair.split("=", 1)
                kwargs[key] = value

        # Perform search
        result = search(
            query=args.query,
            embedding_name=args.embedding_name,
            max_results=args.max_results,
            source_id=args.source_id,
            doc_type=args.doc_type,
            chunking_strategy=args.chunking_strategy,
            filter=filter_dict,
            **kwargs
        )

        # Display results
        print(f"\nSearch Results ({result['metadata']['total_results']} documents):")
        print(f"Query: {result['metadata']['query']}")
        print(f"Embedding: {result['metadata']['embedding_name']}")
        print(f"Total time: {result['metadata']['time_search_total']:.3f}s")
        if 'time_embedding' in result['metadata']:
            print(f"  - Embedding: {result['metadata']['time_embedding']:.3f}s")
        if 'time_deduplication' in result['metadata']:
            print(f"  - Deduplication: {result['metadata']['time_deduplication']:.3f}s")
        print()

        for i, doc_result in enumerate(result['results'], 1):
            doc = doc_result['document']
            print(f"{i}. Document: {doc.id}")
            print(f"   Source: {doc.source_id}/{doc.doc_id}")
            if doc.doc_type:
                print(f"   Type: {doc.doc_type}")
            if doc_result['chunks']:
                first_chunk = doc_result['chunks'][0]
                similarity = doc_result.get('best_similarity') or first_chunk.get('similarity')
                score = doc_result.get('best_score') or first_chunk.get('score')
                if similarity:
                    print(f"   Best similarity: {similarity:.4f}")
                elif score:
                    print(f"   Best score: {score:.4f}")
            print(f"   Matching chunks: {len(doc_result['chunks'])}")

            # Show top chunks
            if doc_result['chunks']:
                print("   Top chunks:")
                for chunk in doc_result['chunks'][:3]:  # Show top 3
                    print(f"     - Chunk #{chunk.get('chunk_index', '?')}: similarity={chunk['similarity']:.4f}")
                if len(doc_result['chunks']) > 3:
                    print(f"     ... and {len(doc_result['chunks']) - 3} more")
            print()

        if not result['results']:
            print("No results found.")

    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in filter: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during search: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_similar(args):
    """Find documents similar to a given chunk or document."""
    try:
        # Parse filter if provided
        filter_dict = None
        if args.filter:
            filter_dict = json.loads(args.filter)

        # Build kwargs for simple metadata filters
        kwargs = {}
        if args.metadata:
            for meta_pair in args.metadata:
                if "=" not in meta_pair:
                    print(f"Error: Metadata filter must be in format 'key=value', got: {meta_pair}")
                    sys.exit(1)
                key, value = meta_pair.split("=", 1)
                kwargs[key] = value

        # Perform similarity search
        result = get_similar(
            chunk_id=args.chunk_id,
            document_id=args.document_id,
            embedding_name=args.embedding_name,
            max_results=args.max_results,
            source_id=args.source_id,
            doc_type=args.doc_type,
            chunking_strategy=args.chunking_strategy,
            filter=filter_dict,
            **kwargs
        )

        # Display results
        print(f"\nSimilar Documents ({result['metadata']['total_results']} documents):")
        if args.chunk_id:
            print(f"Source: Chunk {args.chunk_id}")
        elif args.document_id:
            print(f"Source: Document {args.document_id}")
        print(f"Embedding: {result['metadata']['embedding_name']}")
        print(f"Total time: {result['metadata']['time_search_total']:.3f}s")
        if 'time_embedding' in result['metadata']:
            print(f"  - Embedding: {result['metadata']['time_embedding']:.3f}s")
        if 'time_deduplication' in result['metadata']:
            print(f"  - Deduplication: {result['metadata']['time_deduplication']:.3f}s")
        print()

        for i, doc_result in enumerate(result['results'], 1):
            doc = doc_result['document']
            print(f"{i}. Document: {doc.id}")
            print(f"   Source: {doc.source_id}/{doc.doc_id}")
            if doc.doc_type:
                print(f"   Type: {doc.doc_type}")
            if doc_result['chunks']:
                first_chunk = doc_result['chunks'][0]
                similarity = doc_result.get('best_similarity') or first_chunk.get('similarity')
                score = doc_result.get('best_score') or first_chunk.get('score')
                if similarity:
                    print(f"   Best similarity: {similarity:.4f}")
                elif score:
                    print(f"   Best score: {score:.4f}")
            print(f"   Matching chunks: {len(doc_result['chunks'])}")

            # Show top chunks
            if doc_result['chunks']:
                print("   Top chunks:")
                for chunk in doc_result['chunks'][:3]:  # Show top 3
                    print(f"     - Chunk #{chunk.get('chunk_index', '?')}: similarity={chunk['similarity']:.4f}")
                if len(doc_result['chunks']) > 3:
                    print(f"     ... and {len(doc_result['chunks']) - 3} more")
            print()

        if not result['results']:
            print("No similar documents found.")

    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in filter: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during similarity search: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_search_logs(args):
    """List recent search logs."""
    try:
        logs = get_search_logs(
            limit=args.limit,
            offset=args.offset,
            query=args.query,
            embedding_name=args.embedding_name,
        )

        if args.json:
            # JSON output
            print(json.dumps(logs, indent=2, default=str))
        else:
            # Human-readable output
            if not logs:
                print("No search logs found.")
                return

            print(f"\nRecent Search Logs (showing {len(logs)}):")
            print("=" * 80)

            for i, log in enumerate(logs, 1):
                print(f"\n{i}. {log['query'][:60]}{'...' if len(log['query']) > 60 else ''}")
                print(f"   ID: {log['id']}")
                print(f"   Time: {log['created_time']}")
                print(f"   Embedding: {log['embedding_name'] or 'default'}")
                print(f"   Results: {log['total_results']} documents")
                if log['best_similarity'] is not None:
                    print(f"   Best similarity: {log['best_similarity']:.4f}")
                if log['time_search_total']:
                    print(f"   Total time: {log['time_search_total']:.3f}s")

                # Show filters if present
                filters = []
                if log['source_id']:
                    filters.append(f"source={log['source_id']}")
                if log['doc_type']:
                    filters.append(f"type={log['doc_type']}")
                if log['chunking_strategy']:
                    filters.append(f"strategy={log['chunking_strategy']}")
                if log['filter_params']:
                    filters.append("filter=...")
                if log['metadata_filters']:
                    filters.append("metadata=...")
                if filters:
                    print(f"   Filters: {', '.join(filters)}")

                # Show result document IDs
                if log['results'] and args.verbose:
                    print(f"   Documents: {', '.join([r['document_id'][:8] + '...' for r in log['results'][:5]])}")
                    if len(log['results']) > 5:
                        print(f"   ... and {len(log['results']) - 5} more")

            print("\n" + "=" * 80)

    except Exception as e:
        print(f"Error listing search logs: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def setup_commands(subparsers):
    """Set up search-related commands."""
    # Search command (top-level)
    search_parser = subparsers.add_parser("search", help="Search for documents using vector similarity")
    search_parser.add_argument("query", help="Search query text")
    search_parser.add_argument("--embedding-name", help="Embedding model to use (e.g., 'openai-small')")
    search_parser.add_argument("--max-results", type=int, default=10, help="Maximum number of results (default: 10)")
    search_parser.add_argument("--source-id", help="Filter by source ID")
    search_parser.add_argument("--doc-type", help="Filter by document type")
    search_parser.add_argument("--chunking-strategy", help="Filter by chunking strategy (e.g., 'tokens', 'slide')")
    search_parser.add_argument("--filter", help="Elasticsearch-style filter JSON (e.g., '{\"term\": {\"author\": \"John\"}}')")
    search_parser.add_argument("--metadata", action="append", help="Simple metadata filter (key=value, can be used multiple times)")
    search_parser.set_defaults(func=cmd_search)

    # Similar command
    similar_parser = subparsers.add_parser("similar", help="Find documents similar to a chunk or document")
    similar_group = similar_parser.add_mutually_exclusive_group(required=True)
    similar_group.add_argument("--chunk-id", help="Find documents similar to this chunk")
    similar_group.add_argument("--document-id", help="Find documents similar to this document (searches all chunks)")
    similar_parser.add_argument("--embedding-name", help="Embedding model to use (e.g., 'openai-small')")
    similar_parser.add_argument("--max-results", type=int, default=5, help="Maximum number of results (default: 5)")
    similar_parser.add_argument("--source-id", help="Filter by source ID")
    similar_parser.add_argument("--doc-type", help="Filter by document type")
    similar_parser.add_argument("--chunking-strategy", help="Filter by chunking strategy (e.g., 'tokens', 'slide')")
    similar_parser.add_argument("--filter", help="Elasticsearch-style filter JSON (e.g., '{\"term\": {\"author\": \"John\"}}')")
    similar_parser.add_argument("--metadata", action="append", help="Simple metadata filter (key=value, can be used multiple times)")
    similar_parser.set_defaults(func=cmd_similar)
