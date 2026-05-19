# Confluid Mandates

- **Post-Construction Paradigm:** Configuration MUST be applied to already-instantiated objects. Never require constructor-time configuration injection.
- **Tag-Based IR:** All serialization MUST use YAML tags (`!class:ClassName`, `!ref:path`). Never use plain dictionaries for class representations.
- **Registry Discipline:** Only `@configurable` classes and explicitly `@register`-ed third-party classes may participate in the config graph. Never traverse into unregistered library internals.
- **Tag-Driven Scopes:** Scopes are a tagged construct (`!scope:KEY[=VAL]` and `!notscope:KEY[=VAL]`, with `KEY(VAL)` equivalent to `KEY=VAL`); they live in confluid and are activated via the `scopes=` kwarg on `load()`. Liquifai parses `--scope` and dimension-bound `--KEY VAL` flags from the CLI and forwards the merged list. `!notscope:` uses the unset-⇒-active convention. Bare dict keys are never treated as scopes — every scope wrapper must carry an explicit tag.
- **Flat-View Ordered Matching:** When a class materializes, its visible context is the original document with the descent-path keys popped (each ancestor's wrapper key is replaced in place by its kwargs). Matching uses the receiving class's accept-list; values are applied in document order; **last write wins**. There is no "explicit kwargs > broadcast" priority — every source (own kwargs, sibling broadcasts, class-name blocks) takes its slot at its YAML position. Receivers are located in the parent's ambient view via Python identity (Fluid `is` check; class-marker dicts are also identity-preserved through `deep_merge`).
- **Serialization Symmetry:** `dump()` followed by `load()` MUST reconstruct an identical object graph. Round-trip fidelity is non-negotiable.
- **Type Safety:** Strict mypy enforcement (`disallow_untyped_defs=true`). All public APIs must have complete type annotations.

## Testing & Validation
- **Round-Trip Tests:** Every new feature MUST include a test that dumps and reloads the configured object graph.
- **Registry Cleanup:** Tests MUST use `setup_registry()` fixtures to clear global state between runs.
- **Line Length:** 120 characters (Black, isort, flake8).
