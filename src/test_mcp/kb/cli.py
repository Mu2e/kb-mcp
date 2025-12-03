#!/usr/bin/env python3
"""CLI tool for knowledge base operations."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


class GroupedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom formatter that groups commands with spacing."""
    
    def _format_action(self, action):
        """Format individual actions, adding spacing for groups."""
        if isinstance(action, argparse._SubParsersAction):
            # Custom formatting for subparsers
            parts = []
            
            # Build a map of command name to help text
            # Get help from choice actions (these are the actions created when adding subparsers)
            cmd_help = {}
            for choice_action in action._choices_actions:
                cmd_name = choice_action.dest
                help_text = getattr(choice_action, 'help', '') or ''
                cmd_help[cmd_name] = help_text
            
            # Find aliases by checking which parsers are the same
            # Aliases point to the same parser object
            parser_to_cmds = {}
            for cmd_name, parser in action._name_parser_map.items():
                parser_id = id(parser)  # Use object id to identify same parser
                if parser_id not in parser_to_cmds:
                    parser_to_cmds[parser_id] = []
                parser_to_cmds[parser_id].append(cmd_name)
            
            # Define groups with their commands in order (only primary names, aliases shown separately)
            command_groups = [
                ("Document Operations", ["add", "get", "embed", "drop"]),
                ("Chunks, Embeddings & Sources", ["source", "chunks", "embedding"]),  # "emb" is an alias, will be shown
                ("Tools & Statistics", ["tools", "stats"]),
            ]
            
            for group_name, command_names in command_groups:
                # Add spacing before each group (except the first)
                if parts:
                    parts.append("")
                
                # Add group header
                parts.append(f"  {group_name}:")
                
                # Add commands in this group
                for cmd_name in command_names:
                    if cmd_name in action._name_parser_map:
                        help_text = cmd_help.get(cmd_name, "")
                        # Find aliases for this command
                        parser = action._name_parser_map[cmd_name]
                        parser_id = id(parser)
                        aliases = [c for c in parser_to_cmds.get(parser_id, []) if c != cmd_name]
                        # Format aliases
                        if aliases:
                            alias_str = f" ({', '.join(aliases)})"
                        else:
                            alias_str = ""
                        parts.append(f"    {cmd_name:<18}{alias_str:<10} {help_text}")
            
            return "\n".join(parts) + "\n"
        
        return super()._format_action(action)

from . import add, add_from_path, add_source, deduplicate, get, get_stats, list_sources, delete_document
from .utils import find_all_duplicates
from .core import Document, Source
from .database import get_db_session
from ..parser import parse

# Import embedding functions (may not be available if dependencies not installed)
try:
    from .embedding import (
        get_chunk_strategies, get_chunks, drop_chunks, get_embedding_names,
        embed_chunk, embed_chunks, chunk_and_embed, get_embeddings, get_embedding_vector,
        drop_embedding, drop_embedding_table
    )
    from .embedding.core import Chunk
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    Chunk = None


def _interactive_dedup_choice(existing_by_id, existing_by_hash, new_doc):
    """Interactive prompt for handling duplicates."""
    print("\n    Duplicates found:")
    
    if existing_by_id:
        print(f"  • Existing document with same source_id+doc_id:")
        print(f"    ID: {existing_by_id.id}")
        print(f"    Source: {existing_by_id.source_id}/{existing_by_id.doc_id}")
        print(f"    Inserted: {existing_by_id.insert_time}")
    
    if existing_by_hash and (not existing_by_id or existing_by_hash.id != existing_by_id.id):
        print(f"  • Existing document with same content_hash:")
        print(f"    ID: {existing_by_hash.id}")
        print(f"    Source: {existing_by_hash.source_id}/{existing_by_hash.doc_id}")
        print(f"    Inserted: {existing_by_hash.insert_time}")
    
    print(f"\n  New document:")
    print(f"    Source: {new_doc.source_id}/{new_doc.doc_id}")
    
    print("\nOptions:")
    print("  0 - Insert anyway (create duplicate)")
    print("  1 - Insert with warning (create duplicate)")
    print("  2 - Update existing by hash (if exists)")
    print("  3 - Update existing by hash with warnings")
    print("  4 - Update existing by source_id+doc_id (if exists), else by hash")
    
    while True:
        try:
            choice = input("\nChoose action [0-4] (default: 2): ").strip()
            if not choice:
                return 3
            level = int(choice)
            if 0 <= level <= 4:
                return level
            print("Invalid choice. Please enter 0-4.")
        except ValueError:
            print("Invalid input. Please enter a number 0-4.")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(1)


