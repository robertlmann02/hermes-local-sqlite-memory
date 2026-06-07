# Changelog

## 0.1.2 - 2026-06-07

- Add built-in opportunistic self-maintenance so active assistants can run cron-style cleanup plus workspace/peer dreaming from the provider's session-end hook.
- Add provider config for `auto_dream`, `auto_dream_interval_seconds`, `auto_dream_limit`, and `assistant_handle`.
- Add graph tool actions `cleanup` and `self_maintain` for manual/integration-level maintenance.
- Add regression tests for automatic session-end dreaming, noise cleanup, and peer dream representation refresh.

## 0.1.1 - 2026-06-07

- Allow user-defined memory namespaces in the provider configuration schema by removing the fixed namespace choices list while still sanitizing namespace values internally.
- Add package `__version__` and bump package/plugin metadata to 0.1.1.
- Add a regression test that verifies the namespace config field remains open-ended for multi-assistant deployments.
