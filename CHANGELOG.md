# Changelog

## Unreleased

- Sanitize public test/documentation examples by removing a personal-name fixture and a private organization logo reference.

## 0.1.8 - 2026-06-23

- Add conservative `cleanup_hygiene` maintenance for review queues: dry-run by default, apply rejects duplicate/noisy pending proposals without auto-approving anything.
- Add duplicate active-memory cleanup that archives duplicate durable memories instead of hard-deleting them.
- Add CLI `hermes mann_memory cleanup --dry-run|--apply` and graph action support for operator-controlled cleanup.
- Add regression coverage for duplicate proposal rejection, transcript/task-fragment cleanup, and duplicate memory archiving.
- Document safe chat-bridge context-packet integration so large Mann_Memory results cannot truncate away persona and identity-boundary instructions.

## 0.1.7 - 2026-06-15

- Add one-command `export_backup` portability action that creates a portable backup bundle containing JSONL export, SQLite snapshot, manifest, and SHA-256 checksums.
- Add CLI `hermes mann_memory portability export-backup ...` for operator-friendly backup creation.
- Add regression coverage that verifies bundle files, manifest metadata, and checksums are produced.

## 0.1.6 - 2026-06-15

- Add `mann_memory_portability` for JSONL export, consistent SQLite backup, schema migration history, SQLite restore, and JSONL import.
- Add CLI `hermes mann_memory portability ...` commands, including dry-run import previews with duplicate/conflict summaries.
- Add `schema_migrations` table and migration-history reporting for portability/audit readiness.
- Rebuild FTS indexes after JSONL import or SQLite restore so restored memories are immediately searchable.
- Add regression coverage for JSONL export/import preview/apply, SQLite backup/restore, and migration-history reporting.

## 0.1.5 - 2026-06-15

- Add `mann_memory_manage` for operator visibility/control of durable memory contents: list, show, update, archive, and delete by ID.
- Add CLI `hermes mann_memory memories ...` commands for memory inspection and correction.
- Keep archived/deleted rows out of normal recall while allowing explicit audit with `status=all`.
- Add regression coverage for namespace-isolated listing, show, update, archive, and all-status audit listing.

## 0.1.4 - 2026-06-09

- Rename the provider/package/CLI identity from `local_sqlite_memory` to `mann_memory` / Mann_Memory so managed Hermes computers use one consistent memory-provider name.
- Update install/config examples to use `memory.provider = mann_memory`, `$HERMES_HOME/mann_memory.json`, and `$HERMES_HOME/mann-memory/memory.sqlite3`.

## 0.1.3 - 2026-06-09

- Add deterministic Memory Guard scoring for manual proposals, auto-proposals, and direct memory-store writes.
- Quarantine suspicious memory candidates with `status=quarantined` and guard metadata instead of mixing them into the normal pending review queue.
- Detect prompt-injection language, possible secrets, cross-namespace identity contamination, and imperative system-instruction style memories.
- Add review-list status filtering for quarantined proposals plus CLI `--status quarantined` support.
- Add regression tests covering quarantine, normal proposals, direct-store quarantine, and session-end auto-proposal quarantine.

## 0.1.2 - 2026-06-07

- Add built-in opportunistic self-maintenance so active assistants can run cron-style cleanup plus workspace/peer dreaming from the provider's session-end hook.
- Add provider config for `auto_dream`, `auto_dream_interval_seconds`, `auto_dream_limit`, and `assistant_handle`.
- Add graph tool actions `cleanup` and `self_maintain` for manual/integration-level maintenance.
- Add regression tests for automatic session-end dreaming, noise cleanup, and peer dream representation refresh.

## 0.1.1 - 2026-06-07

- Allow user-defined memory namespaces in the provider configuration schema by removing the fixed namespace choices list while still sanitizing namespace values internally.
- Add package `__version__` and bump package/plugin metadata to 0.1.1.
- Add a regression test that verifies the namespace config field remains open-ended for multi-assistant deployments.
