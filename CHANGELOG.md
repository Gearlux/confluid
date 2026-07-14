# Changelog

All notable changes to confluid are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [Unreleased]

### Added

- **Configuration reports — applied / failed / unused override tracking.**
  `configure()` / `configure_from_file()` now return a `ConfigurationReport`
  (previously `None`): one `applied` record per attribute per object (the
  final last-write-wins assignment, with receiver label and origin — bare,
  named block, glob, addressed recursion, nested-class), `failed` keys
  (unknown attributes inside a matched block; per-field validation failures,
  with strict mode recording before re-raising and warn mode recording with
  the value still applied), and `unused` top-level document keys that
  matched nothing across the whole pass (glob blocks tracked per leaf, e.g.
  `**.lr`; one aggregate DEBUG summary per pass). The YAML materialization
  path reports too via the new `collect_report()` context manager — a
  nested `configure()` adopts the ambient report, so a load-then-configure
  pass aggregates into one report. Zero-cost when no report is active. See
  `docs/report.md`.
- **`capture=False` opt-out for the ctor-kwargs capture.**
  `@configurable(capture=False)` (also on `register()`) stamps
  `__confluid_no_capture__` and disables the `__confluid_kwargs__` capture on
  both paths — the validation wrap at direct Python construction and the
  engine stamp on the YAML flow path (which then also skips
  `__confluid_class__`). For classes whose constructor arguments are heavy,
  disposable objects that `__init__` transforms: without the opt-out the
  capture keeps them alive by reference for the instance lifetime. Trade-off:
  `dump()` omits transformed params (reload restores their defaults; a
  transformed required param makes the dump non-reloadable). See
  `docs/eager-classes.md` → "Opting out".
- **Performance baseline example.** `examples/performance.py` times the
  parse / `materialize` / `resolve` / `configure` phases over a synthetic
  ~2,500-marker tree so the scoped-broadcasting context-view overhead can be
  watched across engine changes; `CONFLUID_BENCH_PROFILE=1` adds a cProfile
  breakdown. Print-only — no timing assertions in CI. See
  `docs/performance.md`.

### Breaking

- **Scoped broadcasting — addressed keys are exact, cascade is opt-in.**
  A bare top-level key still broadcasts tree-wide (it is an implicit
  `**.key`), but an ADDRESSED key — dotted `trainer.lr: 0.001`, the nested
  block `trainer: {lr: 0.001}`, or a marker's own kwargs — now configures
  the matched node ONLY and no longer cascades to the node's descendants.
  Glob wildcards opt back into the cascade: `*` matches exactly one nesting
  level (`trainer.*.lr` = direct children), `**` matches zero or more
  (`trainer.**.lr` = trainer AND all its descendants — the declare-once
  form; quote keys that start with `*` in YAML). The first named path
  segment floats (`trainer.lr` ≡ `**.trainer.lr`, the classic block reach);
  segments after the first are strict one-level hops. Glob-delivered keys
  honour the `NoBroadcast[T]` / `@configurable(broadcast=False)` opt-outs
  like bare keys; exact addressed keys bypass them. Document-order
  last-write-wins remains the only priority rule — no specificity tiers.
  `configure()` follows the same rule over live objects. Configs that
  relied on a block's values reaching the addressed node's descendants must
  add a `'**'` segment (`trainer.**.lr`). See `docs/broadcasting.md`.

## [0.2.0] — 2026-07-11

### Breaking

- **Marker-dict IR removed.** The legacy `{"_confluid_class_": ...}` /
  `{"_confluid_ref_": ...}` dicts are no longer accepted by `flow()` /
  `materialize()` / `resolve()`. Fluid objects (`Class` / `Instance` /
  `Reference` / `Clone` / `Lazy`) are the only intermediate representation;
  synthesize markers with `Instance(cls_name)` + `.kwargs.update(...)`.
