#!/usr/bin/env python3
"""CLI tool for document parsing."""

import argparse
import json
import sys
from pathlib import Path

from . import parse


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Parse a document and extract text and metadata"
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to the document file to parse"
    )
    parser.add_argument(
        "--mime-type",
        help="MIME type (optional, will be auto-detected if not provided)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON"
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Output only the extracted text"
    )
    parser.add_argument(
        "--preview",
        type=int,
        metavar="N",
        help="Show only first N characters of text (default: 500)"
    )
    parser.add_argument(
        "--parse-image-additional-doc",
        action="store_true",
        help="Create separate Document objects for extracted images"
    )
    parser.add_argument(
        "--parse-image-llm-description",
        action="store_true",
        help="Generate LLM descriptions for images (uses PARSE_IMAGE_DESCRIPTION_MODEL env var, default: 'gpt-4o-mini')"
    )
    parser.add_argument(
        "--source-id",
        help="Source ID for the document (e.g., 'local', 'mu2e-docdb')"
    )
    parser.add_argument(
        "--doc-id",
        help="Document ID within the source (defaults to filename stem)"
    )

    args = parser.parse_args()

    file_path = args.file

    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        # Prepare data dict for parse()
        data = {
            "source_id": args.source_id if args.source_id else "local",
            "doc_id": args.doc_id if args.doc_id else file_path.stem,
        }
        if args.mime_type:
            data["source_type"] = args.mime_type
        
        # Parse returns List[dict] - get the first (main) document
        doc_dicts = parse(
            file_path,
            data=data,
            parse_image_additional_doc=args.parse_image_additional_doc if args.parse_image_additional_doc else None,
            parse_image_llm_description=args.parse_image_llm_description if args.parse_image_llm_description else None,
        )
        main_doc = doc_dicts[0]
        
        # Convert to old format for CLI output compatibility
        result = {
            'text': main_doc.get('text', ''),
            'mime_type': main_doc.get('source_type', ''),
            'file_path': main_doc.get('uri', '').replace('file://', ''),
            'file_name': main_doc.get('meta', {}).get('filename', file_path.name),
            'file_size': main_doc.get('meta', {}).get('filesize', 0),
            'parser': 'Unknown',  # Parser name not in new format
        }
        
        # Try to get parser name from file extension
        from .utils import get_parser
        try:
            parser = get_parser(file_path, doc_type=result['mime_type'])
            result['parser'] = parser.__class__.__name__
        except Exception:
            pass

        if args.text_only:
            print(result['text'])
        elif args.json:
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output
            print(f"✓ Successfully parsed: {file_path.name}")
            print()
            print("Metadata:")
            print(f"  MIME type: {result['mime_type']}")
            print(f"  Parser: {result['parser']}")
            print(f"  File size: {result['file_size']:,} bytes")
            print(f"  Text length: {len(result['text']):,} characters")
            
            if len(doc_dicts) > 1:
                print(f"  Images: {len(doc_dicts) - 1} image document(s) extracted")
            
            if 'meta' in main_doc and main_doc['meta']:
                print()
                print("Additional metadata:")
                for key, value in main_doc['meta'].items():
                    if key not in ('filename', 'filepath', 'filesize'):  # Already shown
                        print(f"  {key}: {value}")
            
            print()
            print("Text content:")
            print("-" * 60)
            
            preview_len = args.preview if args.preview is not None else 500
            text_preview = result['text'][:preview_len]
            print(text_preview)
            
            if len(result['text']) > preview_len:
                print(f"\n... ({len(result['text']) - preview_len:,} more characters)")
                print(f"\nUse --preview {len(result['text'])} to see full text")

    except NotImplementedError as e:
        print(f"Error: Unsupported file type: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing document: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

