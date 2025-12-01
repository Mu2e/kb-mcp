#!/usr/bin/env python3
"""Example usage of the document parsers module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from test_mcp.parser import parse


def main():
    """Example of using the parse() function."""
    
    # Example 1: Parse a document (auto-detect MIME type)
    if len(sys.argv) > 1:
        file_path = Path(sys.argv[1])
    else:
        print("Usage: python parser_example.py <document_path>")
        print("\nExample:")
        print("  python parser_example.py document.pdf")
        sys.exit(1)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    
    print(f"Parsing: {file_path}")
    print("-" * 60)
    
    try:
        result = parse(file_path)
        
        print(f"✓ Successfully parsed document")
        print(f"\nResults:")
        print(f"  MIME type: {result['mime_type']}")
        print(f"  Parser: {result['parser']}")
        print(f"  File size: {result['file_size']:,} bytes")
        print(f"  Text length: {len(result['text']):,} characters")
        print(f"\nText preview (first 500 chars):")
        print("-" * 60)
        print(result['text'][:500])
        if len(result['text']) > 500:
            print("...")
        
        # Show metadata if available
        if 'metadata' in result:
            print(f"\nAdditional metadata:")
            for key, value in result['metadata'].items():
                print(f"  {key}: {value}")
    
    except NotImplementedError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

