# Hermes Mann_Memory

A local-first SQLite/FTS5 memory provider for [Hermes Agent](https://hermes-agent.nousresearch.com/docs). It is designed for private self-hosted assistants that need durable memory, searchable turn history, reviewable memory proposals, namespace isolation, graph-style context, and deterministic local consolidation without a cloud memory service.

## Features

- Local SQLite database with WAL mode and FTS5 search.
- Durable memories with type, confidence, importance, status, and namespace fields.
- Synced conversation turns for searchable recall.
- Review queue: propose, list, approve, reject, archive/delete.
- Memory Guard: deterministic poisoning/secret-risk scoring quarantines suspicious proposals away from the normal pending queue.
- Graph-style workspace, peer, session, message, conclusion, and representation tables.
- Deterministic local consolidation pass for repeated recent patterns.
- Opportunistic self-maintenance: cleanup + workspace/peer dreaming can run at session end without an external cron job.
- User-defined namespaces so different assistants/users stay separated.
- Runtime tools for store/search/context/review/forget/status/graph plus durable-memory inspection/control and portability operations.
- JSONL export/import, consistent SQLite backups, restore, schema migration history, and dry-run import conflict previews.
- No vector database, embedding model, or cloud memory dependency.

## Install as a Hermes memory plugin

Copy the provider package into a Hermes memory plugin directory:

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/mann_memory
cp mann_memory/__init__.py ~/.hermes/hermes-agent/plugins/memory/mann_memory/__init__.py
cp mann_memory/cli.py ~/.hermes/hermes-agent/plugins/memory/mann_memory/cli.py
cp plugin/plugin.yaml ~/.hermes/hermes-agent/plugins/memory/mann_memory/plugin.yaml
```

Then activate it:

```bash
hermes config set memory.provider mann_memory
hermes memory status
```

Optional provider config at `$HERMES_HOME/mann_memory.json`:

```json
{
  "db_path": "$HERMES_HOME/mann-memory/memory.sqlite3",
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

- `mann_memory_store`
- `mann_memory_search`
- `mann_memory_context`
- `mann_memory_review`
- `mann_memory_forget`
- `mann_memory_manage`
- `mann_memory_graph`
- `mann_memory_portability`
- `mann_memory_status`

## CLI examples

```bash
hermes mann_memory status
hermes mann_memory remember "User prefers concise status updates." --namespace default --memory-type preference
hermes mann_memory search "concise status" --namespace default
hermes mann_memory review list --namespace default
hermes mann_memory review list --namespace default --status quarantined
hermes mann_memory memories list --namespace default --status active
hermes mann_memory memories show dlm_example1234
hermes mann_memory memories update dlm_example1234 --content "Corrected durable memory text." --memory-type fact
hermes mann_memory memories archive dlm_example1234
hermes mann_memory portability export-jsonl ~/mann-memory-export.jsonl --namespace default
hermes mann_memory portability backup-sqlite ~/mann-memory-backup.sqlite3
hermes mann_memory portability migrations
hermes mann_memory portability import-jsonl ~/mann-memory-export.jsonl --dry-run
hermes mann_memory portability import-jsonl ~/mann-memory-export.jsonl --apply
hermes mann_memory portability restore-sqlite ~/mann-memory-backup.sqlite3
hermes mann_memory dream --namespace default
```

The graph tool also exposes `cleanup` and `self_maintain` actions for manual or integration-level maintenance.


## Inspecting and controlling memory contents

Use `mann_memory_manage` or the `hermes mann_memory memories ...` CLI group when an operator needs to see or correct what the provider currently remembers. The management surface is intentionally ID-based so changes are auditable and reversible at the row level.

Supported actions:

- `list` — list active, archived, deleted, or all durable memories in a namespace, with an optional substring query.
- `show` — show one memory by ID.
- `update` — replace content, category, importance, or confidence for one memory by ID.
- `archive` — remove one memory from active recall without deleting its row.
- `delete` — mark one memory deleted and remove it from active recall.

Archived and deleted memories are excluded from normal `mann_memory_search`/context recall. Use `status=all` only for operator review/audit.

## Backup, export, restore, and migration history

Use `mann_memory_portability` or the `hermes mann_memory portability ...` CLI group before moving data between profiles/hosts or before expanding memory features.

Supported actions:

- `export_jsonl` / `export-jsonl` — writes a line-delimited export with a manifest plus rows from durable memory, review, turn, and graph tables. Use `--namespace` to filter to one namespace, and `--active-only` to omit archived/deleted memories and conclusions.
- `backup_sqlite` / `backup-sqlite` — creates a consistent SQLite backup using the SQLite backup API after a WAL checkpoint.
- `migrations` — reports the local `schema_migrations` history and current schema version.
- `restore_sqlite` / `restore-sqlite` — restores the active database from a SQLite backup, writes a pre-restore backup beside the live DB, and rebuilds FTS indexes.
- `import_jsonl` / `import-jsonl` — previews or applies a JSONL import. Dry-run is the default and reports duplicate IDs plus content-level memory/proposal conflicts before anything is written.

Import behavior is conservative by default: rows with duplicate IDs or detected content conflicts are skipped unless `--overwrite` is explicitly provided. Use `--namespace <name>` during import to remap all namespaced rows into a new namespace.

## Memory Guard quarantine

Every manual proposal, auto-proposal, and direct `mann_memory_store` write is scored by a local deterministic guard before it can become durable memory. The guard flags patterns associated with:

- prompt-injection language such as ignoring or overriding system/developer/user instructions;
- possible secrets such as API keys, access tokens, or private-key material;
- cross-namespace identity contamination such as merging assistant identities;
- imperative system-instruction style memories.

Suspicious direct stores are not written to active memory. They are placed in the review queue with `status=quarantined` and guard metadata explaining the score/reasons. Normal review lists still show only `pending` items by default; inspect quarantined entries explicitly with `mann_memory_review` status `quarantined` or the CLI `--status quarantined` option.

## Development checks

Run from this repository with Hermes Agent source on `PYTHONPATH`:

```bash
PYTHONPATH=/path/to/hermes-agent python -m pytest tests -q
PYTHONPATH=/path/to/hermes-agent python -m py_compile mann_memory/__init__.py mann_memory/cli.py
```

## Privacy notes

This package stores memory locally in SQLite. Do not commit `.env` files, live memory databases, logs, profile directories, or user-specific exports. Use separate namespaces or separate Hermes profiles for distinct assistants, users, or organizations.
