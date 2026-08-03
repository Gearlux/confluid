# Changelog

All notable changes to confluid are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [Unreleased]

## [0.3.0] — 2026-08-01

### Added

- **`accepts_any_key(target)` — the third settability predicate.** `accepts_key`
  and `accepts_broadcast` answer *"may this key land here?"*; this one answers
  the prior question, *"does this target discriminate between keys at all?"*. It
  is `True` for a `**kwargs` constructor, where both of the others return `True`
  for **every** key — including keys the class has never heard of.

  ```python
  class Forwards:
      def __init__(self, **kwargs): ...

  accepts_key(Forwards, "run_name")   # True — it cannot refuse it ...
  accepts_any_key(Forwards)           # True — ... because it has no accept-list
  ```

  An external config front-end needs the difference to decide how to *deliver* a
  key: writing it into a marker's own kwargs is the ADDRESSED channel, so it
  becomes a **constructor argument**, and that claim is only justified when the
  class declares the key. One a target merely cannot refuse must be left to
  cascade, landing as a post-init attribute — the same split `flow()` already
  applies to a document's own keys. Without the distinction, a CLI `--run_name x`
  reaches a metric's constructor and a strict library rejects a keyword it never
  asked for, from a call site nowhere near the config. See
  [docs/broadcasting.md](docs/broadcasting.md) → "Declaring a key vs being unable
  to refuse it".

- **`flow(node, *args, **kwargs)` — positional runtime injection.** A marker
  carries keyword arguments only (that is all a YAML tag can express), but the
  constructor being deferred is somebody else's, and a variadic signature has no
  keyword for its inputs at all. Such a slot could not be deferred: the object
  had to be built inline, and every knob beside the inputs became unreachable
  from config.

  ```python
  class Loaders:
      def __init__(self, *loaders, device=None): ...

  slot = LazyClass(Loaders, device="cuda")   # the config owns the knobs
  flow(slot, train_dl, valid_dl)             # the run supplies the inputs
  ```

  Positional args are runtime-only — never stored on a marker, never emitted by
  `dump()` — the same status the `params=` / `dataset=` kwargs already had. They
  suppress `Instance` memoization (they override the stored spec), are dropped
  for an already-live object (matching the runtime-kwarg convention), and raise
  `ConstructionError` for a registry-configurable bare type, which materializes
  through a synthesized marker.

  `_ctor_params` now excludes `VAR_POSITIONAL` names: `inspect.signature` lists
  `*loaders` under the name `loaders`, so a config key of that name used to pass
  the constructor-kwarg filter and reach the call as a keyword, where Python
  rejects it.

- **`discover_dimension_values(config)`** — every keyed scope dimension in a raw
  document mapped to the values it offers:

  ```python
  from confluid import discover_dimension_values, load_config

  discover_dimension_values(load_config("experiment.yaml"))
  # {"task": {"classification", "segmentation"}, "model": {"convnet"}}
  ```

  Takes the **raw** document — by the time `load()` returns, the blocks have been
  spliced away. `discover_dimensions` (keys only) is now derived from the same
  walk rather than traversing separately.

  A dimension declared only by `!notscope:` blocks maps to an **empty set**: it is
  a real dimension a CLI must bind, but a negation is activated by every value
  except the one it names, so there is nothing to *select*.

- **`framework=` — a third orthogonal discovery axis** on `@configurable`,
  `register()` and `register_class()`, indexed like `task`/`role`
  (`list_classes(framework=…)`, `list_frameworks()`).

  `task` and `role` say what a class is *for*; neither says a `torch.nn` loss
  cannot be handed to a Keras trainer. A discovery consumer asked for "a
  classification loss" therefore offers both, and the mismatch surfaces as a
  type error far from its cause. `framework` is what makes such a picker
  offerable:

  ```python
  @configurable(task="classification", role="loss", framework="torch")
  class FocalLoss: ...

  list_classes(task="classification", role="loss", framework="keras")
  ```

  - The unit is the **API, not the tensor runtime** — a `keras.losses.Loss` is
    `"keras"` whether Keras runs on TensorFlow, JAX or PyTorch, because the API
    is what decides whether a trainer can consume it.
  - Deliberately **not** folded into `category`, which stays `f"{task}_{role}"`.
  - Follows the same fallback template as the other marks, so a partial
    re-register never drops it.
  - Works for `register()`-ed **functions** too (builder factories have no base
    class, so type inference cannot cover them — the reason this is an explicit
    tag rather than something derived from the MRO).
  - Untagged classes are absent from the index: a `framework` filter returns
    only what explicitly claims that engine. Probe without it to see everything.

