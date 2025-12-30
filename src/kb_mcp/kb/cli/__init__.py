#!/usr/bin/env python3
"""CLI tool for knowledge base operations."""

import argparse
import sys


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
                ("Document Operations", ["ingest", "get", "embed", "drop", "search", "similar"]),
                ("Chunks, Embeddings & Sources", ["source", "chunks", "embedding"]),  # "emb" is an alias, will be shown
                ("Knowledge Graph", ["graph"]),
                ("Evaluation & Benchmarking", ["eval"]),
                ("Tools & Statistics", ["tools", "stats", "logs"]),
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


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Knowledge base CLI",
        formatter_class=GroupedHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Import and setup all command modules
    from . import document_commands
    from . import embedding_commands
    from . import search_commands
    from . import eval_commands
    from . import tools_commands
    from . import source_commands
    from . import graph_commands

    # Set up commands from each module
    document_commands.setup_commands(subparsers)
    embedding_commands.setup_commands(subparsers)
    search_commands.setup_commands(subparsers)
    eval_commands.setup_commands(subparsers)
    tools_commands.setup_commands(subparsers)
    source_commands.setup_commands(subparsers)
    graph_commands.setup_commands(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute the command function
    if hasattr(args, 'func'):
        args.func(args)
    else:
        # Handle commands with subcommands that weren't specified
        if args.command == "chunks":
            from .embedding_commands import cmd_chunks_list, cmd_chunks_chunk, cmd_chunks_get, cmd_chunks_drop
            if args.chunks_command == "list":
                cmd_chunks_list(args)
            elif args.chunks_command == "chunk":
                cmd_chunks_chunk(args)
            elif args.chunks_command == "get":
                cmd_chunks_get(args)
            elif args.chunks_command == "drop":
                cmd_chunks_drop(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "embedding" or args.command == "emb":
            from .embedding_commands import cmd_embedding_list, cmd_embedding_embed, cmd_embedding_get, cmd_embedding_drop, cmd_embedding_embed_all
            if args.embedding_command == "list":
                cmd_embedding_list(args)
            elif args.embedding_command == "embed":
                cmd_embedding_embed(args)
            elif args.embedding_command == "get":
                cmd_embedding_get(args)
            elif args.embedding_command == "drop":
                cmd_embedding_drop(args)
            elif args.embedding_command == "embed-all":
                cmd_embedding_embed_all(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "source":
            from .source_commands import cmd_source_add, cmd_source_list
            if args.source_command == "add":
                cmd_source_add(args)
            elif args.source_command == "list":
                cmd_source_list(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "tools":
            from .tools_commands import cmd_deduplicate, cmd_chunk_and_embed_all, cmd_summarize_all, cmd_list_tables, cmd_drop_table, cmd_get_raw, cmd_drop_raw, cmd_extract_all
            if args.tools_command == "deduplicate":
                cmd_deduplicate(args)
            elif args.tools_command == "chunk-and-embed-all":
                cmd_chunk_and_embed_all(args)
            elif args.tools_command == "summarize-all":
                cmd_summarize_all(args)
            elif args.tools_command == "list-tables":
                cmd_list_tables(args)
            elif args.tools_command == "drop-table":
                cmd_drop_table(args)
            elif args.tools_command == "get-raw":
                cmd_get_raw(args)
            elif args.tools_command == "drop-raw":
                cmd_drop_raw(args)
            elif args.tools_command == "extract-all":
                cmd_extract_all(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "logs":
            from .tools_commands import cmd_logs_chunking, cmd_logs_parsing
            from .search_commands import cmd_search_logs
            if args.logs_command == "search":
                cmd_search_logs(args)
            elif args.logs_command == "chunking":
                cmd_logs_chunking(args)
            elif args.logs_command == "parsing":
                cmd_logs_parsing(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "eval":
            from .eval_commands import cmd_eval_generate, cmd_eval_audit, cmd_eval_run, cmd_eval_stats, cmd_eval_list
            if args.eval_command == "generate":
                cmd_eval_generate(args)
            elif args.eval_command == "audit":
                cmd_eval_audit(args)
            elif args.eval_command == "run":
                cmd_eval_run(args)
            elif args.eval_command == "stats":
                cmd_eval_stats(args)
            elif args.eval_command == "list":
                cmd_eval_list(args)
            else:
                parser.print_help()
                sys.exit(1)
        elif args.command == "graph":
            from .graph_commands import cmd_get_node, cmd_get_nodes_for_document, cmd_process_document, cmd_extract_all
            if args.graph_command == "get-node":
                cmd_get_node(args)
            elif args.graph_command == "get-nodes-for-document":
                cmd_get_nodes_for_document(args)
            elif args.graph_command == "process-document":
                cmd_process_document(args)
            elif args.graph_command == "extract-all":
                cmd_extract_all(args)
            else:
                parser.print_help()
                sys.exit(1)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
