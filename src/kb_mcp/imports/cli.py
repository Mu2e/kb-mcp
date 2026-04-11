#!/usr/bin/env python3
"""CLI for importing documents from external sources."""

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False, extra_loggers: list = None):
    """Setup logging configuration."""
    root_log_level = logging.DEBUG if verbose else logging.WARNING
    
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=root_log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        # Handlers exist, just update the level
        root_logger.setLevel(root_log_level)
    
    # Set import module loggers to INFO level (regardless of verbose flag)
    # This ensures we see INFO messages from the import modules
    imports_base_logger = logging.getLogger("kb_mcp.imports.base")
    imports_base_logger.setLevel(logging.INFO)

    imports_inspire_logger = logging.getLogger("kb_mcp.imports.inspire")
    imports_inspire_logger.setLevel(logging.INFO)

    logging.getLogger("kb_mcp.imports.docdb").setLevel(logging.INFO)

    for name in (extra_loggers or []):
        logging.getLogger(name).setLevel(logging.INFO)


def cmd_inspire(args):
    """Handle inspire source command."""
    from .inspire import InspireSource
    print(args)

    setup_logging(args.verbose)

    with InspireSource(
        source_id=args.source_id,
        delay=args.delay,
        skip_existing=args.skip_existing,
    ) as source:
        documents = source.process_all(
            query=args.query,
            max_results=args.max_results,
            output_dir=args.output_dir,
            auto_embed=not args.no_auto_embed,
            auto_summarize=not args.no_auto_summarize,
        )

        print(f"\n  Successfully processed {len(documents)} document(s)")
        for doc_id in documents[:10]:  # Show first 10 document IDs
            print(f"  - {doc_id}")
        if len(documents) > 10:
            print(f"  ... and {len(documents) - 10} more")


def cmd_docdb(args):
    """Handle docdb source command."""
    from .docdb import DocDBSource
    from datetime import datetime

    setup_logging(args.verbose)

    # Parse optional date filters
    before = datetime.strptime(args.before, "%Y-%m-%d") if getattr(args, "before", None) else None
    after  = datetime.strptime(args.after,  "%Y-%m-%d") if getattr(args, "after",  None) else None

    # Explicit doc IDs (--ids 100 200 300)
    doc_ids = getattr(args, "ids", None) or None

    with DocDBSource(
        source_id=args.source_id,
        delay=args.delay,
        skip_existing=args.skip_existing,
        force_reparse=getattr(args, "force_reparse", False),
    ) as source:
        # Override fetch_items kwargs through process_all by monkey-patching
        # the positional query; pass extra state via the source object
        source._fetch_days    = getattr(args, "days", 30)
        source._fetch_doc_ids = doc_ids
        source._fetch_before  = before
        source._fetch_after   = after

        # Patch fetch_items to forward our extra kwargs
        _orig_fetch = source.fetch_items
        def _fetch_with_extras(query=None, max_results=None):
            return _orig_fetch(
                query=query,
                max_results=max_results,
                days=source._fetch_days,
                doc_ids=source._fetch_doc_ids,
                before=source._fetch_before,
                after=source._fetch_after,
            )
        source.fetch_items = _fetch_with_extras

        documents = source.process_all(
            query=getattr(args, "query", None),
            max_results=args.max_results,
            output_dir=args.output_dir,
            auto_embed=not args.no_auto_embed,
            auto_summarize=not args.no_auto_summarize,
        )

    print(f"\n  Successfully processed {len(documents)} document(s)")
    for doc_id in documents[:10]:
        print(f"  - {doc_id}")
    if len(documents) > 10:
        print(f"  ... and {len(documents) - 10} more")


def main():
    """Main CLI entry point for importing documents."""
    parser = argparse.ArgumentParser(
        description="Import documents from various external sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    subparsers = parser.add_subparsers(dest="source", help="Data source", required=True)
    
    # Inspire source
    inspire_parser = subparsers.add_parser(
        "inspire",
        help="Fetch documents from INSPIRE-HEP",
        description="Fetch PDFs and metadata from INSPIRE-HEP using the API",
    )
    inspire_parser.add_argument(
        "--query",
        "-q",
        default="collaboration:SLD",
        help="Direct search query (e.g., 'collaboration:SLD'). Default: collaboration:SLD",
    )
    inspire_parser.add_argument(
        "--max-results",
        type=int,
        help="Maximum number of results to process (default: all)",
    )
    inspire_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save downloaded PDFs (default: data/sources/inspire-hep)",
    )
    inspire_parser.add_argument(
        "--source-id",
        default="inspire-hep",
        help="Source identifier for knowledge base (default: inspire-hep)",
    )
    inspire_parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5)",
    )
    inspire_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    inspire_parser.add_argument(
        "--no-auto-embed",
        action="store_true",
        help="Disable automatic chunking and embedding after processing",
    )
    inspire_parser.add_argument(
        "--no-auto-summarize",
        action="store_true",
        help="Disable automatic summarization after processing",
    )
    inspire_parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip documents that already exist in the database (check by source_id and doc_id before downloading). "
             "This saves bandwidth by not re-downloading files. Default: False (always download)",
    )
    inspire_parser.set_defaults(func=cmd_inspire)

    # DocDB source
    docdb_parser = subparsers.add_parser(
        "docdb",
        help="Fetch documents from Mu2e DocDB",
        description=(
            "Fetch documents from the Mu2e DocDB (or any FNAL DocDB). "
            "Requires MU2E_DOCDB_USERNAME and MU2E_DOCDB_PASSWORD env vars."
        ),
    )
    docdb_mode = docdb_parser.add_mutually_exclusive_group()
    docdb_mode.add_argument(
        "--ids",
        nargs="+",
        type=int,
        metavar="DOC_ID",
        help="Fetch specific DocDB document IDs (e.g. --ids 51472 51500)",
    )
    docdb_mode.add_argument(
        "--query",
        "-q",
        help="Search text (title/abstract/keywords)",
    )
    docdb_mode.add_argument(
        "--days",
        type=int,
        default=30,
        help="List documents updated in the last N days (default: 30)",
    )
    docdb_parser.add_argument(
        "--before",
        metavar="YYYY-MM-DD",
        help="Upper date bound for search (only with --query)",
    )
    docdb_parser.add_argument(
        "--after",
        metavar="YYYY-MM-DD",
        help="Lower date bound for search (only with --query)",
    )
    docdb_parser.add_argument(
        "--max-results",
        type=int,
        help="Maximum number of documents to process",
    )
    docdb_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save downloaded files (default: temporary directory)",
    )
    docdb_parser.add_argument(
        "--source-id",
        default="mu2e-docdb",
        help="Source identifier for the knowledge base (default: mu2e-docdb)",
    )
    docdb_parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5)",
    )
    docdb_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    docdb_parser.add_argument(
        "--no-auto-embed",
        action="store_true",
        help="Disable automatic chunking and embedding after processing",
    )
    docdb_parser.add_argument(
        "--no-auto-summarize",
        action="store_true",
        help="Disable automatic summarization after processing",
    )
    docdb_parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip documents already present in the database",
    )
    docdb_parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="Re-download and re-parse documents even if they already exist in the database",
    )
    docdb_parser.set_defaults(func=cmd_docdb)

    # Parse arguments
    args = parser.parse_args()
    
    # Call the appropriate command function
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)

    # Dispose DB connection pool so pooled connections are closed, then force
    # exit to avoid hanging from non-daemon PyTorch/SentenceTransformers threads.
    try:
        from kb_mcp.kb.database import get_engine
        get_engine().dispose()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()

