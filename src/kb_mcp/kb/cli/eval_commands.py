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


def cmd_eval_generate(args):
    """Generate evaluation questions from documents."""
    try:
        if args.source_id:
            # Use generate_questions_from_source when source_id is provided
            # Convert 0 to None to process all documents
            num_docs = None if args.num_documents == 0 else args.num_documents
            # Ensure we have a valid num_questions value (default should be 1 from argparse)
            num_questions = getattr(args, 'num_questions', 1) or 1
            result = generate_questions_from_source(
                source_id=args.source_id,
                num_documents=num_docs,
                num_questions_per_doc=num_questions,
                generation_method=args.strategy,
                model=args.model,
                name=args.name,
            )
        else:
            # For document-specific generation, we'd need document_ids
            # This is not currently supported via CLI
            print("Error: --source-id is required for question generation")
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
                "model": args.judge_model,
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
            rerank=rerank,
        )

        print(f"Evaluation complete!")
        print(f"  Run ID: {stats['run_id']}")
        print(f"  Questions evaluated: {stats['num_questions']}")
        print(f"  Hits: {stats['num_hits']}")
        if stats['num_questions'] > 0:
            hit_rate = stats['num_hits'] / stats['num_questions']
            print(f"  Hit rate: {hit_rate:.2%}")
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
    eval_generate_parser.add_argument("--doc-id", help="Filter to specific document")
    eval_generate_parser.add_argument("--generation-id", help="Use existing generation ID (or create new if not exists)")
    eval_generate_parser.set_defaults(func=cmd_eval_generate)

    # eval audit
    eval_audit_parser = eval_subparsers.add_parser("audit", help="Audit generated questions")
    eval_audit_parser.add_argument("--generation-id", help="Filter to specific generation")
    eval_audit_parser.add_argument("--limit", type=int, default=20, help="Max questions to audit")
    eval_audit_parser.add_argument("--llm", action="store_true", help="Use LLM for automated auditing instead of interactive")
    eval_audit_parser.add_argument("--model", help="LLM model to use for auditing (if --llm)")
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
    eval_run_parser.add_argument("--answer-model", help="LLM model for answer generation in rag/agentic/llm_only modes (default: EVAL_GEN_MODEL)")
    eval_run_parser.add_argument("--workers", type=int, default=1, metavar="N", help="Number of parallel question evaluations (default: 1)")
    eval_run_parser.add_argument("--rerank", action="store_true", help="Enable cross-encoder reranking")
    eval_run_parser.add_argument("--no-rerank", action="store_true", help="Disable cross-encoder reranking")
    eval_run_parser.set_defaults(func=cmd_eval_run)

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
