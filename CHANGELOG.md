# Changelog

## 0.1.1 - 2026-06-07

- Allow user-defined memory namespaces in the provider configuration schema by removing the fixed namespace choices list while still sanitizing namespace values internally.
- Add package `__version__` and bump package/plugin metadata to 0.1.1.
- Add a regression test that verifies the namespace config field remains open-ended for multi-assistant deployments.
