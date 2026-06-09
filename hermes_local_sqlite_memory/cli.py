"""CLI for Local SQLite Memory provider."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _provider():
    from plugins.memory import load_memory_provider
    p = load_memory_provider("local_sqlite_memory")
    if not p:
        raise SystemExit("local_sqlite_memory provider could not be loaded")
    p.initialize(session_id="cli", platform="cli")
    return p


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def local_sqlite_memory_command(args):
    cmd = getattr(args, "local_sqlite_memory_command", None)
    p = _provider()
    if cmd == "status":
        _print(json.loads(p.handle_tool_call("local_memory_status", {})))
    elif cmd == "search":
        _print(json.loads(p.handle_tool_call("local_memory_search", {
            "query": args.query,
            "namespace": args.namespace,
            "limit": args.limit,
            "include_turns": args.turns,
        })))
    elif cmd == "remember":
        _print(json.loads(p.handle_tool_call("local_memory_store", {
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
        _print(json.loads(p.handle_tool_call("local_memory_review", payload)))
    elif cmd == "forget":
        _print(json.loads(p.handle_tool_call("local_memory_forget", {
            "memory_id": args.memory_id,
            "mode": args.mode,
        })))
    elif cmd == "dream":
        _print(json.loads(p.handle_tool_call("local_memory_graph", {
            "action": "dream",
            "namespace": args.namespace,
            "peer_id": args.peer_id,
            "limit": args.limit,
        })))
    else:
        print("Usage: hermes local_sqlite_memory {status|search|remember|review|forget|dream}")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="local_sqlite_memory_command")

    status = subs.add_parser("status", help="Show local memory database path and counts")
    status.set_defaults(func=local_sqlite_memory_command)

    search = subs.add_parser("search", help="Search local memories")
    search.add_argument("query")
    search.add_argument("--namespace", default="default")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--turns", action="store_true", help="Include synced conversation turns")
    search.set_defaults(func=local_sqlite_memory_command)

    remember = subs.add_parser("remember", help="Store a durable memory")
    remember.add_argument("content")
    remember.add_argument("--namespace", default="default")
    remember.add_argument("--memory-type", default="fact", choices=["fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"])
    remember.add_argument("--importance", type=float, default=0.6)
    remember.add_argument("--confidence", type=float, default=0.7)
    remember.set_defaults(func=local_sqlite_memory_command)

    review = subs.add_parser("review", help="List/propose/approve/reject memory proposals")
    review.add_argument("review_action", nargs="?", default="list", choices=["list", "propose", "approve", "reject"])
    review.add_argument("proposal_id", nargs="?")
    review.add_argument("--content", default="")
    review.add_argument("--namespace", default="default")
    review.add_argument("--memory-type", default="fact", choices=["fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"])
    review.add_argument("--limit", type=int, default=20)
    review.add_argument("--status", default="pending", choices=["pending", "quarantined", "approved", "rejected", "all"], help="Proposal status filter for review list")
    review.set_defaults(func=local_sqlite_memory_command)

    forget = subs.add_parser("forget", help="Archive or delete a memory")
    forget.add_argument("memory_id")
    forget.add_argument("--mode", default="archive", choices=["archive", "delete"])
    forget.set_defaults(func=local_sqlite_memory_command)

    dream = subs.add_parser("dream", help="Run a local deterministic dream/consolidation pass")
    dream.add_argument("--namespace", default="default")
    dream.add_argument("--peer-id", default="", help="Optional peer id to dream for; blank means workspace")
    dream.add_argument("--limit", type=int, default=24, help="Recent messages to inspect")
    dream.set_defaults(func=local_sqlite_memory_command)

    subparser.set_defaults(func=local_sqlite_memory_command)
