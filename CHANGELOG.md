# Changelog

All notable changes to confluid are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [Unreleased]

## [0.3.0] — unreleased (tag deliberately held pending downstream verification)

### Changed

- **`confluid.state` and `confluid.broadcast` split out of `confluid.engine`.**
  The layering is now `fluid → state → broadcast → engine → loader`. `broadcast`
  owns the one precedence rule and its machinery — scope tags, the tagged view,
  the ordered merge, the child-view splice, the accept-lists, and the three
  `accepts_*` predicates; `state` holds the engine ContextVar and exists so
  `broadcast` can read the ambient report without importing the engine.

  The reason is not file size. The rule was implemented twice — in `engine` and,
  over live objects, in `configurator` — and the two copies diverged four separate
  ways, three of them silently. One module both callers import is what stops the
  fifth. `broadcast` deliberately materializes nothing, which is what keeps the
  dependency one-directional.

  **No import with users breaks.** Every moved name that HAD an importer is
  re-exported from `confluid.engine`, so `from confluid.engine import
  accepts_key` (and the internal `_prepare_kwargs` / `active_context` / …
  spellings) keeps working; zero-user private names are pruned instead (eight
  so far — see the Internal and Removed sections). New code should import from
  the real home. One caveat for test suites: broadcast
  diagnostics now log from `confluid.broadcast`, so a monkeypatched logger must
  target that module.

### Changed (behavior)

- **`get_hierarchy` reports a declared `name` constructor param (2026-08-09
  adjudication).** The class walker skipped `name` while the live-instance
  walker, `to_pydantic`, `input_specs` and the settability predicates all
  report it — so a CLI's report view showed the row only when the config
  happened to be flowed, and shell completion never offered `--Cls.name`
  although the override machinery accepts it (the "settable but undocumented"
  trap). Both walkers (and the callable branch) now agree: a row appears for
  targets that genuinely DECLARE the param. Display surface only — nothing
  about bare-`name:` broadcasting changed; the `NoBroadcast[T]` guidance for
  generically-named knobs stands. Pin:
  `tests/test_schema_from_instance.py::test_both_walkers_report_a_declared_name_param`.

### Added

- **`register()` carries the accept-list controls the decorator does** —
  `broadcast=False` and `broadcast_attrs=[...]`. They existed only on
  `@configurable`, which had it backwards: a class you own can be shielded from
  cascade keys by declaring parameters or adding a decorator argument; a class you
  do **not** own can be shielded by neither. A third-party constructor taking
  `**kwargs` has no accept-list, so confluid errs permissive and every bare key in
  the document reaches it — a choice its author never made, having never seen
  confluid. `register()` is the one place the person wiring it up can decide.

  ```python
  register(SomeLibraryClass, name="Sink", broadcast=False)     # no bare key cascades in
  register(Frozen, name="Frozen", broadcast_attrs=["slot"])    # body slots in packaged mode
  ```

  `broadcast_attrs` unions with the AST scan exactly as on the decorator, so
  declaring can never lose a scanned name.


- **A DEBUG line when a value is overridden.** The merge's single write path now
  reports a key whose value is REPLACED, with both sides and which scope won:

  ```
  override: 'lr' 0.5 -> 0.9 (exact value replaced by a bare one;
                             document order decides — the later spec wins)
  ```

  Overriding is normal operation — a sweep's `lr:` reaching every node is the
  point of bare keys — so this is DEBUG, not a warning, and an uncontested value
  is silent. It exists because every configuration defect fixed in this release
  looked identical from outside: the run used a value the author did not write at
  the node they wrote it on, with nothing in the log. "My knob did not take" is
  now one grep (`LOGGAIR_CONSOLE_LEVEL=DEBUG`) instead of a bisect.

### Removed

- **`ConfluidRegistry.register_object` / `get_object` and the `_objects`
  store.** A write-only feature: no resolution path — not `!ref:`, not
  `!class:`, not `resolve_class` — ever consulted the store, so a "registered"
  object could never be reached from a config. Zero consumers workspace-wide
  (Python, YAML, and notebooks all grepped); the only callers were two
  put-then-get round-trip tests, deleted with it.

