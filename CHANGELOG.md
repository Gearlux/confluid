# Changelog

All notable changes to confluid are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [0.3.0] — unreleased (tag deliberately held pending downstream verification)

### Documentation

- **The docs-truth pass (2026-08-13).** The refactor's documentation debt, closed in one sweep:
  `strict_attrs` — shipped and pinned but documented nowhere — got its topic guide
  (`docs/strict-attrs.md`), an asserting example (`examples/strict_attrs.py`) and its README /
  marks-table rows. Stale docstrings were brought back in line with the code: `report.py` still
  claimed the engine path records no failures (false since B1), `introspect.py` still described
  the pre-consolidation module and named a consumer of a function it no longer consumes,
  `resolve()` named two deleted marker classes. The deleted `Class` / `Instance` names were
  purged from every current-API doc sample (`io-contract.md`'s "canonical spellings" raised
  `NameError` when copied). `docs/performance.md`'s baseline was re-measured (~30 % stale).
  The README now lists all nine passes (`import` was missing) and states the tag spelling's
  deprecation + 0.4.0 removal on the landing page; `docs/targets.md` leads with the reserved
  keys instead of "built from six YAML tags". `docs/class-design.md` rule 2 now states zero-arg
  construction as RECOMMENDED (the 2026-08-11 ruling); the body-slot `owner` filter is stated
  in `class-design.md` / `schema-export.md`. Consumer-project names were genericized out of
  every docstring and the two `docs/` violations (a named consumer in `serialization.md` /
  `errors.md`). `loader` / `resolver` / `schema` gained module docstrings. The two unreleased
  CHANGELOG sections were merged into this one. Public docstrings teaching the tag spelling
  (16 objects, `load` / `flow` / `materialize` / `configurable` / `configure_from_file`
  included) were rewritten to the reserved keys, and a third canonical-spelling scan pins the
  `confluid.__all__` docstring surface so they cannot regress. User-facing hint/warning texts
  that taught the tag spelling (the ambiguous-name hint, the auto-defer warning, the scope
  mismatch message) now teach the reserved keys.

### Removed

- **`pydantic_export.partial_param_names_of` and the `_confluid_lazy_params` model stamp** — a
  parallel answer to "which slots are deferred", computed onto every generated model and read by
  nobody outside confluid's own tests (workspace census 2026-08-13: zero consumers; the surface
  was never in `__all__`). The ONE authority is the class-side
  `confluid.partial.partial_param_names(cls)`, which is what serializers already consult.
- **`broadcast._get_post_init_attrs` and `_post_init_attrs_cache`** — the wrapper was a one-line
  alias for `introspect.body_slot_names` kept alive only by tests, and the cache had zero reads
  and zero writes (it was registered for a per-pass clear of an always-empty dict). Tests now
  call `body_slot_names` directly.

### Fixed

- **A nested class-valued kwarg dumps its `__qualname__`.** `dump()` rendered a class VALUE with
  `__name__`, so `Holder.Inner` dumped as `pkg.Inner` — a path that resolves to nothing, or
  silently to an UNRELATED top-level class sharing the short name. Now `pkg.Holder.Inner`,
  matching the two sibling sites in the same file that already used `__qualname__`.

### Internal

- The ` at file:line:col` error-suffix helper (`_at_yaml_loc`) moved to `confluid.fluid` beside
  `format_yaml_loc` — it had drifted into three spellings across seven re-inlined copies because
  the layering kept `broadcast`/`scopes` from importing it out of `engine`. The five
  identical-spelling sites now share it; the two contextual spellings (`(set at …)`,
  `(receiver at …)`) are deliberate and unchanged. `engine._contains_fluid` was renamed
  `_is_definition_shaped` — it shared a name with `validation._contains_fluid` while treating ANY
  list as fluid-shaped and never recursing tuples, a de-duplication trap. The duplicate
  `partial_param_names` entry in `__all__` and its orphaned comment (left by the alias-block
  deletion) are gone.

### Fixed

- **`Clone` has ONE override semantic, whichever engine path resolves it.** The document path
  merged a clone's overrides into the copied marker's kwargs (they reached the constructor)
  while a directly flowed clone deep-copied the BUILT referent and pasted them on as setattrs —
  invisible for a store-only constructor, measured three ways otherwise: an `eager=True` class
  computed from the template's value on one path and the override's on the other (`step_size`
  0.4 vs 0.1 for one document); `flow(clone, lr=0.9)` fed the runtime kwarg to the REFERENT's
  build and the clone's stored kwarg then beat it, inverting `flow()`'s "runtime wins" contract
  for every class; and overrides on non-marker referents vanished silently (live objects and
  mappings on the document path, scalars everywhere). One helper (`engine._clone_of`) now serves
  both paths, deciding by the REFERENT's kind: marker → kwargs-merge then BUILD; mapping → key
  merge; live object → setattrs (the `configure()` semantic); scalar/list with overrides → a
  located `ConfigurationError`. Runtime kwargs configure the clone (runtime wins), the fresh
  marker is pinned in `memo_keepalive`, and the referent is deep-copied before merging so a
  template is never mutated by its clones. Architecture record 14; pinned by the 2026-08-13
  group in `tests/test_clone.py`.