- **A registered NAME may map to more than one class.** Two classes may share a
  name when any discovery tag tells them apart — the same op implemented per
  framework (`group="fft/numpy"` vs `"fft/torch"`), or a library publishing one
  name as both a loss and a metric (`role="loss"` vs `"metric"`). Previously the
  second registration silently replaced the first: it vanished from every
  picker, and which one survived depended on import order.

  ```python
  get_registry().get_class("FourierOp", group="fft/torch")   # tag-filtered lookup
  get_registry().get_class("myops.torch.FourierOp")          # ...or the dotted key
  ```

  - `get_class` takes the same five filters `list_classes` intersects.
  - `list_classes()` returns the bare name while it is unambiguous and the
    canonical dotted key once a name is shared, so the enumerate-then-look-up
    idiom keeps working AND both classes become reachable.
  - `ConfluidRegistry.key_for(cls)` reports the key a class is published under;
    `dump()` uses it, so a round-trip reloads the same class rather than a
    namesake.

- **`AmbiguousClassError`** (a `ConfigurationError`, and so a `ValueError`) —
  raised when a bare lookup names several classes and nothing narrows the
  choice. A sibling of `UnknownClassError`, not a subclass: code catching
  "unknown" to fall back to an import must not swallow "ambiguous". The message
  lists every candidate with the tags that separate them.

- **Tag selectors in a target** — `!class:FourierOp@group=fft/torch`, composable
  with inline kwargs and `!lazy:`. A selector value written `$key` is read from
  the loaded configuration (`!class:Loss@framework=$engine`), so a document
  states its engine once and every ambiguous target follows it — including
  targets nested inside another marker's block, which load-time `${...}`
  interpolation cannot reach. An unknown axis is rejected, not ignored.

### Changed

- **An active keyed scope must name a value the document declares** — otherwise
  `load(..., scopes=[...])` raises `ScopeError` listing the values that do exist.

  ```
  ScopeError: No scope block matches task='classifcation'. This document declares
  task with: classification, segmentation. Either use one of those values, or add
  a `!scope:task=classifcation` block.
  ```

  Previously a value matching no block resolved to the document's *unscoped* keys,
  so a typo ran the default configuration and reported nothing — indistinguishable
  from success until the outputs were inspected.

  The rule is narrow, and three cases are deliberately unchanged: an **undeclared**
  dimension stays an inert no-op (a CLI may pass a dimension a config has not grown
  into yet), an **unset** dimension resolves to the defaults as before, and a
  dimension carrying **any** `!notscope:` block accepts every value (both matching
  and differing are meaningful there, so nothing can be rejected).

  **Breaking** for a config that was relying on an unmatched value falling through
  to its defaults. The fix is to declare the value as a block — including for a
  "default" variant a CLI names unconditionally, which is now a checkable value
  rather than a silent no-op.

- **A bare lookup of a shared name now raises** instead of returning whichever
  class registered last (`get_class`, and `resolve_class(strict=True)` — which
  the construction path uses). `resolve_class` stays non-raising by default, so
  introspection callers that already treat `None` as "not introspectable" are
  unaffected.
- **`list_classes()` returns dotted keys for a shared name** (bare names are
  unchanged for every unique one).
- `ConfluidRegistry._classes` is gone, replaced by `_entries` (name → entries)
  and `_by_key` (canonical key → entry). The five tag indices now hold entry
  keys rather than names.
- **`to_pydantic` keeps the element type of a re-iterable collection slot.**
  `Sequence[X]`, `MutableSequence[X]`, `Collection[X]`, `Container[X]`,
  `Mapping[K, V]` and `MutableMapping[K, V]` now generate a field of that type
  instead of a bare `Any`, so a slot annotated to say what it holds actually
  says so in the generated schema — the JSON-Schema / form-spec surface saw
  `Any` for every one of them before.

  ```python
  class Runner:
      def __init__(self, metrics: Optional[Sequence[Metric]] = None): ...

  to_pydantic(Runner).model_fields["metrics"].annotation
  # was: Optional[Any]        now: Optional[Sequence[Metric]]
  ```

  `Iterable`, `Iterator`, `Generator` and the three async kinds are still
  coerced to `Any`, and the split is the point: pydantic validates those lazily,
  wrapping the input in a one-shot `ValidatorIterator` whose second iteration
  yields nothing, so keeping their element type would trade a schema detail for
  silently-empty collections. The six above validate into a real `list` / `dict`
  holding the identical element objects, so they never had that problem.

