"""Embedding-related commands (embed, chunks_*, embedding_*)."""

import json
import sys

from .. import get
from ..database import get_db_session
from ..embedding import (
    get_chunk_strategies, get_chunks, drop_chunks, get_embedding_names,
    embed_chunk, embed_chunks, chunk_and_embed, get_embeddings, get_embedding_vector,
    drop_embedding, chunk_document
)
from ..embedding.db_models import Chunk, ChunkEmbeddingLog, ParsingLog


def cmd_chunks_list(args):
    """List all chunk strategies."""
    strategies = get_chunk_strategies()

    if args.json:
        print(json.dumps(strategies, indent=2))
        return

    if not strategies:
        print("No chunk strategies found.")
        return

    print(f"Chunk Strategies ({len(strategies)}):")
    print()
    for strategy in strategies:
        print(f"  Strategy: {strategy['strategy']}")
        print(f"  Count: {strategy['count']} chunk(s)")
        if strategy['meta']:
            print(f"  Config: {json.dumps(strategy['meta'])}")
        print(f"  Created: {strategy['created_time']}")
        print()


def cmd_chunks_get(args):
    """Get chunks for a document."""
    chunks = get_chunks(
        document_id=args.document_id,
        chunk_strategy=args.strategy,
        limit=args.limit,
        offset=args.offset,
    )

    if args.json:
        print(json.dumps(chunks, indent=2))
        return

    if not chunks:
        print(f"No chunks found for document {args.document_id}")
        if args.strategy:
            print(f"  (with strategy: {args.strategy})")
        return

    print(f"Chunks for document {args.document_id}: ({len(chunks)} chunk(s))")
    if args.strategy:
        print(f"  Strategy filter: {args.strategy}")
    print()

    for chunk in chunks:
        print(f"  Chunk #{chunk['chunk_index']}")
        print(f"    ID: {chunk['id']}")
        print(f"    Strategy: {chunk['chunk_strategy']}")
        print(f"    Token length: {chunk['token_length']}")
        print(f"    Char range: {chunk['char_start_index']}-{chunk['char_end_index']}")
        text_preview = chunk['text'][:100] + "..." if len(chunk['text']) > 100 else chunk['text']
        print(f"    Text: {text_preview}")
        print()


def cmd_chunks_drop(args):
    """Drop chunks for a document."""
# Get confirmation unless --yes is specified
    if not args.yes:
        strategy_msg = f" with strategy '{args.strategy}'" if args.strategy else ""
        confirmation = input(f"Drop all chunks{strategy_msg} for document {args.document_id}? [y/N]: ")
        if confirmation.lower() not in ['y', 'yes']:
            print("Cancelled.")
            return

    count = drop_chunks(
        document_id=args.document_id,
        chunk_strategy=args.strategy,
    )

    strategy_msg = f" with strategy '{args.strategy}'" if args.strategy else ""
    print(f" Deleted {count} chunk(s){strategy_msg}")


def cmd_chunks_chunk(args):
    """Chunk a document."""
