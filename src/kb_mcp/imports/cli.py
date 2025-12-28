#!/usr/bin/env python3
"""CLI for importing documents from external sources."""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
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
    
    # Parse arguments
    args = parser.parse_args()
    
    # Call the appropriate command function
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