### Fixed

- **A `**kwargs` constructor no longer drops every runtime kwarg.** `_ctor_params`
  reports the VAR_KEYWORD parameter's own name, which is truthy — so `flow()`'s
  constructor-kwarg filter kept only keys literally named `kwargs` and built the
  target with nothing:

  ```python
  class Forwarding(SomeBase):
      def __init__(self, **kwargs): super().__init__(**kwargs)

  flow(LazyClass(Forwarding), model=net, args=training_args)
  # was: Forwarding()  -> "requires either a `model` or `model_init` argument"
  # now: Forwarding(model=net, args=training_args)
  ```

  What such a constructor receives follows the **addressing** — the same split
  the accept-list predicates draw:

  | The key | Reaches |
  |---|---|
  | written on the marker (`!lazy:Forwarding(tag=x)`), or in a block naming it | the constructor |
  | injected at flow time (`flow(node, model=…)`) | the constructor |
  | bare, cascading (a top-level `name:`) | a post-init attribute |

  A `**kwargs` class has no accept-list to filter with, so *every* bare key in
  the document reaches it — passing those on would turn permissive broadcasting
  into "called with whatever the document happens to contain". Nothing is applied
  twice: the post-init step now gates on what the constructor actually received
  rather than on the declared parameter names.

- **`flow()` now solidifies an ALREADY-LIVE object.** The documented promise —
  "domain code does not need to manually trigger solidification, `flow(model)`
  handles it transparently" — held only on the marker path: the hook ran after
  *construction*, below `flow()`'s already-live early return. An object built
  eagerly (`!class:Model()`) or handed in from Python came back with its lazy
  state never finalized, and nothing raised; the failure surfaced elsewhere, as
  an optimizer flowed with `params=model.parameters()` receiving an empty
  parameter list.

  `flow(obj, solidify=False)` still suppresses the hook, live objects included.
  Because a live object may be flowed repeatedly, `solidify()` is now expected
  to be idempotent (build once, cache, return the cache) — the convention the
  lazy-initialization rules already require.

- **A duplicate registration with nothing to tell it apart now warns.** Same
  name, same tags, different class: last-write-wins is preserved, but it is no
  longer silent. Re-registering the SAME class object stays silent, since a
  snapshot restore does that on every bootstrap.