- **The last three private slot walks now project from `introspect.slots()`** — the 2026-08-12
  consolidation left `to_pydantic`'s signature half, `broadcast._get_param_kinds` and the dumper's
  two walks un-migrated, each with a wrong answer the shared enumeration already had right:

  - `to_pydantic` filtered parameters by NAME (`_SKIP_PARAMS = {"self", "cls", "args", "kwargs"}`),
    so an ordinary keyword parameter literally named `args` or `kwargs` was dropped from the
    generated model alone among the six readers — and, the model being `extra="forbid"`, the
    default strict init policy then REFUSED the legal call (`Odd(args=[1])` →
    `ValidationError: args Extra inputs are not permitted`). Exclusion is now by KIND
    (`_FIELD_KINDS`): variadics are never fields whatever they are named, and a name never
    excludes anything. The same name-set also silently excluded a *body slot* named `args` from
    every generated schema (`_post_init_field_specs`' seen-set); it no longer does. The
    introspection-agreement table now carries `to_pydantic`'s field set, so a seventh private
    walk cannot drift unnoticed.
  - `broadcast._get_param_kinds` walked the signature alone and was blind to body slots: a class
    declaring `self.transforms: list[Any] = [...]` answered `{}`, so an addressed
    `Cls: {transforms: [...]}` block was refused as a value on the LOAD path ("block has no
    attribute") while `configure()` applied the identical block. An ANNOTATED body slot now
    classifies exactly like the equivalent ctor param — the two declaration halves of the
    class-design convention behave alike, and the two paths agree. An unannotated body slot
    stays unclassified (routing/block reading unchanged). Consequent rule:
    `_classify_annotation` peels `Annotated` first, because `slots()` resolves hints WITH
    extras — without the peel every range-marked container param
    (`Annotated[Tuple[float, float], Interval(...)]`) would have flipped to `None`.

### Changed

- **`dump()` no longer emits a stored variadic bundle.** The dumper's own signature walks kept
  `*args`/`**kwargs` NAMES, so a class storing `self.kwargs = kwargs` dumped a `kwargs: {...}`
  line — which never round-tripped (on reload the ctor filter passes it INSIDE the catchall as a
  literal `"kwargs"` key, doubly nested). Both walks now project `slots()` to `_DUMP_KINDS`
  (`keyword` + `positional_only`, signature order preserved); the None-skip rule and body-slot
  exclusion are unchanged.

### Breaking

- **`@ignore_config` is REMOVED** — `from confluid import ignore_config` now raises `ImportError`.
  There is no deprecation shim: the decorator was a no-op on every class that existed.

  Measured before removing it: of **275 registered classes across the workspace, 0 used it**. Its
  only occurrence anywhere was a documentation example marking a read-only `@property` — and on
  that shape it changed no reader's answer, because a setter-less property is already excluded by
  `introspect._non_signature_slots`. The same class with and without the decorator answered
  identically on all four surfaces (`accepts_key`, `get_hierarchy`, `to_pydantic`, the accept-list).

  Where the marker *did* bite — a class attribute shadowing an `__init__`-body slot — it made the
  accept-list disagree with the engine: `accepts_key` returned `True` while the engine discarded
  the value, with no warning and no `report.failed` entry, so a config line was silently thrown
  away. Removing the marker makes that key behave as written:

  ```yaml
  cache:
    _target_: Cache
    scratch: /tmp/mine     # before: silently discarded (obj.scratch is None)
                           # after:  obj.scratch == "/tmp/mine"
  ```

  **Migration:** delete the decorator. To keep an attribute out of configuration, make it a
  read-only `@property` (the mechanism `@output` already documents) or prefix it with `_`.

  Pins: `tests/test_introspection_agreement.py::test_a_readonly_property_is_excluded_without_any_marker`,
  `::test_a_yaml_key_aimed_at_a_readonly_property_does_not_change_it`, `::test_ignore_config_is_gone`.

### Changed

- **`get_hierarchy` now reports `__init__`-body slots, so `--docs` stops lying by omission.**
  It walked the signature only, which meant a CLI listed fewer knobs BEFORE a config was flowed
  than after — the two hierarchy walkers disagreeing about the same class. Measured on
  `matrainer.TrainerRunnable`: **10 paths where 13 exist**, with `metrics_sidecar_path`,
  `predictions_sink` and `val_set` invisible to anyone reading `--docs` cold.

  Body slots are configurable by the class-design convention's rule 4, so omitting them
  contradicted the walker's own contract. It could not be fixed until `slots()` carried body-slot
  TYPES (the walker recurses on a type), which landed the same day.

  A configurable-typed body slot RECURSES, exactly as a ctor param of that type does — verified
  side by side that both declaration halves produce the same path shape (`Host.child.lr`). The
  alternative (report as leaves, never recurse) was rejected: measured across 314 registered
  classes and 859 body slots, **zero** carry a directly-configurable type, so the two options
  produce identical output on every real class and only this one is a rule you can state.
  `class_attr` slots stay out — the accept-list wants them, a declared-options listing does not.

  Only body slots the class OWNS are reported — the same `Slot.owner` filter `to_pydantic`
  applies. Without it a class extending `keras.Model` advertised twelve of its internals
  (`predict_function`, `compiled`, `supports_jit`, …) as configuration: measured at 522 of 859
  body slots workspace-wide, so the honest growth in `--docs` is **+337 paths (+15 %)**, not +859.
  The live walker never had the bug — it reads `get_configurable_attrs`, which subtracts
  non-`@configurable` ancestors already.

  Consequence for the reader table: `get_hierarchy` moved from the "what the signature declares"
  answer to the "what is configurable at all" answer, leaving `input_specs` alone on the first —
  which is correct, since that one reports the CONSTRUCTOR contract, and a body slot is by
  definition not part of it.

### Changed

- **`schema.py`'s three private signature reads now project from `introspect.slots()`.** The
  backlog entry called the two hierarchy walkers "~150 near-parallel lines … merge into one walker
  with a static/live policy". Measured first, that premise did not hold — they are 266 lines and
  answer different questions:

  ```
  get_hierarchy(Root)               -> {Root.leaf, Root.name}                     rooted at the CLASS
  get_hierarchy_from_instance(root) -> {run.leaf._Leaf.lr, run.name, run.toggle}  rooted at the INSTANCE
  ```

  The static walker recurses on a configurable ANNOTATION, the live one on a live VALUE. Merging
  them would unify two things that deliberately differ, and both feed user-visible surfaces (a
  CLI's `--help` / `--docs`, hyperparameter logging). So the ENUMERATION is shared and the
  traversals are not; `_skipped_param_names` is deleted, and `schema.py` no longer reads a
  signature at all except for an `@output` property's return type.

  Output pinned before and after — byte-identical. The backlog entry is corrected, since it would
  have sent the next reader at a merge that should not happen.

### Added

- **`@configurable(strict_attrs=True)` / `register(..., strict_attrs=True)` — a closed config
  surface.** Confluid is permissive by default and stays that way: an addressed key naming nothing
  a class declares lands as a post-init attribute, because `_apply_post_init_attrs` IS the
  post-construction toggle mechanism. A class that wants its surface closed can now say so.

  ```
  Trainer has no attribute 'epochz' at exp.yaml:2:3 and is declared strict_attrs=True, so it
  will not be set as a post-init attribute. Trainer declares: epochs, head.
  ```

  "Declared" is the accept-list — constructor parameters (or the callable's own signature for a
  builder function), settable class attributes, and `__init__`-body slots.

  Two decisions, both taken deliberately: `register()` carries the mark for the reason it carries
  `broadcast=False` (a class you do not own is the one you cannot close by editing its
  declaration), and the mark binds `configure()` as well as the load path — one meaning different
  things per path would be the exact asymmetry the previous change removed.

  Never refused: a `**kwargs` target (no accept-list, so nothing is undeclared for it — marking one
  is meaningless rather than an error) and BARE keys (the accept-list drops them first, which keeps
  a strict class usable in a document that also configures something else). All three exemptions
  pinned, along with inheritance and the `marks()` read surface.

  The detection is not new — it is the warning added in the same round, now gated by the mark.

### Changed

- **`slots()` now carries a body slot's DECLARED type, and the two private scans are gone.** The
  first consolidation unified the NAME enumeration and left the TYPE one duplicated:
  `Slot.annotation` was `Any` for every body slot, while `pydantic_export` and `confluid.partial`
  each ran their own AST scan to resolve `self.run_name: Optional[str]`. Nothing checked the two
  agreed, and **163 annotated body slots** in production code ride on it (`matrainer/runnable.py`
  alone: `Optional[Partial[RecordSource]]`, `RunnableTask`, `Partial[KerasOptimizer]`, …).

  ```
  the class says            slots() before      slots() now
  optimizer: Partial[Dep]   Any                 Union[Dep, Fluid, …]
  run_name: Optional[str]   Any                 Optional[str]
  untyped   (no annotation) Any                 Any        <- the class's own answer
  ```

  **`Slot` gained `owner`** — the MRO class that declared the slot — because the two readers need
  different MRO SCOPES, not different filters. The accept-list wants a non-`@configurable` base's
  `self.training = True` (a bare key may legitimately set it, and the engine subtracts those later);
  `to_pydantic` must not, or every model in a torch/Lightning tree grows fields no config should
  set. Measured before the change: a naive projection would have added exactly
  `['allow_zero_length_dataloader', 'prepare_data_per_node', 'training']` to every generated
  schema. So the walk is shared and the scope is the reader's, as an `owner` filter.

  Eager vs lazy resolution was decided by measurement, not preference: 74 µs for a class with ten
  annotated slots, cached once per distinct class per pass, against a 278 ms materialize. Lazy was
  rejected — it would have made `Slot` something other than a plain NamedTuple for under 1 %.
  `materialize` after: 278.1 ms, unchanged.

  One scan deliberately stays: `confluid.partial`'s deferred-by-VALUE half
  (`self.x = PartialClass(...)`). `slots()` carries a slot's declared TYPE, never its assigned
  expression, so that signal is not projectable. Its deferred-by-ANNOTATION half now is.

  Sequenced so every step was reviewable: `to_pydantic`'s current output was pinned FIRST, the
  annotation fill-in changed exactly one test (the pin whose job was to show it), and the schema
  is byte-identical afterwards.

### Fixed

- **An undeclared config key is now reported on the load path, not silently absorbed.** One typo
  had three behaviours: `load()` with the key on the marker SET it silently, `load()` with the key
  in a class block IGNORED it silently, and only `configure()` reported it.

  ```
  Node has no attribute 'pathh' at exp.yaml:2:3 — set as a post-init attribute anyway.
  Declare it as a constructor parameter or an __init__-body slot, or remove it.
  ```

  The load-path sink justified its silence as *"constructor validation is this path's typo
  enforcement"*. Measured false: `pathh` is not a constructor parameter, so `_ctor_params` filters
  it out and it reaches a post-init `setattr` — around the constructor. `Node(pathh="/x")` raises
  `ValidationError`; the same key through `load()` did not.

  **The own-kwarg form still APPLIES the value** (option B1, chosen over full `configure()`
  parity). That branch IS the post-init attribute mechanism — `_apply_post_init_attrs` exists to
  assign kwargs the constructor did not take — so refusing there would delete documented behaviour
  from the commonest spelling. Refusing is the opt-in `strict_attrs` mark instead, now filed.

  Three exemptions, each pinned: a `**kwargs` class is never reported (no accept-list — it accepts
  everything by design), a BARE key is `unused` rather than `failed` (it legitimately matches
  nothing, so reporting it would fire on every sweep document), and a declared body slot is silent.

  Impact measured BEFORE shipping: **0 real warnings across 96 workspace configs and 412 markers**
  (five apparent hits were artifacts of the measurement resolving bare names against a merged
  registry — verified individually). All 24 examples stay silent.

### Fixed

- **A config key naming a `*args` parameter is refused instead of absorbed.** A variadic is
  positional-only by construction and a marker carries keyword arguments alone, so such a key can
  never reach the parameter. It was silently set as a post-init ATTRIBUTE: the constructor never
  saw it, and `obj.loaders` held a value the object ignored.

  ```
  DataLoaders cannot accept 'loaders' at exp.yaml:3:3: it is a *args parameter, which can
  never be passed by keyword, so no config key can reach it. Pass the values positionally —
  flow(node, a, b) — or give the target a keyword parameter.
  ```

  Hydra refuses the identical spelling (measured, 1.3.5: `loaders: [...]` on a `*loaders` target
  raises `InstantiationException`) and offers `_args_` as its separate channel; confluid's channel
  is `flow(node, a, b)`, runtime-only by mandate, so the config-side answer matches.

  **ADDRESSED keys only** — one written on the marker or in a `ClassName:` block. A BARE key is an
  implicit `**.key` that cascades tree-wide and legitimately matches nothing, so a document whose
  top-level key collides with some class's variadic parameter keeps loading. Hydra never faces
  that case: it has no bare-key broadcast. A `**kwargs` name is never refused either — such a
  class accepts everything by design. All three exemptions are pinned.

  Scope came from measurement, not assumption: the two spellings reach different code paths
  (`engine._apply_post_init_attrs` for own kwargs, `_MergeSink.unknown` for a class block) and the
  bare form reaches neither, so its exemption is structural rather than a check.

### Changed

- **One slot enumeration, six projections.** Six readers answer "which slots does this target
  have", each walking the signature itself with its own hand-rolled "minus `self`/`cls`" filter.
  Measured on one class, they gave **five different answers**; they now project from
  `introspect.slots()` and give **three** — one per question actually being asked (what is
  configurable at all · what the signature declares · what the constructor can take).

  Three of the five differences were defects, each silent:

  * the accept-list admitted a `*args` NAME, which can never be passed by keyword — it landed as
    a post-init attribute nothing reads;
  * `get_hierarchy` published the same name as a dotted CLI path, so a front-end offered a flag
    Python rejects at the call. Its filter skipped `self`/`cls`/`args`/`kwargs` by NAME, which
    misses a variadic spelled anything else and would wrongly drop an ordinary parameter called
    `args`;
  * `declares_key` — the public predicate `AGENTS.md` tells front-ends to call INSTEAD of
    re-deriving settability — gave OPPOSITE answers for the same parameter kind depending on
    whether the class also took `**kwargs`, because it short-circuited to the accept-list in one
    case and ran its own kind-filtered walk in the other.

  **Behaviour change:** a bare key matching a `*args` parameter name no longer lands as a
  post-init attribute. `positional_only` is deliberately still settable — it falls through to a
  post-init `setattr`, which is what `configure()` has always done for it, so both paths agree.

  The `introspect.py` NOTE that rejected a shared "ctor params" helper was correct and shaped the
  fix rather than blocking it: all three of its objections (the dumper needs ordered params, the
  accept-list needs its `**kwargs` → `None` sentinel, schema needs rich metadata) are about the
  RETURN TYPE of a name-set helper, none about the enumeration underneath. Rationale:
  `docs/architecture.md` record 12. Measured cost: none — `materialize` 278.2 ms vs 276.9 ms on
  the 2,500-marker benchmark, within noise, since one cached enumeration replaces several walks.

  Two readers keep their own walk, deliberately: `to_pydantic` needs the AST-resolved annotation
  of a typed body slot (a feature gap, filed), and the dumper's signature walk is `hasattr`-guarded
  and outside the divergence.

### Added

- **`tests/test_introspection_agreement.py` — the baseline for the `slots()` consolidation.**
  Five subsystems answer "which slots does this target have", and for one class they give five
  different answers; nothing checked that, so a divergence could only ever be found by someone
  hitting it. The file records each answer verbatim and separates the DELIBERATE differences (an
  accept-list is not a form spec; the `**kwargs` → `None` sentinel; `_ctor_params` keeping
  VAR_KEYWORD while dropping VAR_POSITIONAL) from the DRIFT, which it pins under `test_DRIFT_*`
  names that say what the fix should make each one do. Pinning a defect before touching the layer
  that produces it is what makes that refactor reviewable — verified by breaking `_ctor_params`
  with a one-line change and confirming three tests trip, each naming a different consequence.

### Fixed

- **A quoted marker string the resolver cannot honour now RAISES instead of reaching your
  config as text.** `optimizer: "!lazy:Adam(lr=0.01)"` used to load as the literal string
  `!lazy:Adam(lr=0.01)` — no marker, no error, no warning — and the constructor got the text.
  So did `"!clone:…"` and `"!scope:…"`, in every position, and so did `"!class:…"` / `"!ref:…"`
  **inside a marker's own kwargs**, which is the position `docs/targets.md` recommended the
  spelling for. Measured against the pre-change engine before changing anything.

  ```
  '!lazy:Adam(lr=0.01)' is a marker written as a quoted STRING, which confluid cannot
  honour here. Write it as plain YAML instead:

      {_target_: Adam, _partial_: true, lr: 0.01}

  (The quoted-string spelling is deprecated and is removed in confluid 0.4.0; only
  "!class:" and "!ref:" were ever parsed from a string, and only outside a marker's own kwargs.)
  ```

  The replacement is derived from the same `Target(...)` grammar the tags use, so the message
  is a line you can paste. The refusal matches the exact marker prefixes and never a bare
  leading `!` — an ordinary config value may legitimately start with one (`!important`), and
  breaking those to fix a spelling nobody uses would be the worse trade. No `file:line`: a
  scalar carries none (only markers are stamped), so the message quotes the offending text
  instead, which is greppable; a location side-table is filed in `TASKS.md`.

  `tests/test_loader.py::test_quoted_lazy_tag_is_not_recognized` previously ASSERTED the
  silence and is replaced by the refusal group. Two resolver tests used a marker string in
  marker kwargs as a vehicle for unrelated rules (the bare-`$` skip, the in-place kwargs walk)
  and were retargeted; one of them carried the comment *"string keeps its prefix — parsed at
  flow time"*, which was false — nothing parsed it, there or later.

  **The whole path is deleted in 0.4.0 with the tags** (`TASKS.md` phase 5e). It exists only
  to work around a tag limitation — YAML forbids two tags on one node, so a nested `!ref:` had
  to be quoted — and `{_target_: Adam, lr: ${ref:base}}` needs no escape at all. A census
  found zero configs using it workspace-wide.

### Added

- **`ConfigurationReport.explain(key)` — why a key has the value it has.** Confluid arbitrates by
  document POSITION and nothing else, which is the one thing about it that surprises people: a
  value written AT a node loses to a bare key that merely sits lower in the file. That is the
  documented rule, and until now the only way to watch it happen was `LOGGAIR_CONSOLE_LEVEL=TRACE`
  and a grep — even though both candidates already reached the report's sink and the loser was
  simply discarded.

  ```
  lr on Trainer = 0.9
      #0   block 'Trainer'    0.5          beaten — earlier in the document
    ✓ #2   bare               0.9          applied
  ```

  Both paths report identically — a `configure()` report explains the same contest a `load()`
  report does, because both run the one scanner. The candidates live on `AppliedKey.contest`
  (`Candidate(origin, value, pos)`, document order, last entry wins) so a front-end can render
  them; `value` is a bounded string, never the object.

  Cost: `materialize()` / `resolve()` are unchanged (the ledger is behind the existing
  `report is not None` guard), and `configure()` — which always has a report — pays 1.5 ms on a
  2,500-marker pass. The first implementation cost 5.7 ms; rendering the `repr` at record time,
  and only for keys something actually contested, removed 4.6 ms of that.

- **`docs/broadcasting.md` → "What this costs you"** and a `RATIONALE.md` section naming the bet.
  Nothing in the documentation said that **moving** a line — not just adding or removing one — can
  change the result, which is the price of implicit reach-many. Libraries that make reach-many
  explicit pay the opposite price in verbosity. `RATIONALE.md`'s comparison table listed confluid's
  only weakness as "New implementation"; it now names order-dependence, with `explain()` as the
  mitigation.

### Changed

- **The examples and the README now teach the reserved-key spelling.** Twelve of the
  twenty-four runnable examples still used the deprecated YAML tags — `lifecycle.py` (the
  README's designated "start here"), `deep_injection.py` (the flagship pitch),
  `broadcasting.py`, `eager_classes.py`, `error_handling.py`, `interpolation_includes.py`,
  `introspection.py`, `performance.py`, `report.py` and `scopes.py` — so a reader following
  the documentation path got a `FutureWarning` saying their config was obsolete. Every one
  was converted with `confluid-migrate` and its equivalence check; the two that document the
  tag form itself (`plain_format.py`, `tags_deferred.py`) keep it.

  Three more were fixed that no runtime check could have found: `ml_pipeline.py` used the
  QUOTED-STRING spelling (`"!class:Adam(lr=!ref:base_lr)"`), which emits no deprecation
  warning at all and which `confluid-migrate` reports rather than converts;
  `examples/ml_experiments/` had migrated YAML with its comments, its `run.py` docstrings and
  its README still describing tags — including a Hydra-comparison table telling a Hydra user
  that confluid's answer to `_target_:` is `!class:Name()`; and `examples/schema_export.py`
  named `!class:` in a comment.

- **The guides now match their companion examples.** Converting the examples left 69 tag
  lines across nine guides showing configs their own runnable companion no longer matched —
  `docs/broadcasting.md` displayed `sink: !class:Passthrough(tag=addressed)` while
  `examples/broadcasting.py` had moved to the reserved keys. `broadcasting.md`, `scopes.md`,
  `interpolation.md`, `lifecycle.md`, `errors.md`, `eager-classes.md`, `introspection.md`,
  `report.md` and `class-design.md` were converted, and `scopes.md` was reoriented around
  `_scope_` (it opened by announcing "this page is written in the tag form"). Every YAML
  snippet written into `scopes.md` and `discovery.md` was executed to confirm it does what
  the page claims. A further 25 stale prose references were fixed across seven more guides.

- **README corrections.** The "Design Goals" bullet advertised a "Tag-Based IR" with
  `!class:Name` deferred / `!class:Name()` eager — a distinction deleted in 0.3.0 when
  `Class` and `Instance` collapsed into `Target`. The Quick Start's first config used the
  quoted-string spelling. The Scopes bullet spelled a scope `_scope_: debug`, which is not
  valid: the value must be a MAPPING, and that spelling raises `ConfigurationError`.

- **`confluid-migrate` is no longer suggested for a document that has no file.** PyYAML names
  a string-loaded document `<unicode string>`, which the deprecation notice interpolated
  straight into its remediation — `confluid-migrate <unicode string>`, a command that cannot
  be copied or run, from a message whose only job is to say what to do next. Such a document
  now gets an explanation instead of an uncopyable command. Pinned by
  `tests/test_plain_format.py::test_a_string_loaded_document_is_not_told_to_run_an_uncopyable_command`.

### Fixed

- **`Resolver._parse_class_string`'s docstring described behaviour that was deleted** —
  "`Name(...)` (with parens) is eager → `Instance`; a bare `Name` is deferred → `Class`".
  Both classes are gone and both branches return a `Target` with `partial=False`; the
  trailing `()` has been inert since the IR collapse. It now also records that this is the
  QUOTED-STRING grammar, a third input spelling beside the tags and the reserved keys, whose
  inline values resolve EAGERLY where `_target_`'s `${ref:...}` stays late-bound — so the two
  are not interchangeable for a slot flowed outside the document.

- **`docs/targets.md` documented a construction rule that was deleted.** Its table said
  `!class:Model` parses to a deferred `Class` stub and only `!class:Model()` builds — the
  "rule of thumb: a trailing `()` means build it now" — and a second table said a nested
  marker under a `@configurable` parent is NOT built. Both are false since the IR collapse
  (2026-08-11): the trailing `()` is inert, `_target_` always builds, and the only things
  that defer are `_partial_: true` and a slot the RECEIVER declared deferred (`Partial[T]` /
  a `PartialClass(...)` body value). Verified against the engine before rewriting — a reader
  following the old text would write `child: {_target_: Optimizer}` expecting a stub and get
  a constructed optimizer with no `params`. The "Deferred initialization" section, the
  marker-family table and the `Class(...)` code samples were rewritten around the two real
  modes.

- **`examples/ml_experiments/README.md` linked to `docs/tags.md`**, renamed to `targets.md`.
  `tests/test_docs_links.py` scanned `docs/` and the root README only, so a directory
  example's README — which IS its documentation page, per the AGENTS rule — was outside every
  link check. It now covers them.

### Added

- **`tests/test_canonical_spelling.py`** — two scans at two strictnesses: no example may name
  a tag anywhere (config or prose), and no guide may carry one inside a fenced ```` ```yaml ````
  block. Prose in `docs/` stays free, because naming the deprecated spelling is how a reader
  with old configs learns what to convert. Static over source text rather than a run of each
  example: a runtime check costs seconds per file, sees no prose, and would have passed
  `ml_pipeline.py`. Two inverse pins keep the allow-lists honest — an entry naming a file that
  no longer exists, and an entry that is no longer needed.


### Deprecated

- **The YAML tag syntax (`!class:` / `!lazy:` / `!ref:` / `!clone:` / `!scope:` / `!notscope:`)
  is deprecated and is REMOVED in 0.4.0.** Loading a tagged document now emits a `FutureWarning`
  naming the file, the line, and the fix:

  ```
  config/train.yaml:12: the YAML tag syntax (…) is DEPRECATED and is removed in confluid 0.4.0.
  Convert this file with `confluid-migrate config/train.yaml` — it rewrites the tags to the
  reserved-key format (_target_ / _partial_ / ${ref:}) and verifies the marker tree is unchanged
  before writing.
  ```

  Once per DOCUMENT, not per tag (a 400-line config would otherwise bury the message it is
  delivering) and not per process (which would name your first config and stay silent about the
  rest). `FutureWarning` rather than `DeprecationWarning` because Python shows it by default —
  the audience is whoever wrote the YAML, not library code, which is the split the two categories
  encode.

  **0.3.0 deliberately ships BOTH the tags and `confluid-migrate`**, because the codemod's safety
  check parses the tagged original to prove a conversion equivalent (`verify_equivalence`) — a
  release that removed the tags and shipped the tool would make it refuse every file it exists to
  convert. 0.4.0 removes the tag constructors, and with them the `${PLAIN}` → config-key
  interpolation flip (a bare `${VAR}` becomes a config-key reference, so every one must already be
  `${env:VAR}` — which is what the codemod converts it to).

### Removed — BREAKING

- **The deprecation aliases are gone (2026-08-11).** `Class`, `Instance`, `Lazy`, `LazyClass`,
  `lazy_param_names` and the `confluid.lazy` shim module are DELETED. They were the pre-merge /
  pre-rename spellings, kept while both YAML formats were supported so a consumer need not change
  in the same release. Use `Target`, `Partial`, `PartialClass`, `partial_param_names` and
  `confluid.partial`. Importing an old name now fails loudly rather than resolving to something
  that behaves differently: `Class` and `Instance` had become the same class, so code
  discriminating with `isinstance(x, Instance)` reads `x.partial`.

  `introspect._PARTIAL_CALL_NAMES` narrowed to `("PartialClass", "Partial")` with them. That list
  matches on the call NAME in the SOURCE, so a body slot written with a removed spelling does not
  raise — it silently stops deferring, and a runtime-injection target reaches its constructor
  without its argument. That is why the removal is called out here rather than left to the import
  error.

### Changed — BREAKING

- **Two construction modes, not three (2026-08-11).** `Class` and `Instance` merged into one
  marker, `Target`, carrying a `partial: bool`. **A bare `!class:Foo` is now BUILT**, exactly like
  `!class:Foo()` — the trailing `()` is inert, and `_partial_: true` (`!lazy:`) is the only
  spelling that withholds construction.

  The deleted middle state built or didn't depending on whether its *parent* was `@configurable`,
  which is not something a reader can see locally. Its stated justification — "pick deferred so
  broadcasting can still reach it" — does not hold: broadcasting is pass 7 and construction is
  pass 8, so a built node already receives every cascading key before its constructor runs.
  Verified, same document, both spellings: `Model(layers=3, seed=7)` either way.

  What this means for a config: a `!class:Foo` whose receiver genuinely needs to build it later
  (an optimizer needing `params=`, a model needing `num_classes`) must now say so — `_partial_:
  true`, or `!lazy:`. A slot the receiving class *declared* deferred (`Partial[T]`, or a body slot
  holding `PartialClass(...)`) still keeps its value unbuilt automatically, so classes following
  the workspace convention are unaffected.

- **`Lazy` renamed to `Partial` across the API** so the Python name and the YAML key are one word:
  `Lazy[T]`→`Partial[T]`, `LazyClass(...)`→`PartialClass(...)`,
  `lazy_param_names`→`partial_param_names`, `@configurable(lazy=True)`→`partial=True`,
  `confluid.lazy`→`confluid.partial`, `docs/tags.md`→`docs/targets.md`.
  `Class`/`Instance`/`Lazy`/`LazyClass`/`lazy_param_names` remain as **deprecation aliases** and
  are removed with the tag spelling. Note `Class is Instance is Target` now, so
  `isinstance(x, Instance)` no longer separates eager from deferred — read `x.partial`.

  `confluid/lazy.py` survives as a deprecation SHIM re-exporting the renamed names,
  because renaming a module breaks importers even when every name it exports still
  resolves: a consumer doing `from confluid.lazy import lazy_param_names` fails on
  the module path alone. Removed with the tag spelling.

- **`dump()` emits the plain format** (`_target_:` / `_partial_:` / `_ref_:` / `_clone_:`), so an
  archived config is readable by `yaml.safe_load`, `yq` and a diff viewer. Reload fidelity is
  unchanged.

### Changed

- **`resolve()` no longer instantiates a dotted `!ref:a.b` (2026-08-11).** It is
  documented as "markers returned, NOTHING constructed", but reading `split.train`
  means BUILDING `split`, and that branch ran on this path too. A dotted reference
  now stays a `Reference`, exactly as a plain `!ref:name` already did.

  Measured on a real config whose split scans 37 rar archives: `resolve()` took
  **3.9s** for a call that constructs nothing; it is now 10ms (the first call in a
  process still pays one-time imports of the classes the config names).

  `materialize()` / `load()` are unchanged — a dotted ref still resolves off ONE
  shared instance there, which is the whole point of writing `split.train` and
  `split.val`.

  Two consumers already documented behaviour they were not getting (a visual
  editor's YAML importer: "no instantiation — every node is a Fluid marker"; a
  flow-graph builder: "step markers stay UNbuilt"), and two projects' shipped-config
  test suites hand-rolled a tag-stubbing YAML parser specifically because
  `resolve()` "is not an escape either, since it instantiates a dotted `!ref:a.b`".
  Those workarounds can now be deleted.

### Fixed

- **`dump()` no longer emits a document `yaml.safe_load` refuses.** Two fallbacks still wrote
  TAGS: a function-valued param became `!ref 'module.qualname'` (a `collate_fn` is the common
  case) and an opaque object became a `!class:<name>` scalar. One tag anywhere costs the whole
  file its plain-YAML readability, which is the single property the format change exists to
  give. They are now `${ref:module.qualname}` — the spelling the codemod converts a `!ref:` to,
  so dumps and migrated configs agree — and a bare `{_target_: <name>}` mapping.

- **A class-resolution failure now names the YAML line that wrote the name.** `UnknownClassError`
  reported `Cannot resolve class: pkg.mod.Typo` with no file and no line, so the exact failure a
  rename produces left the reader a 30-frame traceback and a config tree to grep — while
  `ConstructionError`, a few frames later on the same run, had been printing
  `.../evaluate_yolo26.yaml:20:5` the whole time. The location was available and discarded:
  `_resolve_target_callable` was handed `obj.target`, the bare string, so the marker holding
  `_yaml_loc` never reached the raise. It now takes the MARKER, and both construction funnels
  report `at <file>:<line>:<col>`. `AmbiguousClassError` gets the same treatment — the registry
  raises it and has never seen the document, so the funnel re-raises it with the location and the
  registry's candidate list intact.

  Nested markers report their OWN node, not the enclosing one, which is what makes the line usable
  in a real config where the offending target is a source inside a pipeline's kwargs.

- **`confluid-migrate --verify` reported three kinds of FALSE difference**, each
  found by running the tool over real configs rather than test fixtures. All
  three had the same shape — the check failed, the file was refused, and the
  refusal looked exactly like the safety net working:

  - an **unset environment variable** left `$NAME` on one side and `${env:NAME}`
    on the other, two literals that differ as text while meaning the same thing.
    Unset variables now get a sentinel value for the comparison, which also makes
    it stronger: the substitution actually runs on both sides.
  - a **live object** reached the comparison (a dotted `!ref:split.train`
    instantiates its target to read the attribute) and was compared by `repr`,
    which carries a memory address. The two sides are necessarily different
    objects, so every such node read as a difference. Compared by TYPE now.
  - a **relative `include:`** resolved against the caller's working directory
    rather than the file's, since verification loads from text.

- **`confluid-migrate --verify` could not verify a config with a relative
  `include:`.** Verification loads the document from text, which carries no
  location, so `include: ../base.yaml` resolved against the caller's working
  directory instead of the file's. Every such config reported
  `ConfigFileNotFoundError` under every activation and was refused as
  unverifiable — a failure that reads exactly like the check doing its job. It
  now resolves against the migrated file's own directory.

- **Sibling markers could share one instance through a recycled `id()`.** Both engine memos key on
  `id(marker)`, which is unique only while the marker is alive; the engine builds short-lived
  broadcast copies, and CPython reuses a freed object's address, so the second item of a list could
  land on the first one's address and read as a memo HIT. Measured on a three-stage pipeline: every
  stage came back as stage one. Markers the memos key on are now pinned for the pass
  (`_EngineState.memo_keepalive`). Pinned by
  `tests/test_ref_identity.py::test_sibling_list_items_do_not_share_an_instance_via_recycled_ids`.

### Added

- **`confluid-migrate` — a codemod that rewrites tagged configs into the plain format
  (2026-08-11).** ``confluid-migrate config/`` converts a directory in place;
  ``--check`` reports without writing (exit 1 if anything would change), ``--report
  out.csv`` records every converted site, and ``--verify`` proves the conversion.

  It **edits lines rather than reparsing**, so everything it does not convert stays
  byte-identical — comments, key order, spacing and quoting. That is not a
  preference: a comment-preserving round-tripper changes 34 lines of a real
  458-line config on a *no-op* load+dump, and these files are ~86% comments.

  ``--verify`` compares the resolved marker trees of the old and new documents
  **once per scope activation the document declares** — a wrong conversion inside a
  variant block is invisible to a plain resolve, because the block is dropped
  before any marker is built. A file whose conversion is not provably equivalent
  is left untouched and reported.

  Two forms need a hand edit and are reported with their line rather than guessed
  at: a `!scope:` block with a sequence or scalar body (needs `_content_`), and
  the quoted-string marker spelling. An `@axis=value` target selector needs no
  migration — it lives in the target string, so `_target_: Loss@framework=keras`
  is ordinary YAML and the registry reads it unchanged. The
  tool also makes every environment read explicit (`${DATA_ROOT}` / `$DATA_ROOT`
  → `${env:DATA_ROOT}`), which is meaning-preserving today and is what removes
  every ambiguous spelling ahead of the eventual bare-`${name}` flip.

  Measured over this workspace: 624 sites across 56 of 61 config files, with 3
  findings — all the documented sequence/scalar scope bodies.

- **A plain-YAML config format — no custom tags (2026-08-11).** A document may now
  be written with reserved mapping keys instead of YAML tags, which makes it
  ordinary YAML that `yaml.safe_load`, `yq`, editor schemas and linters can read.
  A tagged document cannot be: `yaml.safe_load` on `!class:MLP` raises
  `ConstructorError: could not determine a constructor for the tag`.

  ```yaml
  seed: 7
  model: {_target_: MLP, hidden: 32}          # was: !class:MLP(hidden=32)
  optimizer:                                   # was: !lazy:SGD(lr=0.5)
    _target_: SGD
    _partial_: true
    lr: 0.5
  alias: ${ref:model}                          # was: !ref:model
  copy: {_clone_: model, hidden: 64}           # was: !clone:model + a block
  variant:                                     # was: !scope:size=big
    _scope_: {size: big}
    model: {_target_: MLP, hidden: 512}
  ```

  The keys are `_target_` / `_partial_` (construction), `_ref_` / `_clone_`
  (references, with `${ref:…}` / `${clone:…}` as scalar shorthand), and
  `_scope_` / `_notscope_` (conditional blocks). Every other key in the mapping
  becomes the marker's kwargs; a mapping carrying none of them is an ordinary
  dict, untouched.

  **`_scope_` takes a MAPPING of dimension → value**, not the tag form's
  `KEY=VAL` string — so it is data rather than a grammar packed into a scalar,
  and several dimensions are ANDed for free (`{framework: mlx, model: convnet}`
  replaces a block nested inside another block). `{debug: }` is a boolean
  dimension. A value YAML would read as a boolean is rejected with a quote-it
  message: unquoted `{extra: yes}` becomes `True` and would never match the
  `extra=yes` an activation passes.

  **A LIST whose first item is a `_scope_` mapping is a scope block**, the
  remaining items its body — the only way to write a conditional list *item*,
  since a YAML node is a mapping or a sequence and never both:

  ```yaml
  ops:
    - always_first
    - - _scope_: {extra: enabled}
      - extra_a
      - extra_b
    - always_last
  ```

  This replaced a `_content_` key that briefly held the same thing. The list
  shape needs no reserved key, no second body-shape rule, and no scalar special
  case (a scalar body is a one-item list, and the resolver already extends).

  Both spellings produce the **same** Fluid markers, so broadcasting, scopes,
  interpolation, `configure()` and `dump()` are unchanged and cannot tell them
  apart — and the two may be **mixed in one file**, which is what makes a
  file-by-file migration safe. Parity is pinned per construct
  (`tests/test_plain_format.py::test_both_spellings_agree`). The tag spelling
  still loads and is not yet deprecated.

  Three details worth knowing: the conversion rides the default mapping tag and
  decides from the YAML *node's* key names, so ordinary mappings keep PyYAML's
  own constructor (and its alias behaviour) untouched; markers built this way are
  stamped with their source location, so diagnostics are unchanged; and a
  malformed marker raises a **located `ConfigurationError`** rather than degrading
  silently — the opposite of the tag grammar's behaviour, where one space in
  `!class:Model(a=1, b=2)` produced a target named `Model(a=1,` and dropped both
  kwargs with no error at all.

  See `docs/plain-format.md`, `examples/plain_format.py`, and architecture
  record 11.

- **`${env:NAME}` / `${env:NAME,default}` environment reads** (alias
  `${oc.env:…}`). An explicit, unambiguous spelling for an environment variable,
  independent of what a bare `${NAME}` is taken to mean. Checked before the
  dotted-name test, since `oc.env` contains a dot.

- **`${ref:path}` / `${clone:path}`** — scalar shorthand producing the same
  markers as `!ref:` / `!clone:`, so identity semantics are identical. A
  reference must be the whole value; embedding one in a larger string raises
  rather than stringifying an object.

### Changed

- **`include:` pastes the included document AT ITS LINE (2026-08-11).** An
  `include:` now behaves as if the included document were pasted into the source
  document at that line — the same "splice at the wrapper's slot" rule `!scope:`
  blocks follow, and the only reading consistent with confluid's one precedence
  rule. Two defects went with the old behaviour:

  - the directive's **position was discarded** (it was popped and the whole
    including file merged over the result), so writing it first or last made no
    difference and the including file always won — a config could not express
    "these are my fallbacks, let the shared file win" at all;
  - an **overridden key kept the INCLUDED file's position**, because `deep_merge`
    assigned into a copy of the base and Python preserves a key's position on
    assignment. So an override written after the include lost to an addressed
    block inside it, while the same document written flat gave the opposite
    answer — with no diagnostic on either path.

  ```yaml
  # base.yaml       # main.yaml, include FIRST     # main.yaml, include LAST
  lr: 0.1           include: base.yaml             lr: 0.3
  Stage:            lr: 0.3                        s: !class:Stage()
    lr: 0.2         s: !class:Stage()              include: base.yaml
                    # -> s.lr = 0.3                # -> s.lr = 0.2
  ```

  `loader._splice_includes` cuts the document at the directive's slot and folds
  the segments with `deep_merge`, which now re-anchors an overridden key at the
  overlay's position — so a key written on both sides survives once, at the later
  position, with the later value. Nested blocks still deep-merge.

  **Migration.** The shape that changes is a config whose lines **above** the
  `include:` set a key the included file also sets: those lines are now
  overridden by the paste, where before the including file always won. Move the
  directive to the top to keep the old meaning.

  Measured across every config in the reference workspace that uses `include:`
  (14 loadable; the directive sits on lines 2–70, so mid-file placement is
  common): **key order changes in all of them and no value changes in any**,
  because none re-states a key its include also sets. Expect the same shape of
  result elsewhere — a reordering that is usually inert, and a genuine change
  only where a key is set on both sides of the directive.

  A front-end that re-seats CLI override keys at the end of the document to work
  around the old positioning can keep doing so — the operation is now a no-op,
  not a conflict. Docs: `docs/interpolation.md` → "`include:` and document
  order"; architecture record 6.

- **`python-dotenv` is no longer a runtime dependency** — it moved to the new
  `confluid[env]` extra. It was only ever used by `confluid.env.load_workspace_env`
  (a `.env`-walking convenience helper); nothing in the configuration engine
  imports it, so every installation of confluid was paying for a helper most
  consumers never call. `confluid.env` still imports without the extra; only the
  call raises, with an `ImportError` naming it. Runtime dependencies are now
  `pyyaml`, `loggair` and `typing-extensions`.

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

### Changed

- **`resolve()` no longer instantiates a dotted `!ref:a.b` (2026-08-11).** It is
  documented as "markers returned, NOTHING constructed", but reading `split.train`
  means BUILDING `split`, and that branch ran on this path too. A dotted reference
  now stays a `Reference`, exactly as a plain `!ref:name` already did.

  Measured on a real config whose split scans 37 rar archives: `resolve()` took
  **3.9s** for a call that constructs nothing; it is now 10ms (the first call in a
  process still pays one-time imports of the classes the config names).

  `materialize()` / `load()` are unchanged — a dotted ref still resolves off ONE
  shared instance there, which is the whole point of writing `split.train` and
  `split.val`.

  Two consumers already documented behaviour they were not getting (a visual
  editor's YAML importer: "no instantiation — every node is a Fluid marker"; a
  flow-graph builder: "step markers stay UNbuilt"), and two projects' shipped-config
  test suites hand-rolled a tag-stubbing YAML parser specifically because
  `resolve()` "is not an escape either, since it instantiates a dotted `!ref:a.b`".
  Those workarounds can now be deleted.

### Fixed

- **Two classes with the same `module.QualName` no longer share an accept-list
  (2026-08-11).** The five per-pass introspection caches (`_acceptable_keys_cache`,
  `_post_init_attrs_cache`, `_param_kind_cache`, `_declared_names_cache`,
  `engine._parent_blacklist_cache`) keyed on that dotted name, which is not unique —
  the registry has always known this (`_claim_key` suffixes `~N`) because two
  classes defined in ONE scope share it: a class factory, a plugin loader building
  classes in a loop, a decorator that rebuilds a class, a parametrised fixture.
  The second class was served the first one's accept-list, so it was built on its
  defaults and had a FOREIGN key `setattr`-ed onto it, silently. All five now key
  on `broadcast._cache_key(target)` — the target object itself.

- **`get_hierarchy()` returned `{}` for every registered builder FUNCTION
  (2026-08-11).** `register()` / `@configurable` stamp `__confluid_configurable__`
  on the function object, which disqualified it from `schema.py`'s callable branch;
  the class branch then read `types.FunctionType.__init__` = `object.__init__` =
  `(*args, **kwargs)` and filtered every parameter away. The same target was fully
  visible to `input_specs` / `to_pydantic` / `parse_param_docs`, so only the
  `get_hierarchy` surface (a CLI `--help` view) lost it. Both walkers in
  `schema.py` now dispatch through `introspect.init_callable`, which exists to
  prevent exactly this.

- **A glob-registered unused-candidate is satisfied by its LEAF (2026-08-10).**
  `ConfigurationReport.mark_used("lr")` now also satisfies registered `**.lr` /
  `*.lr` candidates. The nested-marker cascade delivers from a pool in which
  rider contents are flattened to bare keys, so it can only mark the leaf — a
  `'**'` rider whose content landed everywhere it aimed still reported its
  glob spelling unused in the same report that showed the delivery (found by
  the first real consumer of `collect_report()`, a CLI front-end asking
  "did this override reach anything?").

- **Three silent fallbacks now say so.** `configure()` with a non-mapping
  config (the canonical miss: `configure(model, config="overrides.yaml")` — a
  plain filename fails the YAML heuristic and NOTHING was applied, with an
  empty report reading as success) warns and names `configure_from_file` for
  the string case. A nested-broadcast result dropped by a refusing setattr
  (`_broadcast_onto_instance`'s read-only/`__slots__` swallow) and a
  `get_type_hints` failure in the param-kind scan (which silently flips a
  dict-annotated param from value to routing) each log at DEBUG.

- **A `'**'` rider now orders against a slot-addressed mapping by document
  position — on BOTH paths (the D7 adjudication, 2026-08-10).** The contest was
  position-insensitive with OPPOSITE winners: under `load()` the mapping always
  won (the late-keys verdict kept BARE top-level keys only, and rider contents
  sit under the dict-valued `'**'` entry — invisible), under `configure()` the
  rider always won (the beaten verdict kept non-dict keys only, and the rider
  entry IS a dict — never beaten). A sweep's `'**': {lr: …}` override therefore
  silently lost on one path and silently beat a later per-slot choice on the
  other, for the identical document. Both verdicts now read the ONE candidate
  set, `broadcast._cascade_scalar_positions` — bare keys at their own index, a
  rider's scalar contents at the RIDER's index — with the per-path directional
  read unchanged. Pinned as a rider × spelling × ordering × path matrix in
  `tests/test_document_order.py`.

- **`configure()` applies values BEFORE `solidify()` fires.** Since the
  flow-pass-through solidify (0.3.0's record-2 change), the configure walk's
  internal `flow(obj)` finalized every live object BEFORE the pass applied its
  values — an unsolidified object baked its pre-configure state
  (`configure(m, {"width": 32})` left `backbone(width=8)` where the load path
  builds `backbone(width=32)`), and the idempotency contract then kept it. The
  walk now flows with `solidify=False` and re-fires the hook post-order, once
  the object and its subtree carry the new values. An object solidified before
  the call keeps its built state — rebuild-on-reconfigure remains the
  recompute-property convention's job, not `solidify()`'s.

- **A function-OBJECT marker target is introspected as itself, everywhere (the
  D6 adjudication, 2026-08-10).** Five of the six "normalize `marker.target`"
  sites degraded a plain callable to `None` (`resolve_class` is
  string/type-only), so a code-built `LazyClass(builder_fn, …)` slot ran the
  engine cascade with NO `NoBroadcast` gates — against what `accepts_broadcast`
  answered for the same key — and `configure()` could not tune the slot at all,
  while the identical class-target slot behaved. All six sites now normalize
  through the one callable-aware `broadcast._settability_target`.

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

- **`load_configurables(group="confluid.configurables")` — the entry-point
  bootstrap now ships IN confluid.** Packages have long advertised their
  `@configurable`-bearing modules under `[project.entry-points.
  "confluid.configurables"]`, but the loop that iterates the group lived in a
  consumer; the group carries confluid's name, so confluid now owns the
  loader. One call imports every declared module (running its registration
  side effects) and returns `{entry_name: module}`. Errors are collected PER
  ENTRY: a broken import logs one warning and lands in the dict as the
  exception instance, so one broken package never blanks the other packages'
  registrations. Explicit call only — never invoked at confluid import — and
  repeated calls are cheap (Python's module cache makes re-imports no-ops).

- **Bare `$VAR` expands as an environment variable in the one interpolation
  pass.** After the `${...}` handling, a bare `$IDENTIFIER` in a string value
  reads `os.getenv` — an unset variable leaves the `$name` text literal,
  mirroring `os.path.expandvars`. Env-only by design: no dotted config-path
  form and no `:default` (those spellings stay `${...}`-exclusive), and marker
  strings (leading `!`) are exempt so flow-time parsing — where the
  `@axis=$key` document-selector grammar also spells `$` — keeps its text.
  The reason: 59+ live workspace YAML lines spell `root: $DATA_ROOT/...`,
  which worked only through front-ends that re-implement
  `os.path.expandvars` — the same file through a direct `confluid.load()`
  kept the literal and produced the canonical zero-records failure. One
  spelling now behaves identically on every entry path.

- **`marks(target)` / `Marks` — the ONE public read surface for the
  `__confluid_*__` stamps.** Six projects were reading the dunders raw
  (`getattr(cls, "__confluid_role__", None)` and friends), so a rename would
  have broken every one with no deprecation path. `marks()` accepts a class,
  decorated callable, or instance and returns a frozen record of all sixteen
  marks with typed defaults; the dunder names are now explicitly internal.

- **`declares_key(target, key)` — the fourth settability predicate.** What the
  target NAMES (ctor params, settable properties, body slots), with the
  `**kwargs` catchall never counting — the question between `accepts_key`
  (which answers yes to everything a catchall cannot refuse) and
  `accepts_any_key`. A consumer sizing torchmetrics templates (every metric
  takes `**kwargs` and raises "Unexpected keyword arguments" for undeclared
  names) had re-derived exactly this locally; per the never-re-derive rule the
  gate now lives beside its siblings.

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

- **Eight more never-imported re-exports dropped from `confluid.engine`**
  (2026-08-10, the third pruning tranche): `_expand_block_keys`,
  `_get_acceptable_keys`, `_get_post_init_attrs`, `accepts_key`,
  `accepts_broadcast`, `accepts_any_key`, `active_context`, `collect_report`.
  Each grep-verified dead as an ENGINE name — no engine-body use and no
  `from confluid.engine import` site anywhere (workspace Python, notebooks,
  YAML). The public names are unaffected: all live in `confluid` top-level
  and their real homes (`confluid.broadcast`, `confluid.state`).

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

### Documentation

- **`docs/lifecycle.md` — the map (2026-08-11).** Twenty-one correct topic pages
  with nothing tying them together: no page described the ORDER of the passes, and
  the repo had no diagram at all. The new guide gives the nine passes
  (parse → import → include → scope → interpolate → expand → broadcast → flow →
  solidify), what each one decides permanently, where you can stop
  (`load_config` / `flow=False` / `resolve()` / `load()` / `solidify=False` /
  `configure()`), and the questions the order answers — why `${...}` burns in, why
  a scope block cannot read an interpolated value, why an included file wins or
  loses. Its runnable twin `examples/lifecycle.py` walks one document through every
  stage and ASSERTS each invariant. Cross-linked from the topic guides and listed
  first in the README index.

- **`AGENTS.md` re-split into RULE / PINS / WHY (2026-08-11).** It had grown to
  108 KB of rule, history, measurement and pins interleaved in single paragraphs —
  several mandates were longer than the topic guide they pointed at, and the
  rationale was restated beside every link to the architecture record that already
  held it. Same rules, same 35 test-file and 32 test-method pins, half the size.

- **`examples/modular_includes/` gained the README its own convention requires**,
  and now demonstrates the include-ordering rule with assertions rather than only
  composition.

- **Three new topic guides, each with a runnable example twin** (2026-08-10 —
  closing the review's largest doc gaps): `docs/configure.md`
  (post-construction configuration — the first key-feature bullet finally has
  a page: call surface, the one matching rule over live objects,
  deferred-slot tuning, values-before-finalize ordering, layering) with
  `examples/configure.py`; `docs/schema-export.md` (`to_pydantic`,
  `parse_param_docs`, `validate_model`, `sanitize_schema`) with
  `examples/schema_export.py`; and `docs/serialization.md` (the dump/reload
  round trip, what a dump omits and why) pointing at
  `examples/reproducible_experiment.py`. All three are in the README index.
- **`scope_aliases` is documented** (`docs/scopes.md` + an
  `examples/scopes.py` demo) — the top-level alias map existed since the
  scopes engine landed but was described only by its circular-chain error.
- **Drift fixes**: the README no longer names the nonexistent
  `ClassReference` type (the marker family is `Class`/`Instance`/`Lazy`/
  `Reference`/`Clone`); `examples/ml_pipeline.py` no longer claims the
  configuration machinery executes property getters (it walks instance
  attributes only — the recompute rule is about ordinary domain reads);
  `report.py`'s origin vocabulary lists `"deferred slot"` and `"own"`; the
  `_confluid_lazy_params` stamp's comment no longer claims a downstream
  serializer consumer that does not exist (it is queryable model metadata via
  `lazy_param_names_of`).
- **`AGENTS.md` deduplicated against `docs/architecture.md` (2026-08-10).**
  Eight mandate paragraphs carried the full narrative — failure stories,
  measurements, rejected alternatives — that their architecture record also
  carries, and the two copies had measurably drifted (AGENTS named a
  `Fluid._beaten_bare_keys` field that never survived). Each mandate now
  states its rules, discriminators, and pins and points at its record for
  rationale, history, and measurements; facts that lived only in AGENTS were
  folded into the records in the same pass.

### Internal

- **Materialization is ~22 % faster (2026-08-11), measured, no design change.**
  Two hot-path repairs on `examples/performance.py`'s 2,500-marker tree
  (305 ms → 238 ms):

  - **Per-key TRACE diagnostics are gated.** Python evaluates the f-string before
    the logger can filter, and loggair's handlers sit at TRACE with a filter, so
    loguru's own min-level fast path never fires — 10,000 records per pass were
    built in full, dispatched to both handlers, and discarded (30.1 % of the pass,
    isolated by measurement). The four per-key sites now check a gate recomputed
    once per pass by `clear_pass_caches()` from `loggair.get_active_config()`. A
    swapped-in logger is never gated, so log-asserting tests are unaffected.
  - **`broadcast._same_target` is memoized** through the existing
    `register_pass_cache` mechanism. It was the one uncached introspection helper
    in the module and was asked once per (view entry × marker) — 113,000 times per
    pass, 50,002 of which reached `resolve_class` (9.5 %).

- **The cascade corner runs on shared primitives (2026-08-10 — the review's P1
  batch, closing the residue the D6/D7 defects lived in).** Five
  consolidations, all behavior-preserving: (1) per-pass cache clearing is ONE
  registered `broadcast.clear_pass_caches()` — modules owning a cache
  self-register (`engine._parent_blacklist_cache` does) and every entry point
  fires it, **now including `configure()`**, which cleared nothing: a
  same-qualname class redefined between calls (a notebook cell re-run) was
  served its previous definition's accept-list with no diagnostic; (2) the
  "mapping tunes a deferred marker" idiom is ONE `broadcast.tune_marker`
  (the engine's post-init tune and the live sink's `dict_at_slot` carried
  twin inline copies, already cosmetically drifted); (3)
  `_hoist_block_routing`'s inline `'**'` merge goes through `_merge_routing`
  (a fifth copy of the rider-merge idiom Phase B extracted); (4) the
  report's glob-prefix used-key spelling is ONE `broadcast._mark_used_key`
  beside the origin-label constants it parses (both sinks carried a
  byte-identical parsing expression — a scanner label rename would have
  silently broken unused-tracking in two modules); (5) the scanner's
  `gated`/`floating` boolean pair is the closed `_Delivery` Literal
  (`addressed`/`glob_one`/`rider`) on `_consume` and the receiver
  `dict_slot` predicate — three real states, self-documented.

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

### Changed

- **`resolve()` no longer instantiates a dotted `!ref:a.b` (2026-08-11).** It is
  documented as "markers returned, NOTHING constructed", but reading `split.train`
  means BUILDING `split`, and that branch ran on this path too. A dotted reference
  now stays a `Reference`, exactly as a plain `!ref:name` already did.

  Measured on a real config whose split scans 37 rar archives: `resolve()` took
  **3.9s** for a call that constructs nothing; it is now 10ms (the first call in a
  process still pays one-time imports of the classes the config names).

  `materialize()` / `load()` are unchanged — a dotted ref still resolves off ONE
  shared instance there, which is the whole point of writing `split.train` and
  `split.val`.

  Two consumers already documented behaviour they were not getting (a visual
  editor's YAML importer: "no instantiation — every node is a Fluid marker"; a
  flow-graph builder: "step markers stay UNbuilt"), and two projects' shipped-config
  test suites hand-rolled a tag-stubbing YAML parser specifically because
  `resolve()` "is not an escape either, since it instantiates a dotted `!ref:a.b`".
  Those workarounds can now be deleted.

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

### Changed

- **`resolve()` no longer instantiates a dotted `!ref:a.b` (2026-08-11).** It is
  documented as "markers returned, NOTHING constructed", but reading `split.train`
  means BUILDING `split`, and that branch ran on this path too. A dotted reference
  now stays a `Reference`, exactly as a plain `!ref:name` already did.

  Measured on a real config whose split scans 37 rar archives: `resolve()` took
  **3.9s** for a call that constructs nothing; it is now 10ms (the first call in a
  process still pays one-time imports of the classes the config names).

  `materialize()` / `load()` are unchanged — a dotted ref still resolves off ONE
  shared instance there, which is the whole point of writing `split.train` and
  `split.val`.

  Two consumers already documented behaviour they were not getting (a visual
  editor's YAML importer: "no instantiation — every node is a Fluid marker"; a
  flow-graph builder: "step markers stay UNbuilt"), and two projects' shipped-config
  test suites hand-rolled a tag-stubbing YAML parser specifically because
  `resolve()` "is not an escape either, since it instantiates a dotted `!ref:a.b`".
  Those workarounds can now be deleted.

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

### Changed

- **`resolve()` no longer instantiates a dotted `!ref:a.b` (2026-08-11).** It is
  documented as "markers returned, NOTHING constructed", but reading `split.train`
  means BUILDING `split`, and that branch ran on this path too. A dotted reference
  now stays a `Reference`, exactly as a plain `!ref:name` already did.

  Measured on a real config whose split scans 37 rar archives: `resolve()` took
  **3.9s** for a call that constructs nothing; it is now 10ms (the first call in a
  process still pays one-time imports of the classes the config names).

  `materialize()` / `load()` are unchanged — a dotted ref still resolves off ONE
  shared instance there, which is the whole point of writing `split.train` and
  `split.val`.

  Two consumers already documented behaviour they were not getting (a visual
  editor's YAML importer: "no instantiation — every node is a Fluid marker"; a
  flow-graph builder: "step markers stay UNbuilt"), and two projects' shipped-config
  test suites hand-rolled a tag-stubbing YAML parser specifically because
  `resolve()` "is not an escape either, since it instantiates a dotted `!ref:a.b`".
  Those workarounds can now be deleted.

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
