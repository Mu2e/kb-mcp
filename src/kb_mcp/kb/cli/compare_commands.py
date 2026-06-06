"""Commands for comparing parser outputs on the same raw documents."""

import json
import sys


def cmd_compare_run(args):
    """Run pass-1 LLM comparison for one raw document or all in a source."""
    from ..compare import compare_raw_document, compare_source

    parsers = args.parsers if args.parsers else None
    model = args.model if args.model else None
    force = args.force

    if args.source_id:
        print(f"Comparing parsers for all raw documents in source: {args.source_id}")
        if parsers:
            print(f"  Restricting to parsers: {', '.join(parsers)}")
        if model:
            print(f"  Using model: {model}")
        if force:
            print("  Force re-run enabled")

        result = compare_source(
            source_id=args.source_id,
            parsers=parsers,
            model=model,
            force=force,
            limit=args.limit,
            workers=args.workers,
        )

        print(f"\n  Completed:")
        print(f"  Total raw documents: {result['total_raw_documents']}")
        print(f"  Compared:            {result['compared']}")
        print(f"  Skipped:             {result['skipped']}")
        if result["errors"] > 0:
            print(f"  Errors:              {result['errors']}")

    elif args.raw_document_id:
        print(f"Comparing parsers for raw document: {args.raw_document_id}")
        if parsers:
            print(f"  Restricting to parsers: {', '.join(parsers)}")
        if model:
            print(f"  Using model: {model}")

        result = compare_raw_document(
            raw_document_id=args.raw_document_id,
            parsers=parsers,
            model=model,
            force=force,
        )

        if result.get("error"):
            print(f"\n  Error: {result['error']}")
            sys.exit(1)
        elif result.get("skipped"):
            print(f"\n  Skipped: {result.get('reason', 'unknown reason')}")
            if result.get("parsers_found"):
                print(f"  Parsers found: {result['parsers_found']}")
        else:
            print(f"\n  Comparison stored: {result['comparison_id']}")
            print(f"  Parsers compared: {', '.join(result['parsers_compared'])}")
            print(f"  Time: {result['elapsed_seconds']}s")
    else:
        print("Error: provide --raw-document-id or --source-id", file=sys.stderr)
        sys.exit(1)


def cmd_compare_categorize(args):
    """Run pass-2 categorization over all stored comparisons for a source."""
    from ..compare import categorize_comparisons

    model = args.model if args.model else None
    prompt_extra = args.prompt_extra if args.prompt_extra else None
    title = args.title if args.title else None

    print(f"Running pass-2 categorization for source: {args.source_id}")
    if model:
        print(f"  Using model: {model}")
    if prompt_extra:
        print(f"  Extra focus: {prompt_extra}")
    if title:
        print(f"  Title: {title}")

    result = categorize_comparisons(
        source_id=args.source_id,
        model=model,
        prompt_extra=prompt_extra,
        title=title,
    )

    if result.get("error"):
        print(f"\n  Error: {result['error']}")
        sys.exit(1)

    print(f"\n  Stored as: {result['categories_id']}")
    if result.get("title"):
        print(f"  Title: {result['title']}")
    print(f"  Comparisons read: {result['comparisons_read']}")
    print(f"  Model: {result['model']}")
    print(f"  Time: {result.get('elapsed_seconds', '?')}s")
    print("\n--- Categories ---")
    print(result["categories_text"])


def cmd_compare_categories_list(args):
    """List stored categorization runs."""
    from ..compare import list_categories

    rows = list_categories(source_id=args.source_id if args.source_id else None)

    if not rows:
        print("No categorization runs found.")
        return

    if args.json:
        for r in rows:
            if r.get("created_time"):
                r["created_time"] = str(r["created_time"])
        print(json.dumps(rows, indent=2))
        return

    print(f"{'ID':<36}  {'Source':<20}  {'N':>4}  {'Model':<30}  Created")
    print("-" * 120)
    for r in rows:
        created = str(r["created_time"] or "")[:19]
        extra = f"  [{r['prompt_extra'][:30]}...]" if r.get("prompt_extra") else ""
        print(f"{r['id']:<36}  {r['source_id']:<20}  {r['num_comparisons']:>4}  {(r['model'] or ''):.<30}  {created}{extra}")


def cmd_compare_export(args):
    """Export all comparisons for a source as markdown."""
    from ..compare import export_comparisons

    print(f"Exporting comparisons for source: {args.source_id}", file=sys.stderr)
    md = export_comparisons(source_id=args.source_id)

    if args.output:
        with open(args.output, "w") as f:
            f.write(md)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(md)