- **`introspect.init_setattr_annotations`.** The `to_pydantic` body-slot
  typing projection it was built for consumes `scan_init_body` directly, which
  orphaned it — its only remaining caller was its own test.

- **Three never-imported re-exports dropped from `confluid.engine`**
  (`_broadcast_blocked_keys`, `_get_param_kinds`, `_same_target`) — same
  criterion as the five pruned 2026-08-08: zero users anywhere, engine's own
  body included; the real homes in `confluid.broadcast` are untouched.

  (Considered and deliberately KEPT despite zero Python callers:
  `confluid.env.load_workspace_env` — it has a real notebook consumer, which a
  `.py`-only grep misses. Audits claiming "zero consumers" must grep
  notebooks too.)

### Internal

- **One grammar per concept in the introspection/grammar layer (2026-08-09).**
  Three duplications the one-scanner plan's audit found one layer below
  `broadcast`, each folded to a single implementation: (1) the dotted-key
  expansion — `merger.expand_dotted_keys` (document top level) and
  `broadcast._expand_block_keys` (in-block) are now the ONE
  `merger.expand_dotted_mapping` under two copy-policy hooks (deep-copy with
  Fluid identity + `deep_merge` vs share-by-reference + shallow last-write),
  which also propagates the fresh-head position anchoring to in-block dotted
  keys; (2) the `Target(...)` call grammar — `_TARGET_CALL_RE` moved to
  `resolver` (with the `_split_inline_pairs` k=v splitter) and
  `resolver._parse_class_string` matches it instead of a hand-rolled laxer
  split, so the quoted-string form accepts exactly the spellings the tag form
  does (an illegal name now yields a deferred `Class` marker rather than an
  eager `Instance` under a name the registry can never resolve); (3) the
  annotation-marker scan — `introspect.marked_param_names` (see Fixed).
  Value-coercion policies (tag `parse_value` vs quoted context-resolve) are
  deliberately NOT unified — they differ by design and stay at their callers.

- **The two precedence-rule drivers now share ONE walk** (phases A2/A3 of the
  one-scanner plan; docs/architecture.md record 8). `broadcast._scan_view`
  owns the main loop and the five-branch block ladder that previously existed
  byte-for-byte in both `_prepare_kwargs` and `configurator._apply`; the
  paths differ only in a declared receiver (`_receiver_for_target` /
  `_receiver_for_instance`, side by side, every kept difference pinned in
  `tests/test_cross_path_pins.py`) and an effect-writing sink (`_MergeSink` /
  `configurator._LiveSink`). Behavior-preserving, proven by replaying a
  verbatim copy of the old implementation against every real invocation over
  an 18-document corpus (deleted with the phase); scanner branch pins live in
  `tests/test_scanner.py`. Measured: materialize within 1% of baseline,
  configure() ~19% faster.

- **The splice pair lives in one module over shared primitives** (phase B,
  closing the one-scanner plan). `configurator`'s `_spliced` family moved to
  `broadcast` (renamed `_spliced_subtree_view` / `_spliced_at_slot` /
  `_hoist_block_routing`) beside the marker splice; the three byte-duplicated
  idioms are now single functions — `_merge_rider` (the `'**'` merge),
  `_merge_routing` (the routing hoist), `_spent_at_boundary` (the
  one-level-spend rule). One adjudicated policy change rode along (D1): a
  routing hoist now merges into an existing ROUTING entry only and REPLACES
  anything else — the old marker-path variant merged unconditionally, folding
  an addressed slot value into child routing so its contents leaked to
  descendants they were never aimed at (latent; no suite trigger existed).
  Pinned in `tests/test_scanner.py`.

