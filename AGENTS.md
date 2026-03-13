# Confluid Mandates

- **Post-Construction Paradigm:** Configuration MUST be applied to already-instantiated objects. Never require constructor-time configuration injection.
- **Tag-Based IR:** All serialization MUST use YAML tags (`!class:ClassName`, `!ref:path`). Never use plain dictionaries for class representations.
- **Registry Discipline:** Only `@configurable` classes and explicitly `@register`-ed third-party classes may participate in the config graph. Never traverse into unregistered library internals.
- **Scope Isolation:** Hierarchical scopes MUST merge cleanly via `resolve_scopes()`. A scope override must never corrupt the base configuration.
- **Serialization Symmetry:** `dump()` followed by `load()` MUST reconstruct an identical object graph. Round-trip fidelity is non-negotiable.
- **Type Safety:** Strict mypy enforcement (`disallow_untyped_defs=true`). All public APIs must have complete type annotations.

## Testing & Validation
- **Round-Trip Tests:** Every new feature MUST include a test that dumps and reloads the configured object graph.
- **Registry Cleanup:** Tests MUST use `setup_registry()` fixtures to clear global state between runs.
- **Line Length:** 120 characters (Black, isort, flake8).