def cmd_compare_list(args):
    """List stored comparisons."""
    from ..compare import list_comparisons

    source_id = args.source_id if args.source_id else None
    rows = list_comparisons(source_id=source_id)

    if not rows:
        print("No comparisons found.")
        return

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    print(f"{'ID':<36}  {'Raw Doc ID':<36}  {'Parsers':<40}  Cat?  Created")
    print("-" * 140)
    for r in rows:
        parsers = ", ".join(r["parser_ids"] or [])
        cat = "yes" if r["has_categories"] else "no"
        created = (r["created_time"] or "")[:19]
        print(f"{r['id']:<36}  {r['raw_document_id']:<36}  {parsers:<40}  {cat:<4}  {created}")


def cmd_compare_get(args):
    """Show the stored comparison for a raw document."""
    from ..compare import get_comparison

    cmp = get_comparison(args.raw_document_id)
    if not cmp:
        print(f"No comparison found for raw document {args.raw_document_id}")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "id": cmp.id,
            "raw_document_id": cmp.raw_document_id,
            "parser_ids": cmp.parser_ids,
            "comparison": cmp.comparison,
            "categories": cmp.categories,
            "model": cmp.model,
            "created_time": cmp.created_time.isoformat() if cmp.created_time else None,
            "meta": cmp.meta,
        }, indent=2))
        return

    print(f"Comparison: {cmp.id}")
    print(f"  Raw document: {cmp.raw_document_id}")
    print(f"  Parsers: {', '.join(cmp.parser_ids or [])}")
    print(f"  Model: {cmp.model}")
    if cmp.created_time:
        print(f"  Created: {cmp.created_time}")
    print()
    print("--- Comparison ---")
    print(cmp.comparison or "(none)")
    if cmp.categories:
        print()
        print("--- Categories ---")
        cats = cmp.categories
        if isinstance(cats, dict) and "text" in cats:
            print(cats["text"])
        else:
            print(json.dumps(cats, indent=2))


def setup_commands(subparsers):
    """Set up compare subcommands."""
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare parser outputs for the same raw document(s)",
    )
    compare_sub = compare_parser.add_subparsers(dest="compare_command", help="Compare commands")

    # compare run
    run_parser = compare_sub.add_parser(
        "run",
        help="Run LLM comparison for a raw document or all docs in a source (pass 1)",
    )
    target = run_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--raw-document-id", dest="raw_document_id", help="UUID of a single raw document")
    target.add_argument("--source-id", dest="source_id", help="Source ID to compare all raw documents in")
    run_parser.add_argument(
        "--parsers",
        nargs="+",
        metavar="PARSER",
        help="Restrict comparison to these parser names (default: all available)",
    )
    run_parser.add_argument("--model", help="LLM model to use (overrides SUMMARY_MODEL env var)")
    run_parser.add_argument("--force", action="store_true", help="Re-run even if comparison already exists")
    run_parser.add_argument("--limit", type=int, metavar="N", help="Stop after N raw documents (source mode only)")
    run_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Number of parallel LLM calls (source mode only, default: 1)")
    run_parser.set_defaults(func=cmd_compare_run)

    # compare categorize
    cat_parser = compare_sub.add_parser(
        "categorize",
        help="Synthesize categories across stored comparisons for a source (pass 2)",
    )
    cat_parser.add_argument("--source-id", dest="source_id", required=True, help="Source ID to categorize")
    cat_parser.add_argument("--model", help="LLM model to use")
    cat_parser.add_argument(
        "--prompt-extra",
        dest="prompt_extra",
        help="Extra instructions appended to the base prompt (e.g. 'Focus on equation handling')",
    )
    cat_parser.add_argument(
        "--title",
        dest="title",
        help="Title for this run (default: auto-generated from the output)",
    )
    cat_parser.set_defaults(func=cmd_compare_categorize)

    # compare categories-list
    cat_list_parser = compare_sub.add_parser("categories-list", help="List stored categorization runs")
    cat_list_parser.add_argument("--source-id", dest="source_id", help="Filter by source ID")
    cat_list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    cat_list_parser.set_defaults(func=cmd_compare_categories_list)

    # compare list
    list_parser = compare_sub.add_parser("list", help="List stored per-document comparisons")
    list_parser.add_argument("--source-id", dest="source_id", help="Filter by source ID")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.set_defaults(func=cmd_compare_list)

    # compare get
    get_parser = compare_sub.add_parser("get", help="Show the comparison for a raw document")
    get_parser.add_argument("raw_document_id", help="UUID of the raw document")
    get_parser.add_argument("--json", action="store_true", help="Output as JSON")
    get_parser.set_defaults(func=cmd_compare_get)

    # compare export
    export_parser = compare_sub.add_parser(
        "export",
        help="Export all comparisons for a source as a markdown document",
    )
    export_parser.add_argument("--source-id", dest="source_id", required=True, help="Source ID to export")
    export_parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    export_parser.set_defaults(func=cmd_compare_export)