- **Dead compatibility shims pruned (private names, zero users verified
  workspace-wide).** Five never-imported re-exports dropped from
  `confluid.engine` (`_classify_annotation`, `_scope_of`, `_settability_target`,
  `_warn_if_init_unscannable`, `_warned_unscannable_inits`); `confluid.loader`'s
  blanket compat block reduced to its one real dependency (`materialize`);
  `confluid.fluid.__getattr__`'s `cast` arm removed (`flow` stays — it has a
  downstream consumer); the uncalled `pydantic_export._unwrap_annotated`
  deleted. Public API is untouched.

- **Cache ownership follows module ownership.** The engine's parent-attr
  blacklist now rides its own `engine._parent_blacklist_cache` (cleared per
  materialize/resolve pass like the others) instead of squatting in
  `broadcast._post_init_attrs_cache` under suffixed `#parent_blacklist` keys —
  the arrangement contradicted broadcast's stated cache ownership.

- **Three small duplications folded to one implementation each:** the dumper's
  target-name spelling (three verbatim copies → `_target_name`), the
  `KEY=VALUE` scope split (`loader._parse_scope_suffix` now delegates to
  `scopes.parse_scope_arg`), and `configurator`'s eleven function-local
  import sites hoisted to the module top (no cycle ever required them).

- **Architecture records 6 and 7 added** (`docs/architecture.md`): why
  precedence is discriminated by `Fluid._order_resolved` and never `_yaml_loc`,
  and why `${...}` interpolation burns in at load for every spelling (with the
  rejected late-bound/copy-on-write alternatives on record).

### Fixed

