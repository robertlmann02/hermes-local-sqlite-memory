# Hermes Local SQLite Memory

A local-first SQLite/FTS5 memory provider for [Hermes Agent](https://hermes-agent.nousresearch.com/docs). It is designed for private self-hosted assistants that need durable memory, searchable turn history, reviewable memory proposals, namespace isolation, graph-style context, and deterministic local consolidation without a cloud memory service.

## Features

- Local SQLite database with WAL mode and FTS5 search.
- Durable memories with type, confidence, importance, status, and namespace fields.
- Synced conversation turns for searchable recall.
- Review queue: propose, list, approve, reject, archive/delete.
- Graph-style workspace, peer, session, message, conclusion, and representation tables.
- Deterministic local consolidation pass for repeated recent patterns.
- Opportunistic self-maintenance: cleanup + workspace/peer dreaming can run at session end without an external cron job.
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
  "auto_propose": "true",
  "auto_dream": "true",
  "auto_dream_interval_seconds": "86400",
  "auto_dream_limit": "48",
  "assistant_handle": "default"
}
```

With `auto_dream=true`, the provider performs the same class of maintenance that an external daily cron would normally do for a single active namespace:

- reject/archive obvious smoke-test, tool-fragment, and health-check memory noise;
- ensure the configured assistant peer exists;
- run a deterministic workspace dream pass;
- run a deterministic assistant-peer dream pass;
- record the last run in workspace metadata so it does not repeat more often than `auto_dream_interval_seconds`.

This is opportunistic: it runs when Hermes calls the provider's session-end hook, so active assistants maintain themselves locally. Fleet-wide cross-host scheduling is still possible, but is no longer required for active bots.

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

The graph tool also exposes `cleanup` and `self_maintain` actions for manual or integration-level maintenance.

## Development checks

Run from this repository with Hermes Agent source on `PYTHONPATH`:

```bash
PYTHONPATH=/path/to/hermes-agent python -m pytest tests -q
PYTHONPATH=/path/to/hermes-agent python -m py_compile hermes_local_sqlite_memory/__init__.py hermes_local_sqlite_memory/cli.py
```

## Privacy notes

This package stores memory locally in SQLite. Do not commit `.env` files, live memory databases, logs, profile directories, or user-specific exports. Use separate namespaces or separate Hermes profiles for distinct assistants, users, or organizations.