- **YAML tags parse only through confluid's own loader.** Tag constructors
  live on a private `ConfluidLoader(yaml.SafeLoader)` subclass; the global
  `yaml.SafeLoader` is never mutated, so a plain `yaml.safe_load` elsewhere in
  the process now raises on `!class:` / `!ref:` instead of silently building
  Fluids.
- **`configure()` follows flat-view, document-order, last-write-wins matching**
  — the same rule as YAML materialization. The old 4-candidate priority
  matcher is gone. Additionally: a present `null` value now SETS `None`,
  unknown non-dict keys in an object's own block log a warning, and property
  getters are never executed during configuration.
- **Public API pruned (`__all__` 72 → 60).** Internal machinery moved off the
  top level (still importable from home modules): `validate_kwargs`,
  `validate_setattr`, `override_init_mode` (`confluid.validation`);
  `normalize_active`, `parse_scope_arg`, `resolve_scopes` (`confluid.scopes`);
  `is_lazy_annotation` (`confluid.lazy`); `is_mandatory_annotation`
  (`confluid.mandatory`); `lazy_param_names_of` (`confluid.pydantic_export`);
  `ScopeBlock` (`confluid.fluid`); `load_workspace_env` (`confluid.env`).
- **`readonly_config` deleted.** Its mark was never enforced anywhere.

### Added

- **Eager (plain-constructor) classes are first-class.** `dump()` now
  round-trips a class whose constructor transforms its params instead of
  storing them verbatim: the bound constructor kwargs are captured at
  construction (`__confluid_kwargs__` — stamped by the engine on the YAML
  path and by the `@configurable` validation wrap on direct Python
  construction, even with validation `off`), and the dumper prefers the live
  same-named attribute with the captured kwarg as fallback. New
  `@configurable(eager=True)` / `register(..., eager=True)` stamp-only mark
  (`__confluid_eager__`): `configure()` warns when setting a constructor-param
  attribute on an eager instance post-construction (`__init__` work will not
  re-run — derived state may be stale; the value is still applied). See
  `docs/eager-classes.md`.
- `active_context(ctx)` — public contextmanager activating a resolution
  context for bare `flow()` calls outside a `materialize()` pass (the
  semantics liquifai used to hand-roll by reaching into engine internals;
  its `_confluid_active_context` now delegates here). The mapping is used
  verbatim when it has no dotted keys (live objects keep identity); dotted
  keys are expanded like `materialize`. See README "Using confluid across
  threads & async".
- Broadcast opt-out: `NoBroadcast[T]` (param-level Annotated marker) and
  `@configurable(broadcast=False)` (class-level) — bare top-level keys no
  longer land on opted-out targets; addressed `ClassName:` blocks and
  `configure()` are unaffected. Marker stripped from generated schemas.
- Broadcast trace diagnostics: every accepted broadcast/block-unroll logs
  `broadcast: <key> -> <Class> (<origin>)` at TRACE
  (`LOGGAIR_CONSOLE_LEVEL=TRACE` to see them).
- `confluid-bake <package>` (also `python -m confluid.bake`) — build-time AST
  bake for compiled/frozen/zip deployments: runs the same `__init__` body-slot
  scan the engine uses at runtime, while source still exists, and emits a
  generated `<package>/_confluid_baked.py` table (provenance-headed,
  deterministic; every class the package defines with its own `__init__`,
  in-package MRO bases included). The engine unions it in per MRO class when
  the live scan finds nothing (`scan ∪ declared ∪ baked` — fresh source always
  governs dev), so broadcasting keeps seeing post-init attrs where
  `inspect.getsource` fails. `--check` is the CI drift guard (exit 1 on a
  stale table). PyInstaller-style tracers need `<pkg>._confluid_baked` as a
  hidden import (lazy dotted import).
- `@configurable(broadcast_attrs=[...])` — explicit declaration of post-init
  `__init__`-body broadcast attrs (stamped `__confluid_broadcast_attrs__`,
  UNIONED with the AST scan), the manual override for classes the bake step
  can't reach. The engine warns once per class when it can't scan a
  `@configurable` class covered by neither mechanism
  (`confluid.introspect.init_source_available` is the new probe distinguishing
  "no source" from "no assignments").