- **`register_class(cls)` is idempotent after a `name=` override** — `name` now
  falls back to the mark the class already carries (read from its OWN
  `__dict__`, so a subclass is never registered under its parent's custom name).
  A partial re-register used to mint a second entry under the short name.
- **A dotted `!class:` target now matches its class-name block.**
  `!class:pkg.mod.Widget` set the block-match name to the dotted string, so a
  `Widget:` block silently did not apply and the value stayed at its
  constructor default. This also aligns the load path with `configure()`, which
  has always matched on the registered name.
- **A re-registration that changes a tag no longer leaves the class indexed
  under the old value.**

## [0.2.0] — 2026-07-27

### Added

- **`accepts_key(target, key)` / `accepts_broadcast(target, key)`** — the two
  settability predicates the engine already used internally, now public. They
  answer "may this key set this attribute on this target?" so an external
  config front-end (a CLI layer turning `--lr 0.1` into a config change) gets
  the SAME answer a YAML key gets, instead of re-deriving an accept-list that
  drifts.
  - `accepts_key` covers ADDRESSED writes — a `ClassName:` block, an exact
    dotted path, a marker's own kwargs, a `configure()` block — and is gated by
    the accept-list alone: constructor params, public settable class
    attributes, `__init__`-body slots (scan ∪ declaration ∪ bake), and a
    `**kwargs` constructor accepting everything.
  - `accepts_broadcast` is the stricter BARE-key question: the accept-list
    MINUS the two broadcast opt-outs, `@configurable(broadcast=False)` on the
    class and `NoBroadcast[T]` on the parameter.
  - Both accept a class, a live instance, or the dotted string a `!class:`
    marker carries; an unresolvable target accepts nothing.
  - A consumer that hand-rolled its own accept-list was silently bypassing both
    opt-outs — that is the failure this exports to prevent. See
    `docs/broadcasting.md` → "Asking whether a key may land".


## [0.1.0] — 2026-07-18

_First public release, published to PyPI as `confluid` (tag `v0.1.0`)._

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

- **Real-world scenario examples.** [`examples/ml_experiments/`](examples/ml_experiments/README.md)
  — a Hydra-style ML experiment suite (config groups via `!scope:` dimensions,
  `include:` experiment overlays, `!class:`/`!ref:`/`!lazy:` wiring, bare-key
  global knobs, `dump()` reproducibility round-trip) — and
  [`examples/deep_injection.py`](examples/deep_injection.py) — the gin-config
  pitch: a four-level component tree configured with zero parameter-threading
  code. Directory examples (`examples/*/run.py`) are executed by CI alongside
  the flat scripts; `examples/modular_includes/run.py` was fixed in passing
  (it loaded a config-block document as an object and used a CI-hostile
  relative path).

- **The feature-complete core.** Hierarchical config/DI, YAML tag IR
  (`!class:` / `!ref:` / `!clone:` / `!lazy:` / `!scope:` / `!notscope:`),
  broadcasting with flat-view ordered matching, scopes, pydantic schema export
  (`to_pydantic`), validation policy, dump/load round-trip.

- **XDG config-file search paths.** A relative path handed to `load()` /
  `load_config()` — and every relative `include:` entry — now resolves
  through ordered tiers, local first, XDG last: the including file's
  directory (includes only) → CWD → `./config/` → `$XDG_CONFIG_HOME`
  (default `~/.config`) → each `$XDG_CONFIG_DIRS` entry (default
  `/etc/xdg`). Under each XDG base dir the lookup is namespaced by the new
  process-wide app name (`set_app_name("my-app")` → `<base>/my-app/` then
  `<base>/confluid/`; unset → the bare base dir). New public API:
  `resolve_config_path`, `set_app_name`, `get_app_name`. Absolute paths are
  used verbatim; a total miss raises `ConfigFileNotFoundError` listing every
  searched location. `load_config_with_paths` records the resolved (possibly
  XDG) files. See `docs/search-paths.md`.

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

### Changed

- **`_View.copy()` / `_View.update()` preserve broadcast-scope tags.** The
  engine's scoped-view dict subclass previously degraded to all-BARE on a
  `copy()` (dict subclasses return a plain `dict`) — a latent silent-flattening
  trap for addressed/glob keys. `copy()` now returns a `_View` carrying the
  scope side-table, and `update()` applies last-write-wins to the tags (a
  `_View` source carries its tag over; an untagged source key clears an
  existing tag). Plain-dict syntax (`dict(view)` / `{**view}`) remains lossy
  by nature — construct a `_View` instead.
- **`**kwargs` constructors broadcast permissively — now documented and
  traced.** A `**kwargs` catch-all makes a class's accept-list unknowable, and
  confluid deliberately errs permissive: every bare / glob-delivered key
  broadcasts into such instances. This behaviour is now documented
  (`docs/broadcasting.md` → "Classes with `**kwargs` constructors", with the
  opt-outs as remedies) and announced once per class at TRACE level.
- **`Lazy[T]` and `Mandatory[T]` annotations are now typed:
  `Annotated[Union[T, Fluid], marker]`.** Subscript with the interface the
  slot eventually flows into — `optimizer: Lazy[Optimizer] = Class(Adam,
  lr=1e-3)`, `model: Mandatory[nn.Module] = Class(MyModel)` — which now
  type-checks under strict mypy: pre-flow the slot holds a deferred `Fluid`
  stub, and the union arm admits it (a `Class` *is* a `Fluid`). Previously
  the aliases were `Annotated[T, marker]`, forcing the `Lazy[Any]` /
  hand-spelled `Mandatory[Union[T, Fluid]]` workarounds; `Lazy[Any]` remains
  valid for genuinely open slots. `NoBroadcast[T]` deliberately keeps no
  `Fluid` arm (it gates broadcasting on scalar knobs). Marker detection is
  now recursive (`confluid.introspect.annotation_has_marker` walks nested
  `Annotated`/`Union` layers), so compositions work in either order
  (`Mandatory[Lazy[T]]` / `Lazy[Mandatory[T]]`) and `Optional[Lazy[T]] =
  None` is detected too. `to_pydantic` preserves non-marker metadata on
  nested `Annotated` layers, so a range mark composed with a marker alias
  (`Mandatory[DbPower]`) still reaches the JSON-Schema bounds and pydantic
  validation, container marks included. See `docs/tags.md` and
  `docs/io-contract.md`.

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
  `asyncio.to_thread`, streamstudio's run-worker thread runs under
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
