# confluid — backlog

Open work for this project. Cross-cutting / multi-project initiatives live in the
workspace root `TASKS.md`. Completed items are not archived here — git history is the record.

- [ ] JSON Schema Generation for IDE autocomplete @feature
- [ ] **`hidden_config` marker (external-feedback topic 3/4)** @feature — "settable via config but hidden from `--help`/`--docs` display": `__confluid_hidden__` stamp honored in the display enumerators (`get_hierarchy`/`get_hierarchy_from_instance`/liquifai `discovery.get_configurable_paths`) but NOT in the settable accept-list path.
- [ ] **Frozen-mode body-slot SCHEMA fields** @feature @low — `pydantic_export._post_init_field_specs` (the `to_pydantic` body-slot scan) still reads `scan_init_body` only, so a compiled/frozen deployment loses body-slot fields from form-spec/MCP schemas even when a bake table exists; adopt `confluid.introspect.baked_init_attrs` as the same empty-scan fallback the engine uses (baked names → untyped optional fields).
  - [ ] **I/O-contract follow-ups** @feature — (b) optionally expose a checkpoint-path output (needs marainer to persist the flowed `lightning_trainer`/checkpoint dir on `self`); (c) extend `@output` to source/op node builders if a non-runnable class ever declares outputs; (d) route `_build_source_node`'s required/optional split through `input_specs` for consistency; (e) richer results-panel rendering for non-scalar outputs (confusion-matrix heatmaps / figures), which today are logged to the Lightning loggers but dropped from the scalar panel.
- [ ] **Schema generation + validation constraints:** generate JSON schemas from `@configurable` classes and carry pydantic-style field constraints for hyperparameter safety. @feature
