"""Evaluation and benchmarking commands."""

import sys

from ..database import get_db_session
from ..eval import (
    generate_questions_from_documents,
    generate_questions_from_source,
    add_audit,
    audit_question,
    get_unaudited_questions,
    eval as run_eval,
    get_summary_stats,
)
from ..eval.db_models import get_eval_generation, get_eval_questions, get_eval_run
from ...config import get_eval_config


def cmd_eval_generate(args):
    """Generate evaluation questions from documents."""
    try:
        num_questions = getattr(args, 'num_questions', 1) or 1
        document_ids = list(args.doc_id or [])
        if args.same_documents_as:
            # Reuse another generation's documents, so two generators (or
            # strategies) are compared on exactly the same material.
            with get_db_session() as session:
                questions = get_eval_questions(generation_id=args.same_documents_as, session=session)
                document_ids += [q.source_document_id for q in questions if q.source_document_id]
            document_ids = list(dict.fromkeys(document_ids))  # dedupe, keep order
            if not document_ids:
                print(f"Error: generation {args.same_documents_as} has no questions with a source document")
                sys.exit(1)

        if document_ids:
            result = generate_questions_from_documents(
                document_ids=document_ids,
                num_questions_per_doc=num_questions,
                generation_method=args.strategy,
                model=args.model,
                name=args.name,
            )
        elif args.source_id:
            # Use generate_questions_from_source when source_id is provided
            # Convert 0 to None to process all documents
            num_docs = None if args.num_documents == 0 else args.num_documents
            result = generate_questions_from_source(
                source_id=args.source_id,
                num_documents=num_docs,
                num_questions_per_doc=num_questions,
                generation_method=args.strategy,
                model=args.model,
                name=args.name,
            )
        else:
            print("Error: one of --source-id, --doc-id or --same-documents-as is required")
            print("  Example: kb eval generate --source-id inspire-hep")
            sys.exit(1)

        print(f"Generated {result['num_questions_generated']} questions")
        print(f"  Generation ID: {result['generation_id']}")
        print(f"  Documents processed: {result['num_documents_processed']}")
        print(f"  Total time: {result['total_time_seconds']:.1f}s")

    except Exception as e:
        print(f"Error generating questions: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_eval_audit(args):
    """Audit evaluation questions."""
    try:
        # Get unaudited questions (convert 0 to None for unlimited)
        limit = None if args.limit == 0 else args.limit
        # For LLM auditing, filter for questions without llm_judge audits
        # For human auditing, filter for questions without any audits
        audit_type = "llm_judge" if args.llm else None
        questions = get_unaudited_questions(
            generation_id=args.generation_id,
            audit_type=audit_type,
            limit=limit,
        )

        if not questions:
            if args.generation_id:
                print(f"No unaudited questions found for generation {args.generation_id}.")
                print("  All questions may have already been audited, or the generation has no questions.")
            else:
                print("No unaudited questions found.")
            return

        print(f"Found {len(questions)} unaudited questions\n")

        if args.llm:
            # Automated LLM auditing
            print("Running automated LLM audit...\n")
            from tqdm import tqdm
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            workers = getattr(args, "workers", 1)
            print_lock = threading.Lock()

            def _audit(question):
                return question, audit_question(question_id=question.id, model=args.model)

            with tqdm(total=len(questions), desc="Auditing questions", unit="question") as pbar:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(_audit, q): q for q in questions}
                    for future in as_completed(futures):
                        try:
                            question, audit = future.result()
                            status = "✓ Valid" if audit.is_valid else "✗ Invalid"
                            with print_lock:
                                tqdm.write(f"{status}: {question.id[:8]}... - {audit.comments[:80] if audit.comments else 'No comments'}")
                        except Exception as e:
                            q = futures[future]
                            with print_lock:
                                tqdm.write(f"Error auditing {q.id}: {e}")
                        finally:
                            pbar.update(1)

            print(f"\nCompleted auditing {len(questions)} questions")
        else:
            # Interactive human auditing
            for i, question in enumerate(questions, 1):
                print(f"Question {i}/{len(questions)} (ID: {question.id})")
                print(f"  Q: {question.question}")
                if question.answer:
                    print(f"  A: {question.answer}")
                if question.source_document_id:
                    print(f"  Source doc: {question.source_document_id}")

                print("\nIs this question valid?")
                print("  y - yes (valid)")
                print("  n - no (invalid)")
                print("  s - skip")
                print("  q - quit")

                while True:
                    choice = input("\nChoice [y/n/s/q]: ").strip().lower()
                    if choice in ['y', 'n', 's', 'q']:
                        break
                    print("Invalid choice, please enter y/n/s/q")

                if choice == 'q':
                    print("Quitting audit.")
                    break
                elif choice == 's':
                    print("Skipped.\n")
                    continue
                elif choice in ['y', 'n']:
                    is_valid = (choice == 'y')
                    notes = input("Notes (optional): ").strip() or None

                    add_audit(
                        question_id=question.id,
                        is_valid=is_valid,
                        audit_type="human_review",
                        comments=notes,
                    )
                    print(f"Marked as {'valid' if is_valid else 'invalid'}.\n")

    except Exception as e:
        print(f"Error auditing questions: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _agentic_meta(args):
    """Agent-loop limits for an agentic run, stored on the run so it records them."""
    if args.search_type != "agentic":
        return None
    eval_config = get_eval_config()
    return {
        "max_turns": args.max_turns or eval_config["agentic_max_turns"],
        "tool_result_max_chars": args.tool_result_max_chars or eval_config["agentic_tool_result_max_chars"],
    }


def cmd_eval_run(args):
    """Run an evaluation."""
    try:
        # Build audit filters
        audit_filters = {}
        if not args.include_invalid:
            audit_filters["is_valid"] = True
        if args.audit_type:
            audit_filters["audit_type"] = args.audit_type

        # Build search filters (Elasticsearch query DSL format)
        search_filters = {}
        if args.search_source_id:
            search_filters["source_id"] = args.search_source_id
        if args.search_parser_name:
            search_filters["parser_id"] = args.search_parser_name

        # Build judge strategy
        judge_strategy = None
        if args.use_judge:
            judge_strategy = {
                "enabled": True,
                # Resolved here so the run records which model judged it.
                "model": args.judge_model or get_eval_config()["judge_model"],
            }

        # Determine rerank setting
        rerank = None  # default: use config
        if hasattr(args, 'rerank') and args.rerank:
            rerank = True
        elif hasattr(args, 'no_rerank') and args.no_rerank:
            rerank = False

        stats = run_eval(
            name=args.name,
            description=args.description,
            generation_id=args.generation_id,
            audit_filters=audit_filters or None,
            search_type=args.search_type,
            embedding_name=args.embedding_name,
            chunking_strategy=args.chunking_strategy,
            max_results=args.max_results,
            search_filters=search_filters or None,
            answer_model=args.answer_model,
            judge_strategy=judge_strategy,
            use_llm_judge=args.use_judge,
            workers=args.workers,
            meta=_agentic_meta(args),
            rerank=rerank,
        )

        print(f"Evaluation complete!")
        print(f"  Run ID: {stats['run_id']}")
        print(f"  Questions evaluated: {stats['num_questions']}")
        print(f"  Hits: {stats['num_hits']}")
        if stats['num_questions'] > 0:
            hit_rate = stats['num_hits'] / stats['num_questions']
            print(f"  Hit rate: {hit_rate:.2%}")
            entry_hits = stats.get('num_entry_hits', 0)
            print(f"  Entry-level hits (any file/figure of the source entry): {entry_hits} "
                  f"({entry_hits / stats['num_questions']:.2%})")
        print(f"  Total time: {stats['total_time_seconds']:.1f}s")
        print(f"  Avg retrieval time: {stats['avg_retrieval_time_seconds']:.3f}s")

    except Exception as e:
        print(f"Error running evaluation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_eval_stats(args):
    """Show evaluation statistics."""
    try:
        stats = get_summary_stats(
            run_id=args.run_id,
            use_judge=args.use_judge,
        )

        print(f"Evaluation Statistics for Run: {stats['run_id']}")

        if stats['total_questions'] > 0:
            print(f"\n  Retrieval stats:")
            print(f"    Total questions: {stats['total_questions']}")
            print(f"    Hits: {stats['hits']}")
            print(f"    Misses: {stats['misses']}")
            print(f"    Hit rate: {stats['hit_rate']:.2%}")
            recall_at_k = stats.get('recall_at_k') or {}
            if recall_at_k:
                recall_str = ", ".join(f"@{k}: {v:.2%}" for k, v in sorted(recall_at_k.items()))
                print(f"    Recall {recall_str}")
            if stats['rank_distribution']:
                print(f"    Rank distribution:")
                for rank in sorted(stats['rank_distribution'].keys()):
                    print(f"      Rank {rank}: {stats['rank_distribution'][rank]}")

        if "judge_total_questions" in stats:
            print(f"\n  Judge stats:")
            print(f"    Total questions: {stats['judge_total_questions']}")
            print(f"    Hits: {stats['judge_hits']}")
            print(f"    Misses: {stats['judge_misses']}")
            print(f"    Hit rate: {stats['judge_hit_rate']:.2%}")
            judge_recall_at_k = stats.get('judge_recall_at_k') or {}
            if judge_recall_at_k:
                recall_str = ", ".join(f"@{k}: {v:.2%}" for k, v in sorted(judge_recall_at_k.items()))
                print(f"    Recall {recall_str}")

        if stats['total_questions'] == 0 and "judge_total_questions" not in stats:
            print("  No results found for this run.")

    except Exception as e:
        print(f"Error getting stats: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_eval_list(args):
    """List evaluation generations, runs, or questions."""
    try:
        if args.list_type == "generations":
            with get_db_session() as session:
                from ..eval.db_models import EvalGeneration
                generations = session.query(EvalGeneration).order_by(
                    EvalGeneration.created_time.desc()
                ).limit(args.limit).all()

                if not generations:
                    print("No generations found.")
                    return

                print(f"Recent Generations (limit {args.limit}):\n")
                for gen in generations:
                    print(f"  ID: {gen.id}")
                    if gen.name:
                        print(f"    Name: {gen.name}")
                    print(f"    Created: {gen.created_time}")
                    print(f"    Method: {gen.generation_method or 'N/A'}")
                    print()

        elif args.list_type == "runs":
            with get_db_session() as session:
                from ..eval.db_models import EvalRun
                query = session.query(EvalRun)
                if args.generation_id:
                    query = query.filter_by(generation_id=args.generation_id)
                runs = query.order_by(EvalRun.created_time.desc()).limit(args.limit).all()

                if not runs:
                    print("No runs found.")
                    return

                print(f"Recent Runs (limit {args.limit}):\n")
                for run in runs:
                    print(f"  ID: {run.id}")
                    if run.name:
                        print(f"    Name: {run.name}")
                    print(f"    Created: {run.created_time}")
                    if run.generation_id:
                        print(f"    Generation: {run.generation_id}")
                    print(f"    Embedding: {run.embedding_name}")
                    print(f"    Max results: {run.max_results}")
                    print()

        elif args.list_type == "questions":
            questions = get_eval_questions(
                generation_id=args.generation_id,
                limit=args.limit,
            )

            if not questions:
                print("No questions found.")
                return

            print(f"Questions (limit {args.limit}):\n")
            for q in questions:
                print(f"  ID: {q.id}")
                print(f"    Q: {q.question[:80]}{'...' if len(q.question) > 80 else ''}")
                if q.source_document_id:
                    print(f"    Source: {q.source_document_id}")
                print()

    except Exception as e:
        print(f"Error listing {args.list_type}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_eval_load_benchmark(args):
    """Load a hand-curated benchmark question set."""
    from ..eval.mu2e_benchmark import load_mu2e_benchmark

    try:
        result = load_mu2e_benchmark(json_path=args.path)

        print(f"Benchmark loaded: {result['generation_name']}")
        print(f"  Generation ID: {result['generation_id']}")
        print(f"  Questions loaded: {result['num_questions_loaded']}")
        print(f"  Questions skipped (duplicates): {result['num_skipped']}")
        print(f"  Total questions: {result['total_questions']}")

    except Exception as e:
        print(f"Error loading benchmark: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _print_compare_table(rows, strip_prefix=None):
    """Print compare_runs() rows as a fixed-width table."""
    def frac(n, d):
        return f"{n}/{d}" if d else "-"

    def opt(v, fmt="{}"):
        return fmt.format(v) if v is not None else "-"

    header = f"{'Run':<58}{'Q':>4}{'Exact':>7}{'Entry':>7}{'Judge':>7}{'Saw':>6}{'Open':>6}{'Limit':>6}{'Stop':>5}{'Ans s':>7}{'Ans tok':>10}{'Jdg tok':>9}"
    print(header)
    print("-" * len(header))
    for r in rows:
        name = r["name"] or r["run_id"][:8]
        if strip_prefix and name.startswith(strip_prefix):
            name = name[len(strip_prefix):].lstrip("-") or name
        n = r["questions"]
        print(
            f"{name[:57]:<58}{n:>4}"
            f"{frac(r['exact_hits'], n):>7}{frac(r['entry_hits'], n) if r['entry_hits'] is not None else '-':>7}"
            f"{frac(r['judge_correct'], r['judged']):>7}"
            f"{opt(r['agent_saw_source']):>6}{opt(r['agent_opened_source']):>6}"
            f"{opt(r['turn_limit']):>6}{opt(r['stopped']):>5}"
            f"{opt(r['avg_answer_seconds'], '{:.0f}'):>7}"
            f"{opt(r['answer_tokens'], '{:,}'):>10}{opt(r['judge_tokens'], '{:,}'):>9}"
        )
    print()
    print("Exact: source row in the retrieved list; Entry: any file/figure of the source's DocDB entry;")
    print("Judge: answers judged correct; Saw/Open: agent saw the source in a tool result / opened it with kb_get;")
    print("Limit/Stop: agent hit the turn limit / was stopped by the endpoint (e.g. context overflow).")
    print("For agentic and llm_only runs, Exact/Entry come from a separate search the agent never sees.")


def cmd_eval_grid(args):
    """Run every combination of question set, search type, answering model and judge."""
    from ..eval.grid import run_grid

    agentic_meta = None
    if "agentic" in args.search_type:
        eval_config = get_eval_config()
        agentic_meta = {
            "max_turns": args.max_turns or eval_config["agentic_max_turns"],
            "tool_result_max_chars": args.tool_result_max_chars or eval_config["agentic_tool_result_max_chars"],
        }
    outcomes = run_grid(
        prefix=args.prefix,
        generation_ids=args.generation_id,
        search_types=args.search_type,
        answer_models=args.answer_model or [None],
        judge_models=args.judge_model or [None],
        max_results=args.max_results,
        workers=args.workers,
        run_meta=agentic_meta,
        dry_run=args.dry_run,
    )
    for o in outcomes:
        how = f"  ({o['how']})" if o.get("how", "run") != "run" else ""
        print(f"{o['status']:>8}  {o['name']}{how}")
    if args.dry_run:
        return
    print()
    from ..eval.grid import compare_runs
    _print_compare_table(compare_runs(name_prefix=args.prefix), strip_prefix=args.prefix)


def cmd_eval_rejudge(args):
    """Judge an existing run's answers again with another judge model."""
    from ..eval.rejudge import rejudge_run

    stats = rejudge_run(args.run_id, args.judge_model, name=args.name, workers=args.workers)
    print(f"Re-judged {stats['num_questions']} answers as run {stats['name']}")
    print(f"  Run ID: {stats['run_id']}")
    if stats["num_questions"]:
        print(f"  Judged correct: {stats['num_judge_hits']}/{stats['num_questions']} "
              f"({stats['num_judge_hits'] / stats['num_questions']:.0%})")
    print(f"  Total time: {stats['total_time_seconds']:.1f}s")


def cmd_eval_compare(args):
    """Show runs side by side."""
    from ..eval.grid import compare_runs

    if not args.prefix and not args.run_id:
        print("Error: give --prefix and/or --run-id")
        sys.exit(1)
    rows = compare_runs(name_prefix=args.prefix, run_ids=args.run_id)
    if not rows:
        print("No runs found.")
        return
    if args.json:
        import json
        print(json.dumps(rows, indent=2))
        return
    _print_compare_table(rows, strip_prefix=args.prefix)
    if args.per_question:
        from ..eval.grid import per_question_matrix
        for block in per_question_matrix(name_prefix=args.prefix, run_ids=args.run_id):
            print()
            print(f"Questions from {block['generation_name']} (hardest first; judge verdict, else entry retrieved):")
            for i, run in enumerate(block["runs"], 1):
                name = run["name"] or run["run_id"][:8]
                if args.prefix and name.startswith(args.prefix):
                    name = name[len(args.prefix):].lstrip("-")
                print(f"  [{i}] {name}")
            print("  " + " ".join(f"{i:>2}" for i in range(1, len(block["runs"]) + 1)) + "  question")
            for row in block["rows"]:
                marks = " ".join(f"{('✓' if c['ok'] else '✗') if c else '·':>2}" for c in row["cells"])
                print(f"  {marks}  {row['question'][:100]}")


def cmd_eval_overlap(args):
    """Report how much of each question's wording comes from its source document."""
    from ...eval_utils.overlap import question_overlap
    from ..db_models import Document

    for gid in args.generation_id:
        with get_db_session() as session:
            generation = get_eval_generation(generation_id=gid, session=session)
            questions = get_eval_questions(generation_id=gid, session=session) or []
            scored = []
            for q in questions:
                doc = session.get(Document, q.source_document_id) if q.source_document_id else None
                if doc is not None:
                    scored.append((question_overlap(q.question, doc.text or ""), q.question))
        name = (generation.name if generation else None) or gid[:8]
        if not scored:
            print(f"{name}: no questions with a source document")
            continue
        n = len(scored)
        mean_bigram = sum(o["bigram_overlap"] for o, _ in scored) / n
        mean_longest = sum(o["longest_shared_words"] for o, _ in scored) / n
        copied = sum(1 for o, _ in scored if o["longest_shared_words"] >= args.phrase_words)
        print(f"{name}: {n} questions | mean bigram overlap {mean_bigram:.0%} | "
              f"mean longest copied phrase {mean_longest:.1f} words | "
              f"{copied} copy a phrase of {args.phrase_words}+ words")
        for o, text in sorted(scored, key=lambda x: (-x[0]["longest_shared_words"], -x[0]["bigram_overlap"]))[:args.show]:
            print(f"   {o['bigram_overlap']:>4.0%} {o['longest_shared_words']:>2}w  {text[:110]}")


def setup_commands(subparsers):
    """Set up evaluation commands."""
    # Eval command
    eval_parser = subparsers.add_parser("eval", help="Evaluation and benchmarking")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", help="Eval commands")

    # eval generate
    eval_generate_parser = eval_subparsers.add_parser("generate", help="Generate evaluation questions from documents")
    eval_generate_parser.add_argument("--name", help="Optional name for this generation run")
    eval_generate_parser.add_argument("--num-questions", type=int, default=1, help="Number of questions to generate per document (default: 1)")
    eval_generate_parser.add_argument("--num-documents", type=int, default=10, help="Number of documents to process (default: 10, use --num-documents 0 for all)")
    eval_generate_parser.add_argument("--strategy", default="keypoint", choices=["keypoint", "persona", "agentic"], help="Question generation strategy")
    eval_generate_parser.add_argument("--model", help="LLM model to use for generation")
    eval_generate_parser.add_argument("--source-id", help="Filter to specific source")
    eval_generate_parser.add_argument("--doc-id", action="append", metavar="UUID", help="Generate from this document (documents.id); repeatable")
    eval_generate_parser.add_argument("--same-documents-as", metavar="GENERATION_ID", help="Generate from the same documents as an earlier generation")
    eval_generate_parser.add_argument("--generation-id", help="Use existing generation ID (or create new if not exists)")
    eval_generate_parser.set_defaults(func=cmd_eval_generate)

    # eval audit
    eval_audit_parser = eval_subparsers.add_parser("audit", help="Audit generated questions")
    eval_audit_parser.add_argument("--generation-id", help="Filter to specific generation")
    eval_audit_parser.add_argument("--limit", type=int, default=20, help="Max questions to audit")
    eval_audit_parser.add_argument("--llm", action="store_true", help="Use LLM for automated auditing instead of interactive")
    eval_audit_parser.add_argument("--model", help="LLM model to use for auditing (if --llm; default: EVAL_AUDIT_MODEL, else EVAL_JUDGE_MODEL)")
    eval_audit_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Number of parallel LLM audit calls (default: 1, only applies with --llm)")
    eval_audit_parser.set_defaults(func=cmd_eval_audit)

    # eval run
    eval_run_parser = eval_subparsers.add_parser("run", help="Run an evaluation")
    eval_run_parser.add_argument("--name", help="Name for this run")
    eval_run_parser.add_argument("--description", help="Description for this run")
    eval_run_parser.add_argument("--search-type", default="semantic", choices=["semantic", "fulltext", "hybrid", "rag", "agentic", "llm_only"], help="Search/answer mode (default: semantic)")
    eval_run_parser.add_argument("--generation-id", help="Filter to questions from specific generation")
    eval_run_parser.add_argument("--include-invalid", action="store_true", help="Include questions marked as invalid")
    eval_run_parser.add_argument("--audit-type", help="Filter by audit type (e.g., 'llm_judge', 'human_review')")
    eval_run_parser.add_argument("--embedding-name", help="Embedding model to use")
    eval_run_parser.add_argument("--chunking-strategy", help="Chunking strategy to use for search (e.g., 'summary', 'tokens', 'tokens_1000_200')")
    eval_run_parser.add_argument("--max-results", type=int, default=10, help="Max search results to retrieve")
    eval_run_parser.add_argument("--search-source-id", help="Filter search to specific source")
    eval_run_parser.add_argument("--search-parser-name", help="Filter search to specific parser (e.g., 'marker', 'docling')")
    eval_run_parser.add_argument("--use-judge", action="store_true", help="Run LLM judge on results")
    eval_run_parser.add_argument("--judge-model", help="LLM model for judge (if --use-judge)")
    eval_run_parser.add_argument("--answer-model", help="LLM model for answer generation in rag/agentic/llm_only modes (default: EVAL_ANSWER_MODEL, else DEFAULT_LLM_MODEL)")
    eval_run_parser.add_argument("--max-turns", type=int, metavar="N", help="agentic: max tool rounds before the agent must answer (default: EVAL_AGENTIC_MAX_TURNS, else 10)")
    eval_run_parser.add_argument("--tool-result-max-chars", type=int, metavar="N", help="agentic: truncate each tool result to N chars, noting it for the model (default: EVAL_AGENTIC_TOOL_RESULT_MAX_CHARS, else 100000)")
    eval_run_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Number of parallel question evaluations (default: 1)")
    eval_run_parser.add_argument("--rerank", action="store_true", help="Enable cross-encoder reranking")
    eval_run_parser.add_argument("--no-rerank", action="store_true", help="Disable cross-encoder reranking")
    eval_run_parser.set_defaults(func=cmd_eval_run)

    # eval grid
    eval_grid_parser = eval_subparsers.add_parser(
        "grid", help="Run every combination of question set, search type, answer model and judge; skips runs that already have results")
    eval_grid_parser.add_argument("--prefix", required=True, help="Run name prefix, e.g. pilot-20260927; each run is named <prefix>-<type>[-<model>]-q<set>[-judge-<judge>]")
    eval_grid_parser.add_argument("--generation-id", action="append", required=True, help="Question set (generation) to evaluate; repeatable")
    eval_grid_parser.add_argument("--search-type", action="append", required=True, choices=["semantic", "fulltext", "hybrid", "rag", "agentic", "llm_only"], help="Repeatable")
    eval_grid_parser.add_argument("--answer-model", action="append", help="Answering model for rag/agentic/llm_only; repeatable (default: EVAL_ANSWER_MODEL)")
    eval_grid_parser.add_argument("--judge-model", action="append", help="Judge model; repeatable, one run per judge (default: no judge)")
    eval_grid_parser.add_argument("--max-results", type=int, default=10, help="Max search results to retrieve")
    eval_grid_parser.add_argument("--max-turns", type=int, metavar="N", help="agentic: max tool rounds (default: EVAL_AGENTIC_MAX_TURNS, else 10)")
    eval_grid_parser.add_argument("--tool-result-max-chars", type=int, metavar="N", help="agentic: per-tool-result cap (default: EVAL_AGENTIC_TOOL_RESULT_MAX_CHARS, else 100000)")
    eval_grid_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Parallel questions per run (default: 1)")
    eval_grid_parser.add_argument("--dry-run", action="store_true", help="Only list the runs, marking which would be skipped")
    eval_grid_parser.set_defaults(func=cmd_eval_grid)

    # eval rejudge
    eval_rejudge_parser = eval_subparsers.add_parser("rejudge", help="Judge an existing run's answers again with another judge (no new answers)")
    eval_rejudge_parser.add_argument("--run-id", required=True, help="Answer-mode run (rag/agentic/llm_only) to re-judge")
    eval_rejudge_parser.add_argument("--judge-model", required=True, help="Judge model for the new run")
    eval_rejudge_parser.add_argument("--name", help="Name of the new run (default: source name with its judge suffix replaced)")
    eval_rejudge_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Parallel judge calls (default: 1)")
    eval_rejudge_parser.set_defaults(func=cmd_eval_rejudge)

    # eval compare
    eval_compare_parser = eval_subparsers.add_parser("compare", help="Show runs side by side (retrieval hits, judge verdicts, agent behaviour, tokens)")
    eval_compare_parser.add_argument("--prefix", help="Runs whose name starts with this")
    eval_compare_parser.add_argument("--run-id", action="append", help="Run id; repeatable")
    eval_compare_parser.add_argument("--json", action="store_true", help="Output as JSON")
    eval_compare_parser.add_argument("--per-question", action="store_true", help="Also show a question-by-run matrix of verdicts, per question set")
    eval_compare_parser.set_defaults(func=cmd_eval_compare)

    # eval overlap
    eval_overlap_parser = eval_subparsers.add_parser("overlap", help="How much of each question's wording comes from its source document")
    eval_overlap_parser.add_argument("--generation-id", action="append", required=True, help="Question set; repeatable")
    eval_overlap_parser.add_argument("--phrase-words", type=int, default=5, help="Count questions copying a phrase at least this long (default: 5)")
    eval_overlap_parser.add_argument("--show", type=int, default=3, help="Show the N most document-worded questions per set (default: 3)")
    eval_overlap_parser.set_defaults(func=cmd_eval_overlap)

    # eval stats
    eval_stats_parser = eval_subparsers.add_parser("stats", help="Show evaluation statistics")
    eval_stats_parser.add_argument("run_id", help="Run ID to analyze")
    eval_stats_parser.add_argument("--use-judge", action="store_true", help="Show LLM judge results instead of exact matches")
    eval_stats_parser.set_defaults(func=cmd_eval_stats)

    # eval list
    eval_list_parser = eval_subparsers.add_parser("list", help="List generations, runs, or questions")
    eval_list_parser.add_argument("list_type", choices=["generations", "runs", "questions"], help="What to list")
    eval_list_parser.add_argument("--generation-id", help="Filter to specific generation (for runs/questions)")
    eval_list_parser.add_argument("--limit", type=int, default=10, help="Max items to show")
    eval_list_parser.set_defaults(func=cmd_eval_list)

    # eval load-benchmark
    eval_benchmark_parser = eval_subparsers.add_parser("load-benchmark", help="Load a hand-curated benchmark question set")
    eval_benchmark_parser.add_argument("--path", default="data/eval/mu2e_benchmark.json", help="Path to benchmark JSON (default: data/eval/mu2e_benchmark.json)")
    eval_benchmark_parser.set_defaults(func=cmd_eval_load_benchmark)