- Typed exception hierarchy (`confluid.exceptions`, root `ConfluidError`);
  every class dual-inherits the builtin it replaces, so existing
  `except ValueError:` call sites keep working.
- `${key.path}` config-key string interpolation: a dotted/bracketed name in
  `${...}` resolves against the config tree (`${train.dataset}`,
  `${items[0]}`, `${db.port:5432}`); a plain name stays an env var.
- `configure_from_file(*instances, path)` — one-call load + apply.
- `@configurable` / `register` accept plain builder **functions**; a
  `@configurable` function's call is validated like a class constructor.
- Unified dotted-path resolution: one tokenizer/walker with structural and
  object policies behind `resolve_reference_path` (multi-level attribute
  walks and `packs[0].name` combos now resolve).
- README documentation for `cast` (the typed materializer for static
  checkers), the `${...}` interpolation family, and loader-directive notes.

### Changed (internal — no public API change)

- Logging is loggair-only (the stdlib/loggair split is gone); `%`-style log
  args converted to f-strings (loguru drops printf args silently).

- **Module layering:** the materialization engine (`flow`/`cast`,
  `materialize`/`resolve`, broadcasting, accept-lists, the engine state)
  moved to `confluid.engine`, breaking the old `fluid`↔`loader`
  import cycle; `fluid` is now a pure marker-class leaf and `loader` is
  YAML-only. Deep imports from `confluid.loader` / `confluid.fluid` keep
  working via re-exports (PEP-562 for `fluid.flow`/`fluid.cast`).
- **Engine state migrated from `threading.local` to a `contextvars.ContextVar`**
  (a frozen `_EngineState` dataclass, set/reset by token): an active
  materialization context now propagates into asyncio tasks and
  `asyncio.to_thread` workers (previously `!ref:` resolution silently failed
  inside an event-loop task). A raw `Thread`/`run_in_executor` still needs
  `contextvars.copy_context().run(...)` or `active_context` in the worker.
  The loader's include-accumulator moved to its own ContextVar; the private
  `_state` re-export from `confluid.loader` is gone. Downstream boundary
  fixes shipped in the same change: navigaitor's in-process trainer uses
  `asyncio.to_thread`, fluxstudio's run-worker thread runs under
  `copy_context()`.
- **Stamping single source of truth:** `registry.register_class` stamps every
  `__confluid_*__` mark (widened with `random`/`constant`/`strict_typing`/
  `display_name`/`no_broadcast`/`broadcast_attrs`, each with the
  existing-mark fallback), and `@configurable` delegates its whole mark set —
  a `register_class`-ed third-party class can now carry every mark, and a
  partial re-register never drops marks.
- **`flow()` decomposed** from one ~390-line function into a dispatcher +
  named `_flow_*` phase helpers (behavior byte-identical).
- **One AST scanner:** the three near-identical `__init__`-body scanners are
  unified in stdlib-only `confluid.introspect` (`scan_init_body` + three
  projections); the wraps-transparency dependency is now pinned by a test.

### Fixed

- `dump()` no longer silently drops a constructor param explicitly holding
  `None`: it now dumps `param: null` unless the param's default is also
  `None` (where the omission is lossless). The old unconditional skip made a
  reload silently restore the non-`None` default.
- `configure()` no longer executes property getters, can set `None`, and
  warns on typo'd block keys.
- Order-dependent test failures caused by global YAML-loader mutation.
- An inherited `cfg.items` → `dict.items` method leak in dotted-ref
  resolution (dict-key lookup always wins on containers).

## [0.1.0] — baseline

Initial feature-complete release: hierarchical config/DI, YAML tag IR
(`!class:` / `!ref:` / `!clone:` / `!lazy:` / `!scope:` / `!notscope:`),
broadcasting with flat-view ordered matching, scopes, pydantic schema export
(`to_pydantic`), validation policy, dump/load round-trip.