# Get the document
    doc = get(uuid=args.document_id)
    if not doc:
        print(f"Error: Document {args.document_id} not found")
        sys.exit(1)

    # Build config from arguments
    config = {}
    if args.chunk_size:
        config['chunk_size'] = args.chunk_size
    if args.chunk_overlap:
        config['chunk_overlap'] = args.chunk_overlap

    print(f"Chunking document {args.document_id} ({doc.source_id}/{doc.doc_id})...")
    print(f"  Strategy: {args.strategy}")
    if config:
        print(f"  Config: {json.dumps(config)}")

    try:
        chunks = chunk_document(doc, strategy=args.strategy, config=config if config else None)
        print(f" Created {len(chunks)} chunk(s)")

        if not args.quiet:
            for i, chunk in enumerate(chunks[:5]):  # Show first 5 chunks
                print(f"\n  Chunk #{i}:")
                print(f"    Tokens: {chunk.token_length}")
                text_preview = chunk.text[:80] + "..." if len(chunk.text) > 80 else chunk.text
                print(f"    Text: {text_preview}")

            if len(chunks) > 5:
                print(f"\n  ... and {len(chunks) - 5} more chunks")
    except Exception as e:
        print(f"Error chunking document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_list(args):
    """List all embedding configurations."""
    configs = get_embedding_names()

    if args.json:
        print(json.dumps(configs, indent=2))
        return

    if not configs:
        print("No embedding configurations found.")
        return

    print(f"Embedding Configurations ({len(configs)}):")
    print()
    for config in configs:
        print(f"  Short name: {config['short_name']}")
        print(f"  Provider: {config['provider']}")
        print(f"  Model: {config['model']}")
        print(f"  Dimension: {config['dimension']}")
        print(f"  Table: {config['table_name']}")
        print(f"  Count: {config.get('count', 0)} embeddings")
        if config['meta']:
            print(f"  Meta: {json.dumps(config['meta'])}")
        print(f"  Created: {config['created_time']}")
        print()


def cmd_embed(args):
    """Chunk and embed a document (top-level command)."""
    try:
        # Get the document
        from ..db_models import Document
        with get_db_session() as session:
            document = session.query(Document).filter(Document.id == args.document_id).first()
            if not document:
                print(f"Error: Document {args.document_id} not found")
                sys.exit(1)

            print(f"Chunking and embedding document {args.document_id}...")

            # Chunk and embed the document
            chunks = chunk_and_embed(
                document,
                strategy=args.strategy,
                chunk_config={
                    "chunk_size": args.chunk_size,
                    "chunk_overlap": args.chunk_overlap,
                } if args.chunk_size or args.chunk_overlap else None,
                embedding_name=args.embedding_name,
                provider=args.provider,
                model=args.model,
                batch_size=args.batch_size,
                session=session
            )

            print(f"Successfully chunked and embedded {len(chunks)} chunk(s)")
    except Exception as e:
        print(f"Error chunking and embedding document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_get(args):
    """Get embeddings for a chunk or document."""
    try:
        if args.chunk_id:
            # Get embeddings for a specific chunk
            if args.vector:
                # Get embedding vector
                if not args.embedding_name:
                    print("Error: --embedding-name is required when using --vector")
                    sys.exit(1)
                embedding = get_embedding_vector(args.chunk_id, args.embedding_name)
                if embedding is None:
                    print(f"No embedding found for chunk {args.chunk_id} with embedding '{args.embedding_name}'")
                    sys.exit(1)

                if args.json:
                    print(json.dumps(embedding, indent=2))
                else:
                    print(f"Embedding vector (dimension: {len(embedding)}):")
                    print(f"  First 10 values: {embedding[:10]}...")
            else:
                # Get embedding metadata
                embeddings = get_embeddings(args.chunk_id, embedding_name=args.embedding_name)
                if not embeddings:
                    print(f"No embeddings found for chunk {args.chunk_id}")
                    sys.exit(1)

                if args.json:
                    print(json.dumps(embeddings, indent=2))
                else:
                    print(f"Embeddings for chunk {args.chunk_id}:")
                    for emb_name, emb_data in embeddings.items():
                        print(f"  {emb_name}:")
                        print(f"    ID: {emb_data['id']}")
        elif args.document_id:
            # Get embeddings for all chunks in a document
            chunks = get_chunks(document_id=args.document_id)
            if not chunks:
                print(f"No chunks found for document {args.document_id}")
                sys.exit(1)

            all_embeddings = {}
            for chunk in chunks:
                chunk_id = chunk['id'] if isinstance(chunk, dict) else chunk.id
                embeddings = get_embeddings(chunk_id, embedding_name=args.embedding_name)
                if embeddings:
                    all_embeddings[chunk_id] = embeddings

            if not all_embeddings:
                print(f"No embeddings found for any chunks in document {args.document_id}")
                sys.exit(1)

            if args.json:
                print(json.dumps(all_embeddings, indent=2))
            else:
                print(f"Embeddings for document {args.document_id} ({len(all_embeddings)} chunks with embeddings):")
                for chunk_id, embeddings in all_embeddings.items():
                    print(f"  Chunk {chunk_id[:8]}...: {len(embeddings)} embedding(s)")
                    for emb_name, emb_data in embeddings.items():
                        print(f"    {emb_name}: ID {emb_data['id']}")
        else:
            print("Error: Either --chunk-id or --document-id must be provided")
            sys.exit(1)
    except Exception as e:
        print(f"Error getting embeddings: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_embed(args):
    """Embed a specific chunk."""
    try:
        with get_db_session() as session:
            chunk = session.query(Chunk).filter(Chunk.id == args.chunk_id).first()
            if not chunk:
                print(f"Error: Chunk {args.chunk_id} not found")
                sys.exit(1)

            print(f"Embedding chunk {args.chunk_id}...")
            embedding = embed_chunk(
                chunk,
                embedding_name=args.embedding_name,
                provider=args.provider,
                model=args.model,
                session=session
            )
            print(f"Successfully embedded chunk (dimension: {len(embedding)})")
    except Exception as e:
        print(f"Error embedding chunk: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_drop(args):
    """Drop embeddings for a specific chunk."""
    try:
        count = drop_embedding(args.chunk_id, embedding_name=args.embedding_name)
        print(f"Dropped {count} embedding(s) for chunk {args.chunk_id}")
    except Exception as e:
        print(f"Error dropping embeddings: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_embed_all(args):
    """Generate embeddings for all chunks that don't have them yet."""
    try:
        from ..tools import embed_all

        print("Generating embeddings for chunks without embeddings")
        if args.source_id:
            print(f"  Filtering by source_id: {args.source_id}")
        if args.chunk_strategy:
            print(f"  Filtering by chunk_strategy: {args.chunk_strategy}")
        if args.embedding_name:
            print(f"  Using embedding config: {args.embedding_name}")
        elif args.provider or args.model:
            print(f"  Using embedding: {args.provider or 'default'}/{args.model or 'default'}")

        result = embed_all(
            source_id=args.source_id,
            chunk_strategy=args.chunk_strategy,
            embedding_name=args.embedding_name,
            provider=args.provider,
            model=args.model,
        )

        print(f"\n  Completed:")
        print(f"  Total chunks found: {result['total_chunks']}")
        print(f"  Successfully embedded: {result['embedded']}")
        if result['errors'] > 0:
            print(f"  Errors: {result['errors']}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def setup_commands(subparsers):
    """Set up embedding-related commands."""
    # Embed command (top-level, for documents)
    embed_parser = subparsers.add_parser("embed", help="Chunk and embed a document")
    embed_parser.add_argument("document_id", help="Document ID (UUID)")
    embed_parser.add_argument("--strategy", help="Chunking strategy (default: from CHUNK_STRATEGY env var or 'tokens')")
    embed_parser.add_argument("--chunk-size", type=int, help="Chunk size in tokens (default: 1000)")
    embed_parser.add_argument("--chunk-overlap", type=int, help="Chunk overlap in tokens (default: 200)")
    embed_parser.add_argument("--embedding-name", help="Embedding name (e.g., 'openai-small')")
    embed_parser.add_argument("--provider", help="Embedding provider (e.g., 'openai')")
    embed_parser.add_argument("--model", help="Model name (e.g., 'text-embedding-3-small')")
    embed_parser.add_argument("--batch-size", type=int, help="Batch size for embedding generation")
    embed_parser.set_defaults(func=cmd_embed)

    # Chunks command
    chunks_parser = subparsers.add_parser("chunks", help="Manage document chunks")
    chunks_subparsers = chunks_parser.add_subparsers(dest="chunks_command", help="Chunks commands")

    chunks_list_parser = chunks_subparsers.add_parser("list", help="List chunk strategies")
    chunks_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    chunks_list_parser.set_defaults(func=cmd_chunks_list)

    chunks_chunk_parser = chunks_subparsers.add_parser("chunk", help="Chunk a document")
    chunks_chunk_parser.add_argument("document_id", help="Document UUID")
    chunks_chunk_parser.add_argument(
        "--strategy",
        default="tokens",
        help="Chunking strategy (default: 'tokens')"
    )
    chunks_chunk_parser.add_argument(
        "--chunk-size",
        type=int,
        help="Chunk size in tokens (default: 1000)"
    )
    chunks_chunk_parser.add_argument(
        "--chunk-overlap",
        type=int,
        help="Chunk overlap in tokens (default: 200)"
    )
    chunks_chunk_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Don't show chunk previews"
    )
    chunks_chunk_parser.set_defaults(func=cmd_chunks_chunk)

    chunks_get_parser = chunks_subparsers.add_parser("get", help="Get chunks for a document")
    chunks_get_parser.add_argument("document_id", help="Document UUID")
    chunks_get_parser.add_argument(
        "--strategy",
        help="Filter by chunk strategy (e.g., 'tokens_1000_200')"
    )
    chunks_get_parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of chunks returned"
    )
    chunks_get_parser.add_argument(
        "--offset",
        type=int,
        help="Offset for pagination"
    )
    chunks_get_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    chunks_get_parser.set_defaults(func=cmd_chunks_get)

    chunks_drop_parser = chunks_subparsers.add_parser("drop", help="Drop chunks for a document")
    chunks_drop_parser.add_argument("document_id", help="Document UUID")
    chunks_drop_parser.add_argument(
        "--strategy",
        help="Only drop chunks with this strategy (default: drop all)"
    )
    chunks_drop_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt"
    )
    chunks_drop_parser.set_defaults(func=cmd_chunks_drop)

    # Embedding command
    embedding_parser = subparsers.add_parser("embedding", aliases=["emb"], help="Manage embeddings")
    embedding_subparsers = embedding_parser.add_subparsers(dest="embedding_command", help="Embedding commands")

    embedding_list_parser = embedding_subparsers.add_parser("list", help="List embedding configurations")
    embedding_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    embedding_list_parser.set_defaults(func=cmd_embedding_list)

    embedding_embed_parser = embedding_subparsers.add_parser("embed", help="Embed a specific chunk")
    embedding_embed_parser.add_argument("chunk_id", help="Chunk ID (UUID)")
    embedding_embed_parser.add_argument("--embedding-name", help="Embedding name (e.g., 'openai-small')")
    embedding_embed_parser.add_argument("--provider", help="Embedding provider (e.g., 'openai')")
    embedding_embed_parser.add_argument("--model", help="Model name (e.g., 'text-embedding-3-small')")
    embedding_embed_parser.set_defaults(func=cmd_embedding_embed)

    embedding_get_parser = embedding_subparsers.add_parser("get", help="Get embeddings for a chunk or document")
    embedding_get_group = embedding_get_parser.add_mutually_exclusive_group(required=True)
    embedding_get_group.add_argument("--chunk-id", help="Chunk ID (UUID)")
    embedding_get_group.add_argument("--document-id", help="Document ID (UUID) - gets embeddings for all chunks")
    embedding_get_parser.add_argument("--embedding-name", help="Specific embedding name (optional)")
    embedding_get_parser.add_argument("--vector", action="store_true", help="Get embedding vector instead of metadata (requires --chunk-id and --embedding-name)")
    embedding_get_parser.add_argument("--json", action="store_true", help="Output as JSON")
    embedding_get_parser.set_defaults(func=cmd_embedding_get)

    embedding_drop_parser = embedding_subparsers.add_parser("drop", help="Drop embeddings for a specific chunk")
    embedding_drop_parser.add_argument("chunk_id", help="Chunk ID (UUID)")
    embedding_drop_parser.add_argument("--embedding-name", help="Specific embedding name (optional, drops all if not provided)")
    embedding_drop_parser.set_defaults(func=cmd_embedding_drop)

    embedding_embed_all_parser = embedding_subparsers.add_parser("embed-all", help="Generate embeddings for chunks that don't have them yet")
    embedding_embed_all_parser.add_argument("--source-id", help="Filter by source identifier")
    embedding_embed_all_parser.add_argument("--chunk-strategy", help="Filter by chunking strategy (e.g., 'tokens', 'slide', 'summary', 'image')")
    embedding_embed_all_parser.add_argument("--embedding-name", help="Embedding config short name (e.g., 'openai-small')")
    embedding_embed_all_parser.add_argument("--provider", help="Embedding provider (e.g., 'openai', 'sentence-transformers')")
    embedding_embed_all_parser.add_argument("--model", help="Embedding model name (e.g., 'text-embedding-3-small')")
    embedding_embed_all_parser.set_defaults(func=cmd_embedding_embed_all)
