"""CLI for Mann_Memory provider."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _provider():
    from plugins.memory import load_memory_provider
    p = load_memory_provider("mann_memory")
    if not p:
        raise SystemExit("mann_memory provider could not be loaded")
    p.initialize(session_id="cli", platform="cli")
    return p


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def mann_memory_command(args):
    cmd = getattr(args, "mann_memory_command", None)
    p = _provider()
    if cmd == "status":
        _print(json.loads(p.handle_tool_call("mann_memory_status", {})))
    elif cmd == "search":
        _print(json.loads(p.handle_tool_call("mann_memory_search", {
            "query": args.query,
            "namespace": args.namespace,
            "limit": args.limit,
            "include_turns": args.turns,
        })))
    elif cmd == "remember":
        _print(json.loads(p.handle_tool_call("mann_memory_store", {
            "content": args.content,
            "namespace": args.namespace,
            "memory_type": args.memory_type,
            "importance": args.importance,
            "confidence": args.confidence,
        })))
    elif cmd == "review":
        if args.review_action in {"approve", "reject"}:
            payload = {"action": args.review_action, "proposal_id": args.proposal_id, "namespace": args.namespace}
        elif args.review_action == "propose":
            payload = {"action": "propose", "content": args.content, "namespace": args.namespace, "memory_type": args.memory_type}
        else:
            payload = {"action": "list", "namespace": args.namespace, "limit": args.limit, "status": args.status}
        _print(json.loads(p.handle_tool_call("mann_memory_review", payload)))
    elif cmd == "memories":
        action = args.memory_action
        if action == "list":
            payload = {
                "action": "list",
                "namespace": args.namespace,
                "status": args.status,
                "query": args.query,
                "limit": args.limit,
            }
        elif action == "show":
            payload = {"action": "show", "memory_id": args.memory_id}
        elif action == "update":
            payload = {"action": "update", "memory_id": args.memory_id}
            if args.content is not None:
                payload["content"] = args.content
            if args.memory_type is not None:
                payload["memory_type"] = args.memory_type
            if args.importance is not None:
                payload["importance"] = args.importance
            if args.confidence is not None:
                payload["confidence"] = args.confidence
        else:
            payload = {"action": action, "memory_id": args.memory_id}
        _print(json.loads(p.handle_tool_call("mann_memory_manage", payload)))
    elif cmd == "forget":
        _print(json.loads(p.handle_tool_call("mann_memory_forget", {
            "memory_id": args.memory_id,
            "mode": args.mode,
        })))
    elif cmd == "dream":
        _print(json.loads(p.handle_tool_call("mann_memory_graph", {
            "action": "dream",
            "namespace": args.namespace,
            "peer_id": args.peer_id,
            "limit": args.limit,
        })))
    elif cmd == "portability":
        payload = {"action": args.portability_action}
        if getattr(args, "path", None):
            payload["path"] = str(args.path)
        if getattr(args, "namespace", ""):
            payload["namespace"] = args.namespace
        if hasattr(args, "include_archived"):
            payload["include_archived"] = args.include_archived
        if hasattr(args, "dry_run"):
            payload["dry_run"] = args.dry_run
        if hasattr(args, "overwrite"):
            payload["overwrite"] = args.overwrite
        _print(json.loads(p.handle_tool_call("mann_memory_portability", payload)))
    else:
        print("Usage: hermes mann_memory {status|search|remember|review|memories|forget|dream|portability}")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="mann_memory_command")

    status = subs.add_parser("status", help="Show Mann_Memory database path and counts")
    status.set_defaults(func=mann_memory_command)

    search = subs.add_parser("search", help="Search local memories")
    search.add_argument("query")
    search.add_argument("--namespace", default="default")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--turns", action="store_true", help="Include synced conversation turns")
    search.set_defaults(func=mann_memory_command)

    remember = subs.add_parser("remember", help="Store a durable memory")
    remember.add_argument("content")
    remember.add_argument("--namespace", default="default")
    remember.add_argument("--memory-type", default="fact", choices=["fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"])
    remember.add_argument("--importance", type=float, default=0.6)
    remember.add_argument("--confidence", type=float, default=0.7)
    remember.set_defaults(func=mann_memory_command)

    review = subs.add_parser("review", help="List/propose/approve/reject memory proposals")
    review.add_argument("review_action", nargs="?", default="list", choices=["list", "propose", "approve", "reject"])
    review.add_argument("proposal_id", nargs="?")
    review.add_argument("--content", default="")
    review.add_argument("--namespace", default="default")
    review.add_argument("--memory-type", default="fact", choices=["fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"])
    review.add_argument("--limit", type=int, default=20)
    review.add_argument("--status", default="pending", choices=["pending", "quarantined", "approved", "rejected", "all"], help="Proposal status filter for review list")
    review.set_defaults(func=mann_memory_command)


    memories = subs.add_parser("memories", help="Inspect/control durable memories")
    memory_subs = memories.add_subparsers(dest="memory_action", required=True)
    memory_list = memory_subs.add_parser("list", help="List durable memories")
    memory_list.add_argument("--namespace", default="default")
    memory_list.add_argument("--status", default="active", choices=["active", "archived", "deleted", "all"])
    memory_list.add_argument("--query", default="", help="Optional substring filter")
    memory_list.add_argument("--limit", type=int, default=50)
    memory_list.set_defaults(func=mann_memory_command)
    memory_show = memory_subs.add_parser("show", help="Show one memory by ID")
    memory_show.add_argument("memory_id")
    memory_show.set_defaults(func=mann_memory_command)
    memory_update = memory_subs.add_parser("update", help="Update one memory by ID")
    memory_update.add_argument("memory_id")
    memory_update.add_argument("--content")
    memory_update.add_argument("--memory-type", choices=["fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"])
    memory_update.add_argument("--importance", type=float)
    memory_update.add_argument("--confidence", type=float)
    memory_update.set_defaults(func=mann_memory_command)
    memory_archive = memory_subs.add_parser("archive", help="Archive one memory by ID")
    memory_archive.add_argument("memory_id")
    memory_archive.set_defaults(func=mann_memory_command)
    memory_delete = memory_subs.add_parser("delete", help="Delete one memory by ID")
    memory_delete.add_argument("memory_id")
    memory_delete.set_defaults(func=mann_memory_command)

    forget = subs.add_parser("forget", help="Archive or delete a memory")
    forget.add_argument("memory_id")
    forget.add_argument("--mode", default="archive", choices=["archive", "delete"])
    forget.set_defaults(func=mann_memory_command)

    dream = subs.add_parser("dream", help="Run a local deterministic dream/consolidation pass")
    dream.add_argument("--namespace", default="default")
    dream.add_argument("--peer-id", default="", help="Optional peer id to dream for; blank means workspace")
    dream.add_argument("--limit", type=int, default=24, help="Recent messages to inspect")
    dream.set_defaults(func=mann_memory_command)

    portability = subs.add_parser("portability", help="Export, back up, restore, import, and inspect migrations")
    portability_subs = portability.add_subparsers(dest="portability_action", required=True)
    export_jsonl = portability_subs.add_parser("export-jsonl", help="Export Mann_Memory rows as JSONL")
    export_jsonl.set_defaults(portability_action="export_jsonl", func=mann_memory_command)
    export_jsonl.add_argument("path", type=Path)
    export_jsonl.add_argument("--namespace", default="", help="Optional namespace filter")
    export_jsonl.add_argument("--include-archived", action="store_true", default=True, help="Include archived/deleted rows (default)")
    export_jsonl.add_argument("--active-only", dest="include_archived", action="store_false", help="Export only active status rows where supported")
    backup_sqlite = portability_subs.add_parser("backup-sqlite", help="Create a consistent SQLite backup")
    backup_sqlite.set_defaults(portability_action="backup_sqlite", func=mann_memory_command)
    backup_sqlite.add_argument("path", type=Path)
    migrations = portability_subs.add_parser("migrations", help="Show schema migration history")
    migrations.set_defaults(portability_action="migrations", func=mann_memory_command)
    restore_sqlite = portability_subs.add_parser("restore-sqlite", help="Restore the SQLite DB from a backup file")
    restore_sqlite.set_defaults(portability_action="restore_sqlite", func=mann_memory_command)
    restore_sqlite.add_argument("path", type=Path)
    import_jsonl = portability_subs.add_parser("import-jsonl", help="Import JSONL export; dry-run previews conflicts by default")
    import_jsonl.set_defaults(portability_action="import_jsonl", func=mann_memory_command)
    import_jsonl.add_argument("path", type=Path)
    import_jsonl.add_argument("--namespace", default="", help="Override namespace while importing")
    import_jsonl.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Preview without writing (default)")
    import_jsonl.add_argument("--apply", dest="dry_run", action="store_false", help="Actually import rows")
    import_jsonl.add_argument("--overwrite", action="store_true", help="Replace rows with matching IDs")

    subparser.set_defaults(func=mann_memory_command)