def cmd_add(args):
    """Add a document from a file."""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Determine source_id if not provided
    source_id = args.source_id
    if not source_id:
        source_id = "local"
        print(f"Warning: No source_id provided, using '{source_id}'")
        print("  Use --source-id to specify a source")

    # Check if source exists, create if needed
    with get_db_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            print(f"Source '{source_id}' does not exist. Creating it...")
            add_source(source_id=source_id, name=f"Local files ({source_id})")

    # Determine doc_id
    doc_id = args.doc_id
    if not doc_id:
        doc_id = file_path.stem

    # Handle file copying if --copy is specified
    uri = None
    if args.copy:
        # Get DATA_DIR and create local directory
        data_dir = os.getenv("DATA_DIR", "data")
        local_dir = Path(data_dir) / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy file to local directory
        dest_file = local_dir / file_path.name
        # Handle duplicates by adding a number
        counter = 1
        original_dest = dest_file
        while dest_file.exists():
            stem = original_dest.stem
            suffix = original_dest.suffix
            dest_file = local_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        
        shutil.copy2(file_path, dest_file)
        print(f"  Copied file to: {dest_file}")
        
        # Set URI to local://local/filename
        uri = f"local://local/{dest_file.name}"

    # Prepare data dict
    data = {"source_id": source_id}
    if doc_id:
        data["doc_id"] = doc_id
    if uri:
        data["uri"] = uri

    # Determine deduplication level
    dedup_level = args.dedup_level
    
    # If no dedup level specified and not in batch mode, use interactive mode
    interactive = not args.batch
    
    # If no dedup level specified and interactive mode, check for duplicates first
    if dedup_level is None and interactive:
        # Parse the file first to check for duplicates
        try:
            from .base import _find_duplicates
            from .core import Document
            import hashlib
            
            doc_dicts = parse(file_path, data=data)
            if not doc_dicts:
                raise ValueError("No documents extracted from file")
            
            main_doc_dict = doc_dicts[0]
            temp_doc = Document.from_dict(main_doc_dict)
            
            # Compute hash manually
            if temp_doc.text:
                content = temp_doc.text.encode("utf-8")
            elif temp_doc.binary:
                content = temp_doc.binary
            else:
                content = b""
            if content:
                temp_doc.content_hash = hashlib.sha256(content).hexdigest()
            
            with get_db_session() as session:
                existing_by_id, existing_by_hash = _find_duplicates(temp_doc, session)
                
                if existing_by_id or existing_by_hash:
                    dedup_level = _interactive_dedup_choice(
                        existing_by_id, existing_by_hash, temp_doc
                    )
        except Exception as e:
            print(f"Warning: Could not check for duplicates: {e}")
            print("  Proceeding with default deduplication level")

    print(f"Processing file: {file_path}")
    
    # Add document using add_from_path
    try:
        documents = add_from_path(
            file_path,
            data=data,
            parse_image_additional_doc=args.parse_images,
            parse_image_llm_description=args.parse_images_llm,
            dedup_level=dedup_level,
        )
        
        print(f"✓ Added {len(documents)} document(s):")
        for doc in documents:
            print(f"  • {doc.id}")
            print(f"    Source: {doc.source_id}")
            print(f"    Doc ID: {doc.doc_id}")
            print(f"    Type: {doc.source_type}")
            if doc.uri:
                print(f"    URI: {doc.uri}")
            if doc.text:
                print(f"    Text length: {len(doc.text)} characters")
        
        # Automatically chunk and embed if not suppressed
        if args.no_embed:
            print("\n(Skipping chunking and embedding)")
        elif not EMBEDDING_AVAILABLE:
            print("\n(Skipping chunking and embedding - embedding module not available)")
        else:
            try:
                from .embedding import chunk_and_embed
                
                for doc in documents:
                    print(f"\nChunking and embedding document {doc.id}...")
                    chunks = chunk_and_embed(
                        doc,
                        strategy=args.strategy,
                        chunk_config={
                            "chunk_size": args.chunk_size,
                            "chunk_overlap": args.chunk_overlap,
                        } if args.chunk_size or args.chunk_overlap else None,
                        embedding_name=args.embedding_name,
                        provider=args.provider,
                        model=args.model,
                        batch_size=args.batch_size,
                    )
                    print(f"  ✓ Chunked and embedded {len(chunks)} chunk(s)")
            except Exception as e:
                print(f"Warning: Could not chunk and embed documents: {e}")
                import traceback
                traceback.print_exc()
    except Exception as e:
        print(f"Error adding document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_get(args):
    """Get a document."""
    try:
        result = get(
            identifier=args.identifier,
            uuid=args.uuid,
            source_id=args.source_id,
            doc_id=args.doc_id,
            limit=args.limit,
            offset=args.offset,
        )

        if result is None:
            print("No document found")
            sys.exit(1)
        elif isinstance(result, list):
            print(f"Found {len(result)} document(s):")
            for doc in result:
                print(f"  - {doc.id} | {doc.source_id}/{doc.doc_id} | {doc.source_type}")
        else:
            doc = result
            print(f"Document: {doc.id}")
            print(f"  Source: {doc.source_id}")
            print(f"  Doc ID: {doc.doc_id}")
            print(f"  URI: {doc.uri}")
            print(f"  Type: {doc.source_type}")
            if doc.text:
                print(f"  Text: {len(doc.text)} characters")
                if args.show_text:
                    print(f"\n  Content:\n  {doc.text[:500]}...")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_stats(args):
    """Show knowledge base statistics."""
    import json
    
    stats = get_stats()
    
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print("Knowledge Base Statistics")
        print("=" * 40)
        print(f"Total documents: {stats['total_documents']}")
        print(f"Total sources: {stats['total_sources']}")
        print()
        if stats['documents_by_source']:
            print("Documents by source:")
            for item in stats['documents_by_source']:
                print(f"  {item['source_id']}: {item['count']}")


def cmd_source_list(args):
    """List all sources."""
    import json
    
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


def cmd_deduplicate(args):
    """Deduplicate the entire database."""
    print("Scanning database for duplicates...")
    
    duplicates = find_all_duplicates()
    
    if not duplicates:
        print("✓ No duplicates found.")
        return
    
    print(f"\nFound {len(duplicates)} duplicate group(s):")
    
    # Fetch document details for display
    from . import get
    
    total_by_id = sum(len(by_id_dups) for _, by_id_dups, _ in duplicates)
    total_by_hash = sum(len(by_hash_dups) for _, _, by_hash_dups in duplicates)
    
    if total_by_id > 0:
        print(f"\n  {total_by_id} duplicate(s) by source_id+doc_id:")
        count = 0
        for keep_id, by_id_dups, _ in duplicates:
            if by_id_dups:
                keep_doc = get(uuid=keep_id)
                for dup_id in by_id_dups[:10 - count]:  # Show up to 10 total
                    dup_doc = get(uuid=dup_id)
                    if keep_doc and dup_doc:
                        print(f"    • Keep: {keep_id[:8]}... ({keep_doc.source_id}/{keep_doc.doc_id})")
                        print(f"      Duplicate: {dup_id[:8]}... ({dup_doc.source_id}/{dup_doc.doc_id})")
                    count += 1
                    if count >= 10:
                        break
                if count >= 10:
                    break
        if total_by_id > 10:
            print(f"    ... and {total_by_id - 10} more")
    
    if total_by_hash > 0:
        print(f"\n  {total_by_hash} duplicate(s) by content_hash:")
        count = 0
        for keep_id, _, by_hash_dups in duplicates:
            if by_hash_dups:
                keep_doc = get(uuid=keep_id)
                for dup_id in by_hash_dups[:10 - count]:  # Show up to 10 total
                    dup_doc = get(uuid=dup_id)
                    if keep_doc and dup_doc:
                        hash_preview = keep_doc.content_hash[:16] if keep_doc.content_hash else 'N/A'
                        print(f"    • Keep: {keep_id[:8]}... (hash: {hash_preview}...)")
                        print(f"      Duplicate: {dup_id[:8]}...")
                    count += 1
                    if count >= 10:
                        break
                if count >= 10:
                    break
        if total_by_hash > 10:
            print(f"    ... and {total_by_hash - 10} more")
    
    if args.dry_run:
        print("\n[DRY RUN] No changes made. Use --apply to perform deduplication.")
        return
    
    if not args.apply:
        print("\nUse --apply to perform deduplication, or --dry-run to see what would happen.")
        return
    
    # Apply deduplication
    print(f"\nApplying deduplication...")
    print("  (Keeping oldest document in each duplicate group, deleting others)")
    
    result = deduplicate()

    print(f"✓ Deduplication complete:")
    print(f"  Deleted: {result['deleted']} duplicate document(s)")


def cmd_chunks_list(args):
    """List all chunk strategies."""
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available. Install with: pip install tiktoken")
        sys.exit(1)

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available. Install with: pip install tiktoken")
        sys.exit(1)

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available. Install with: pip install tiktoken")
        sys.exit(1)

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
    print(f"✓ Deleted {count} chunk(s){strategy_msg}")


def cmd_chunks_chunk(args):
    """Chunk a document."""
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available. Install with: pip install tiktoken")
        sys.exit(1)

    from .embedding import chunk_document

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
        print(f"✓ Created {len(chunks)} chunk(s)")

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

    try:
        # Get the document
        from .core import Document
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


def cmd_drop(args):
    """Delete a document (top-level command)."""
    try:
        # Get confirmation unless --yes is specified
        if not args.yes:
            confirmation = input(f"Delete document {args.document_id} and all its chunks/embeddings? [y/N]: ")
            if confirmation.lower() not in ['y', 'yes']:
                print("Cancelled.")
                return

        result = delete_document(args.document_id)
        
        print(f"✓ Deleted document {args.document_id}")
        if result.get("chunk_count", 0) > 0:
            print(f"  (Also deleted {result['chunk_count']} chunk(s) and their embeddings)")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error deleting document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_get(args):
    """Get embeddings for a chunk or document."""
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

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
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

    try:
        count = drop_embedding(args.chunk_id, embedding_name=args.embedding_name)
        print(f"Dropped {count} embedding(s) for chunk {args.chunk_id}")
    except Exception as e:
        print(f"Error dropping embeddings: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_embedding_drop_table(args):
    """Drop an embedding table and configuration."""
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available.")
        sys.exit(1)

    try:
        result = drop_embedding_table(args.embedding_name)
        print(f"Dropped embedding table '{result['table_name']}'")
        print(f"  Removed {result['count']} embedding(s)")
    except Exception as e:
        print(f"Error dropping embedding table: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Knowledge base CLI",
        formatter_class=GroupedHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a document from a file")
    add_parser.add_argument("file", help="Path to document file")
    add_parser.add_argument("--source-id", help="Source identifier (default: 'local')")
    add_parser.add_argument("--doc-id", help="Document ID (default: filename without extension)")
    add_parser.add_argument(
        "--dedup-level",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Deduplication level: 0=insert, 1=warn, 2=overwrite hash, 3=overwrite hash+warn (default), 4=overwrite all"
    )
    add_parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: disable interactive prompts, use default deduplication level"
    )
    add_parser.add_argument(
        "--parse-images",
        action="store_true",
        help="Extract images as separate documents"
    )
    add_parser.add_argument(
        "--parse-images-llm",
        action="store_true",
        help="Generate LLM descriptions for images (requires OPENAI_API_KEY)"
    )
    add_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy file to DATA_DIR/local and set URI to local://local/FILENAME"
    )
    add_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip automatic chunking and embedding after adding document"
    )
    add_parser.add_argument(
        "--strategy",
        help="Chunking strategy (default: from CHUNK_STRATEGY env var or 'tokens')"
    )
    add_parser.add_argument(
        "--chunk-size",
        type=int,
        help="Chunk size in tokens (default: 1000)"
    )
    add_parser.add_argument(
        "--chunk-overlap",
        type=int,
        help="Chunk overlap in tokens (default: 200)"
    )
    add_parser.add_argument(
        "--embedding-name",
        help="Embedding name (e.g., 'openai-small')"
    )
    add_parser.add_argument(
        "--provider",
        help="Embedding provider (e.g., 'openai')"
    )
    add_parser.add_argument(
        "--model",
        help="Model name (e.g., 'text-embedding-3-small')"
    )
    add_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for embedding generation"
    )

    # Get command
    get_parser = subparsers.add_parser("get", help="Get a document")
    get_parser.add_argument("identifier", nargs="?", help="Document identifier (UUID or parsed format)")
    get_parser.add_argument("--uuid", help="Document UUID")
    get_parser.add_argument("--source-id", help="Source identifier")
    get_parser.add_argument("--doc-id", help="Document ID")
    get_parser.add_argument("--limit", type=int, help="Maximum number of documents to return")
    get_parser.add_argument("--offset", type=int, help="Number of documents to skip (for pagination)")
    get_parser.add_argument("--show-text", action="store_true", help="Show document text content")

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

    # Drop command (top-level, for documents)
    drop_parser = subparsers.add_parser("drop", help="Delete a document (and all its chunks and embeddings)")
    drop_parser.add_argument("document_id", help="Document ID (UUID)")
    drop_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # Chunks command
    chunks_parser = subparsers.add_parser("chunks", help="Manage document chunks")
    chunks_subparsers = chunks_parser.add_subparsers(dest="chunks_command", help="Chunks commands")

    chunks_list_parser = chunks_subparsers.add_parser("list", help="List chunk strategies")
    chunks_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

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

    # Embedding command
    embedding_parser = subparsers.add_parser("embedding", aliases=["emb"], help="Manage embeddings")
    embedding_subparsers = embedding_parser.add_subparsers(dest="embedding_command", help="Embedding commands")

    embedding_list_parser = embedding_subparsers.add_parser("list", help="List embedding configurations")
    embedding_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    
    embedding_embed_parser = embedding_subparsers.add_parser("embed", help="Embed a specific chunk")
    embedding_embed_parser.add_argument("chunk_id", help="Chunk ID (UUID)")
    embedding_embed_parser.add_argument("--embedding-name", help="Embedding name (e.g., 'openai-small')")
    embedding_embed_parser.add_argument("--provider", help="Embedding provider (e.g., 'openai')")
    embedding_embed_parser.add_argument("--model", help="Model name (e.g., 'text-embedding-3-small')")
    
    embedding_get_parser = embedding_subparsers.add_parser("get", help="Get embeddings for a chunk or document")
    embedding_get_group = embedding_get_parser.add_mutually_exclusive_group(required=True)
    embedding_get_group.add_argument("--chunk-id", help="Chunk ID (UUID)")
    embedding_get_group.add_argument("--document-id", help="Document ID (UUID) - gets embeddings for all chunks")
    embedding_get_parser.add_argument("--embedding-name", help="Specific embedding name (optional)")
    embedding_get_parser.add_argument("--vector", action="store_true", help="Get embedding vector instead of metadata (requires --chunk-id and --embedding-name)")
    embedding_get_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    embedding_drop_parser = embedding_subparsers.add_parser("drop", help="Drop embeddings for a specific chunk")
    embedding_drop_parser.add_argument("chunk_id", help="Chunk ID (UUID)")
    embedding_drop_parser.add_argument("--embedding-name", help="Specific embedding name (optional, drops all if not provided)")
    
    embedding_drop_table_parser = embedding_subparsers.add_parser("drop-table", help="Drop an embedding table and configuration")
    embedding_drop_table_parser.add_argument("embedding_name", help="Embedding name (e.g., 'openai-small')")

    # Source command
    source_parser = subparsers.add_parser("source", help="Manage sources")
    source_subparsers = source_parser.add_subparsers(dest="source_command", help="Source commands")
    
    source_add_parser = source_subparsers.add_parser("add", help="Add or update a source")
    source_add_parser.add_argument("source_id", help="Source identifier")
    source_add_parser.add_argument("--name", help="Source name")
    source_add_parser.add_argument("--description", help="Source description")
    source_add_parser.add_argument("--base-uri", help="Base URI for the source")
    
    source_list_parser = source_subparsers.add_parser("list", help="List all sources")
    source_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    # Tools command (renamed from db-admin)
    tools_parser = subparsers.add_parser("tools", help="Utility tools and functions")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", help="Tools commands")

    dedup_parser = tools_subparsers.add_parser("deduplicate", help="Find and remove duplicate documents")
    dedup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show duplicates without making changes"
    )
    dedup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deduplication (required to make changes)"
    )

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show knowledge base statistics")
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output statistics as JSON"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "add":
        cmd_add(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "embed":
        cmd_embed(args)
    elif args.command == "drop":
        cmd_drop(args)
    elif args.command == "chunks":
        if args.chunks_command == "list":
            cmd_chunks_list(args)
        elif args.chunks_command == "chunk":
            cmd_chunks_chunk(args)
        elif args.chunks_command == "get":
            cmd_chunks_get(args)
        elif args.chunks_command == "drop":
            cmd_chunks_drop(args)
        else:
            chunks_parser.print_help()
            sys.exit(1)
    elif args.command == "embedding" or args.command == "emb":
        if args.embedding_command == "list":
            cmd_embedding_list(args)
        elif args.embedding_command == "embed":
            cmd_embedding_embed(args)
        elif args.embedding_command == "get":
            cmd_embedding_get(args)
        elif args.embedding_command == "drop":
            cmd_embedding_drop(args)
        elif args.embedding_command == "drop-table":
            cmd_embedding_drop_table(args)
        else:
            embedding_parser.print_help()
            sys.exit(1)
    elif args.command == "source":
        if args.source_command == "add":
            try:
                source = add_source(
                    source_id=args.source_id,
                    name=args.name,
                    description=args.description,
                    base_uri=args.base_uri,
                )
                print(f"✓ Added/updated source: {source.id}")
                if source.name:
                    print(f"  Name: {source.name}")
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)
        elif args.source_command == "list":
            cmd_source_list(args)
        else:
            source_parser.print_help()
            sys.exit(1)
    elif args.command == "tools":
        if args.tools_command == "deduplicate":
            cmd_deduplicate(args)
        else:
            tools_parser.print_help()
            sys.exit(1)
    elif args.command == "stats":
        cmd_stats(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