- **Rider content reaches a declared deferred slot on BOTH paths, BOTH value
  shapes (the D5 adjudication, 2026-08-09).** The 2×2 was crossed: for a
  trainer with `self.optimizer = LazyClass(AdamW, lr=0.001)`,
  `'**.optimizer.lr': 0.01` (rider MAPPING) tuned the slot under `configure()`
  and was silently ignored under `load()` — the kept difference D5 — while
  `'**.lr': 0.01` (rider SCALAR) tuned it under `load()` and was silently
  ignored under `configure()`, an uncatalogued mirror in no pin (the
  deferred-slot cascade sat outside the one-scanner audit's walk). Both
  failing cells failed identically: the run silently used a value the author
  overrode. Now: both receivers' `dict_slot` admit a gated/floating dict at a
  DECLARED key (gated deliveries respect the NoBroadcast opt-outs — each
  shield gates its own key: the slot param's shield refuses the rider
  mapping, the target param's shield refuses the rider scalar), and
  `configure()`'s deferred tuning flattens the `'**'` rider into its pool via
  the same `_broadcast_pool` the engine uses. Along the way
  `_broadcast_pool` became scope-preserving: flattening to a plain dict
  erased the `_View` tags exactly when a rider was present, making an
  ancestor's addressed (EXACT) values cascade-eligible for descendants'
  deferred slots. Pins: the spelling×path matrix + NoBroadcast pin in
  `tests/test_cross_path_pins.py` (the old D5 twins, which ENFORCED the
  divergence, are replaced by it); example:
  `examples/broadcasting.py::rider_content_reaches_deferred_slots`.

- **The marker helpers and `input_specs` read a builder FUNCTION's own
  signature.** `lazy_param_names` / `mandatory_param_names` reached for
  `getattr(target, "__init__")` — on a function that is `object.__init__`
  (`*args, **kwargs`) — so an identical `Lazy[...]` / `Mandatory[...]`
  annotation was reported on a class and silently EMPTY on a registered
  builder function (measured: `{'model'}` vs `set()`), and `input_specs`
  reported an empty contract for every function target. All now dispatch
  through `introspect.init_callable`; the scan-plus-cache itself is ONE
  helper, `introspect.marked_param_names`, replacing three near-identical
  copies that had already drifted on exactly this. Pins:
  `tests/test_lazy.py::test_lazy_param_names_reads_a_builder_functions_own_signature`,
  `tests/test_io_contract.py::test_input_specs_and_mandatory_read_a_builder_functions_signature`.

- **`get_hierarchy` finds docs kept at class level.** The class walker read
  `__init__.__doc__` alone while the instance walker fell back to the class
  docstring, so a class keeping its `Args:` block at class level (the common
  convention) had help text in one hierarchy and none in the other. All four
  docstring-resolution copies (`get_hierarchy`, `get_hierarchy_from_instance`,
  `to_pydantic`, `parse_param_docs`) now ARE `parse_param_docs` — whose
  docstring always claimed `to_pydantic` resolved "the same way"; now it is
  the same code. Pin:
  `tests/test_schema_from_instance.py::test_both_walkers_find_docs_kept_at_class_level`.

- **The direct-flow `'**'` receiver application runs the ONE cascade gate.**
  `_pop_glob_routing` carried an inline copy of the bare-key gates that was
  strictly weaker than `merge_bare_pool_into_kwargs` — no list skip, no
  Fluid declared-key requirement, no `_same_target` self-broadcast guard — so
  a list-valued or Fluid-valued `**.key` behaved differently on the
  direct-flow path than on the materialize path (the same drift class the
  D2/D3 adjudication closed for `configure()`). Both halves are now the one
  function; glob contents still feed the nested-marker pool unchanged. Pin:
  `tests/test_broadcast_robustness.py::test_pop_glob_routing_applies_the_one_cascade_gate`.

- **A top-level dotted address orders at the position it was WRITTEN.**
  `expand_dotted_keys` created a missing head key by plain assignment — i.e.
  APPENDED at the end of the document — so `Model.layers: 3` written FIRST
  still beat a marker's own `layers=10` below it, while the supposedly
  identical `Model: {layers: 3}` in the same position lost. One documented
  rule, two answers, split by spelling. A fresh head is now anchored at the
  dotted key's own position; a head that exists as a real key keeps its own
  position, unchanged. Pins: the fresh-head pair in
  `tests/test_document_order.py`.

- **The legacy colon-free `!class` spelling accepts a kwarg named `target`.**
  `class_compat` was the one tag constructor still building its marker via
  `Instance(name, **kwargs)` instead of `_make_fluid`, so
  `!class Widget(target=x)` raised `got multiple values for argument
  'target'` on a config the modern `!class:` form loads fine. Pin:
  `tests/test_loader.py::test_kwarg_named_target_survives_the_legacy_class_spelling`.

- **The override DEBUG diagnostic can no longer crash the merge.** `_View.set`
  decided "did this write change the value?" with a bare `!=`, which raises on
  any value whose comparison returns a non-boolean (a numpy/torch array's
  `__eq__` returns an ARRAY) — a `ValueError` inside the merge's single write
  path, far from the config that caused it. Comparison failures now fall back
  to identity: a false "changed" on an equal-but-distinct array costs one
  DEBUG line. Pin:
  `tests/test_scanner.py::test_view_set_override_diagnostic_survives_array_valued_writes`.

- **`configure()`'s deferred-slot tuning now applies the same cascade gates as
  the load path.** The two copies of the bare-pool merge had drifted apart
  twice: a bare LIST value tuned a deferred slot on the configure path only
  (`stages: [1, 2]` silently rode into an optimizer marker's kwargs), and the
  Fluid gates — declared-key-only (never the `**kwargs` catchall) plus the
  `_same_target` self-broadcast guard — existed on the engine path only. Both
  paths now call the ONE cascade function,
  `broadcast.merge_bare_pool_into_kwargs`; the ordering verdicts stay
  per-caller by design (the engine's per-pass `_order_resolved`, configure()'s
  call-scoped `beaten` set). Pins: the D2/D3 group in
  `tests/test_cross_path_pins.py`, alongside difference-pins for the
  documented KEPT cross-path behaviors (D4/D5) so future drift in either
  direction fails a named test.

- **The settability predicates read a builder FUNCTION's own signature.**
  `accepts_key` / `accepts_broadcast` / `accepts_any_key` answered True for
  EVERY key on a registered builder function (measured: `accepts_key(builder,
  "run_name")` on a builder declaring only `weights`/`num_classes`): the
  accept-list machinery read `target.__init__` unconditionally, which for a
  function is `object.__init__` — `(*args, **kwargs)` — so every function
  target looked like a `**kwargs` constructor. That defeated, for function
  targets, the exact stray-CLI-key failure `accepts_any_key` exists to
  prevent. The class-vs-callable dispatch every signature reader must make now
  lives in ONE helper (`introspect.init_callable`), used by the accept-lists,
  the param-kind scan, and the engine's constructor filter alike.

- **The per-class marker caches no longer leak across the MRO.**
  `lazy_param_names` / `mandatory_param_names` / `no_broadcast_param_names`
  read their cache with `getattr`, which walks the MRO: a subclass queried
  after its parent returned the PARENT's stamped answer (measured: a subclass
  declaring `opt: Lazy[Any]` reported an empty set), and in the other
  direction a subclass overriding `__init__` without markers inherited the
  parent's — for `NoBroadcast`, silently blocking bare keys the subclass never
  opted out of. The read is now the class's OWN `__dict__`, the same guard the
  registry has always used for `__confluid_name__`.

- **`materialize()` interpolates.** docs/interpolation.md promises `${...}`
  substitution "at materialization — `load()`, `materialize()`, or
  `resolve()`"; measured, `materialize()` was the one entry point that skipped
  the Resolver pass, so the literal `${key.path}` rode into values silently
  (`configure()` resolves too). It now runs the same pass `load()` runs —
  idempotent on the load path, which has already substituted.

- **`${...}` inside a marker's kwarg block interpolates — all spellings agree.**
  The Resolver returned any Fluid whole, so a placeholder written in a
  `!class:`/`!lazy:` tag's mapping body stayed the LITERAL string on every path
  (measured: `input_dir: "${DATA_ROOT}/files"` reached the constructed object
  verbatim, silently) — while the quoted-string spelling of the same target
  interpolated, because `_parse_class_string` resolves per kwarg. Marker kwargs
  are now walked by the same load-time pass, in place (marker identity is
  load-bearing for the flow memo and `!ref:` sharing) and text-only: a
  `"!ref:"`/`"!class:"` string keeps its prefix for flow-time parsing, a
  `Reference` fluid stays late-bound, nested markers recurse, and sibling
  kwargs act as the local scope. Substituted values BURN IN — `dump()` emits
  them and a deferred `!lazy:` slot flowed later sees them; a slot that must
  stay late-bound uses `!ref:` to a plain key.

- **The override DEBUG line picks its article from the winning scope's name** —
  "replaced by an exact one", not "a exact one". Cosmetic, but the line exists
  to be grepped by an operator explaining a value they did not expect, and a
  typo there reads as a bug in the very diagnostic meant to build trust.

- **Positional-only constructor parameters are configurable.** `def __init__(self,
  k, /)` rejected every document path with a `ConstructionError` while
  `configure()` set it fine — the same engine/configurator split, from the same
  cause as `*args`: a name that can never be passed by keyword was left in the
  constructor-kwarg filter, so a matching config key reached the call and Python
  refused it. Both kinds are now excluded; the value arrives as a post-init
  attribute, which is what the post-construction path always did.

- **A target that can accept a key nowhere now says so.** An addressed key aimed at
  a `__slots__` class with no matching slot (or any object refusing the attribute)
  escaped as a raw `AttributeError` — `'S' object has no attribute '__dict__'`,
  from a line the author never wrote. It is now a located `ConstructionError`
  naming the key, the target and the YAML position, and saying what to do:

  ```
  ConstructionError: S cannot accept 'k' (set at config.yaml:4:3): it is not a
  constructor parameter and the object does not allow the attribute to be set.
  Add it to the constructor, or remove it from the config.
  ```

  A *bare* key nothing declares is still dropped silently — it was aimed at the
  whole document, so a node that cannot take it is the normal case, not a mistake.

- **Every published registry key is now a legal YAML tag.** `_entry_key` strips
  `<locals>` precisely so keys survive a `!class:` tag, but `<lambda>` reached it
  unhandled. Registering two anonymous callables under one name published
  `__main__.<lambda>` — a key `list_classes()` returned and the loader rejected:

  ```
  ScannerError: while scanning a tag
  ```

  Registering an anonymous callable is legitimate (a one-line metric, a builder
  factory) and works until the *name* becomes ambiguous, at which point the public
  key switches to the dotted form and becomes unusable. So the key is made
  tag-legal rather than the registration refused.

  The two bracketed shapes get opposite treatment, because they mean opposite
  things: `<locals>` names a *scope* and is dropped (`outer.<locals>.Inner` →
  `outer.Inner`, still identifying the class), while `<lambda>` *is* the name and is
  unwrapped (`__main__.lambda`) — dropping it would leave a bare trailing dot,
  identical for every lambda in the module. Same-module collisions fall to the
  existing `~N` suffix, `~` being a legal tag character. The rule is general, so
  `<listcomp>` / `<genexpr>` / `<module>` are covered too.

- **A zero-parameter constructor is configurable again.** A `@configurable` class
  whose `__init__` takes no parameters at all died on any config key:

  ```python
  @configurable
  class Host:
      def __init__(self) -> None:      # no parameters
          self.slot = None             # ...but a body slot to configure
  ```
  ```
  ConstructionError: Host.__init__() got an unexpected keyword argument 'slot'
  ```

  The error came from inside the class's own constructor and pointed nowhere near
  the config that caused it. The shape is one the class-design convention actively
  encourages — a minimal constructor with the dependencies as `__init__`-body
  slots — taken to its limit.

  `_ctor_params` returned a plain `set()` for two different states: the signature
  could not be *read*, and the signature was read and found *empty*. Those need
  opposite handling (pass every kwarg through as a best effort, versus pass none),
  and the caller's truthiness fallback picked the first for both. The unreadable
  case now returns a distinct `_UNKNOWN_PARAMS` sentinel — still an ordinary
  `set` for every reader, told apart by identity — so an empty parameter list
  means what it says.

  Pre-existing, not introduced this cycle.


- **Precedence is document order for EVERY spelling, not just some.** Confluid has
  one precedence rule — last spec wins — and two of the four ways to address a
  value at a deferred slot did not follow it. A mapping (`optimizer: {lr: 0.5}`)
  and a dotted key (`runnable.optimizer.lr: 0.5`) lost to a bare `lr:` **wherever
  it sat**, while the equivalent `optimizer: !lazy:AdamW(lr=0.5)` correctly won
  when written later:

  | spelling | bare key above it | bare key below it |
  |---|---|---|
  | `optimizer: !lazy:AdamW(lr=0.5)` | slot wins | bare wins |
  | `optimizer: {lr: 0.5}` | *was* bare wins → now slot wins | bare wins |
  | `runnable.optimizer.lr: 0.5` | *was* bare wins → now slot wins | bare wins |

  The cause was a guard testing `_yaml_loc is not None` — a source location, i.e.
  a *diagnostic* — as a stand-in for "was this addressed by the author?". Tuning a
  code-declared slot copies the code marker's empty location, so the author's own
  value was recorded as a default and any bare key beat it.

  The guard's real question is narrower: *has the ordered merge already settled
  this key?* `_prepare_kwargs` resolves a marker's own kwargs against surrounding
  bare keys **by position** and the later broadcast pass must not re-run that
  contest. That is now stamped explicitly as `Fluid._order_resolved`, and
  `_yaml_loc` is back to being diagnostics only.

  A mapping addressed at a slot is applied post-construction — the first moment
  the slot's default is knowable — and a plain dict cannot carry a position, so
  its contest is decided during the ordered merge and carried forward as its
  outcome (`Fluid._late_bare_keys`: the bare keys sitting later than that slot).

- **A class-name block now reaches an `__init__`-body slot.**
  `Trainer: {optimizer: {lr: 0.5}}` left the constructor default even with nothing
  competing — the value was silently dropped — while the inline spelling of the
  same thing worked. Two paths were involved and each had its own reason:

  - **Loading:** `_consume_block` admitted a dict only for a dict-*typed* param,
    where `_apply_own` admits one at any *declared* key. A deferred body slot is
    not dict-typed, so the mapping was hoisted as routing for the children and
    never applied. `_consume_block` now follows the same rule on the addressed
    path; a glob-delivered or floating dict stays routing.
  - **`configure()`:** the same block recursed *into* the marker. A `Fluid`
    reports `__confluid_configurable__`, so it looked like a live configurable
    child, and attributes were set on the marker object where nothing reads them.
    It now tunes `marker.kwargs`, matching the load path.

- **`configure()` no longer builds a deferred slot, and no longer discards what you
  aim at one.** Two further divergences from the load path, both on the
  post-construction side:

  - `_assign` materialized the marker, because `Lazy` subclasses `Class`. A `!lazy:`
    slot exists precisely so its owner can flow it later *with* the runtime argument
    (`params=model.parameters()`), so building it here produced an object
    constructed without that argument — and the failure landed far from the cause.
    `Lazy` is now excluded, matching `engine._apply_post_init_attrs`.
  - `_walk` then flowed the slot again to recurse into it and configured the
    resulting object, which is never written back to the attribute. Every bare key
    aimed at a deferred slot was applied to a throwaway and silently lost. A `Lazy`
    is now tuned in place — merged into `marker.kwargs` under the same accept-list
    and `NoBroadcast` gates the engine applies — so the value is there when the
    owner flows it.

  With those, **both paths now order identically** across all four spellings.

- **An ordering verdict no longer outlives the document that produced it.**
  `configure()` recorded which bare keys a slot-addressed block had out-positioned
  *on the marker*, so a second call carrying only a bare key found that key still
  marked "lost" against a document it never saw:

  ```python
  configure(car, config="power: 99\nCar: {engine: {power: 50}}")   # -> 50, correct
  configure(car, config="power: 77")                               # -> 50, WRONG
  ```

  Layering a base config then an override is ordinary usage, and it silently kept
  the first call's value. The verdict is now a local of the scan that computes it
  and is passed to the tuning step, so it cannot escape the call. Deferred slots
  are also tuned by their owner's scan rather than during the graph walk — the
  owner is the only place that knows where each block sat relative to the bare
  keys.

- **A deferred (`!lazy:`) slot declared in code is now configurable.** Three
  separate rules combined to make a `self.optimizer = LazyClass(AdamW, lr=1e-4)`
  slot unreachable from config — every natural spelling failed, and all but one
  failed *silently*:

  | what you write | before | after |
  |---|---|---|
  | `lr: 0.5` (bare) | ignored — trains at `1e-4` | applied |
  | `optimizer: {lr: 0.5}` | slot replaced by a raw `dict` | applied, `weight_decay` kept |
  | `optimizer.lr: 0.5` | slot replaced by a raw `dict` | applied, `weight_decay` kept |

  The three causes: (1) `_resolve_kwarg_value` returned a `Lazy` untouched, so it
  received no broadcasting at all — but deferral means "do not BUILD it", and
  merging keys into a marker's `kwargs` builds nothing, which is precisely what
  the `Class` branch beside it already did. A `Lazy` **is** a `Class`, so it now
  takes that branch and only the terminal eager flow is withheld. (2) A mapping
  addressed at a slot holding a deferred marker was assigned verbatim, destroying
  the marker; it now **merges into the marker's kwargs**, so the kwargs you did
  not mention survive. (3) A kwarg set in **code** blocked a bare key, while a
  constructor default with the same value did not — so *where* a default was
  written decided whether config could reach it. Code-set marker kwargs are now
  treated as the defaults they are. A kwarg written on the marker in the
  **document** still wins over a bare key (provenance via `_yaml_loc`), so
  addressed-beats-bare is unchanged.

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
