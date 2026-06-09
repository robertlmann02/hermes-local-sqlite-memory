# Changelog

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
