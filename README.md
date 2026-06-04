# Hermes Local SQLite Memory

A local-first SQLite/FTS5 memory provider for [Hermes Agent](https://hermes-agent.nousresearch.com/docs). It is designed for private self-hosted assistants that need durable memory, searchable turn history, reviewable memory proposals, namespace isolation, graph-style context, and deterministic local consolidation without a cloud memory service.

## Features

- Local SQLite database with WAL mode and FTS5 search.
- Durable memories with type, confidence, importance, status, and namespace fields.
- Synced conversation turns for searchable recall.
- Review queue: propose, list, approve, reject, archive/delete.
- Graph-style workspace, peer, session, message, conclusion, and representation tables.
- Deterministic local consolidation pass for repeated recent patterns.
- User-defined namespaces so different assistants/users stay separated.
- Runtime tools for store/search/context/review/forget/status/graph operations.
- No vector database, embedding model, or cloud memory dependency.

## Install as a Hermes memory plugin

Copy the provider package into a Hermes memory plugin directory:

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/local_sqlite_memory
cp hermes_local_sqlite_memory/__init__.py ~/.hermes/hermes-agent/plugins/memory/local_sqlite_memory/__init__.py
cp hermes_local_sqlite_memory/cli.py ~/.hermes/hermes-agent/plugins/memory/local_sqlite_memory/cli.py
cp plugin/plugin.yaml ~/.hermes/hermes-agent/plugins/memory/local_sqlite_memory/plugin.yaml
```

Then activate it:

```bash
hermes config set memory.provider local_sqlite_memory
hermes memory status
```

Optional provider config at `$HERMES_HOME/local_sqlite_memory.json`:

```json
{
  "db_path": "$HERMES_HOME/local-sqlite-memory/memory.sqlite3",
  "namespace": "default",
  "context_limit": "8",
  "sync_turns": "true",
  "auto_propose": "true"
}
```

## Tool names

- `local_memory_store`
- `local_memory_search`
- `local_memory_context`
- `local_memory_review`
- `local_memory_forget`
- `local_memory_graph`
- `local_memory_status`

## CLI examples

```bash
hermes local_sqlite_memory status
hermes local_sqlite_memory remember "User prefers concise status updates." --namespace default --memory-type preference
hermes local_sqlite_memory search "concise status" --namespace default
hermes local_sqlite_memory review list --namespace default
hermes local_sqlite_memory dream --namespace default
```

## Development checks

Run from this repository with Hermes Agent source on `PYTHONPATH`:

```bash
PYTHONPATH=/path/to/hermes-agent python -m pytest tests -q
PYTHONPATH=/path/to/hermes-agent python -m py_compile hermes_local_sqlite_memory/__init__.py hermes_local_sqlite_memory/cli.py
```

## Privacy notes

This package stores memory locally in SQLite. Do not commit `.env` files, live memory databases, logs, profile directories, or user-specific exports. Use separate namespaces or separate Hermes profiles for distinct assistants, users, or organizations.
