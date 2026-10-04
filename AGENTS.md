# Confluid Mandates

Rules only. The reasons are in [`docs/architecture.md`](docs/architecture.md) (cited as §N), the
usage in the README and `docs/*.md`. `docs/lifecycle.md` is the map — the nine passes a document
goes through; most rules below belong to one pass. Each rule names its pin (the test that fails
if you break it): run the pin, don't re-derive the behaviour. Root `AGENTS.md` rules are not
repeated here.

## Current state

The hierarchical YAML configuration + dependency-injection engine: the `@configurable` /
`register` registry, the ONE door `load(x, until="raw"|"document"|"settled"|"objects")`, the node
builder `flow()`, scoped broadcasting under one precedence rule, post-construction `configure()`,
the `dump()` round trip, and the introspection surface every AI- and GUI-facing consumer reads
(`to_pydantic`, `parse_param_docs`, `sanitize_schema`, `input_specs`/`output_specs`, the
settability predicates). Feature-complete; open work is in `TASKS.md` and the `BUGS-*.md`
inventories.

- **Two spellings, one IR.** Tags (`!class:` / `!partial:` (alias `!lazy:`) / `!ref:` /
  `!scope:` / `!notscope:`) are the authoring form; reserved keys (`_target_` / `_partial_` /
  `_ref_` / `_scope_` / `_notscope_`) are the machine form `hydraide` emits. Both are first-class
  input. §11, §19.
- **`hydraide`** — `confluid.hydraide.emit/check`, and the command `confluid/cli.py` (Click, the
  `confluid[cli]` extra, console script `hydraide = confluid.cli:main`): `emit CONFIG [--scope
  DIM=VALUE]… [-o FILE]`, `check CONFIG`, `completion bash|zsh|fish`. `confluid-bake` is
  `confluid/bake.py`.
- **Released on PyPI, latest v0.4.0**, public repo `Gearlux/confluid`. Update this line in the
  same change as a version bump. New work opens an `[Unreleased]` CHANGELOG section; a release
  proposes its version with rationale and waits for the user's confirmation before tagging.
- **Performance baseline:** `examples/performance.py` + `docs/performance.md`
  (`CONFLUID_BENCH_PROFILE=1` adds a cProfile pass).

**Module map.** Layering `fluid → state → broadcast → engine → loader`, one direction only (§3):

| Module | Owns |
|---|---|
| `fluid` | marker data classes (`Fluid`/`Target`/`PartialClass`/`Reference`/`ScopeBlock`), `format_yaml_loc`. Imports no other confluid module. |
| `state` | the one engine `ContextVar` (`_ENGINE_STATE`), `active_context`, `collect_report` |
| `broadcast` | the ONE precedence rule and its machinery (scanner, views, accept-lists, predicates). Materializes nothing. |
| `engine` | `flow`/`cast`; `materialize` (passes 7–9) / `settle` (pass 7) — the prepared-data entries `load()` calls, not exported; `_flow_recursive` (settle), `instantiate` (build) |
| `loader` | YAML parsing and composition (includes, imports, scopes glue) and `load`, which runs passes 1–6 |
| `introspect` | stdlib-only signature/AST scanning: `slots`, `init_callable`, `scan_init_body`, `marked_param_names` |
| `configurator` | `configure` / `configure_from_file` (objects → document → pass 7 → apply) |
| `dumper` | `dump()`, `dumpable_kwargs` (the one object→document reconstruction), `to_markers` |

## Standing user rulings — do not re-litigate

- **A configuration utility, not a compiler (user ruling 2026-08-20).** ONE precedence rule —
  document order, last spec wins — and few spellings. An ambiguous spelling gets a located
  refusal or a documented limit, never an invented meaning. Unless the user asks in that change,
  never add a second precedence rule or exemption, per-key/provenance bookkeeping to make two
  spellings agree, a new spelling for a corner case, or a walker that reaches through a marker,
  an object or a string (full ruling: root `AGENTS.md`; the deferred BC10/BC11 in `TASKS.md`).
- **SR16 — the `KEY(VAL)` scope call form is removed (user ruling 2026-08-20).** An activation is
  `dim` or `dim=value` in every spelling (tag suffix, `_scope_` value, `--scope`,
  `default_scopes:`); anything else is a located `ScopeError`. `scopes.parse_scope_arg` is the one
  splitter (`loader._parse_scope_suffix` delegates). Never add a second.
  (`tests/test_scopes.py::test_a_malformed_activation_string_is_refused_in_every_spelling`)
- **BC12 — a wrapper shield no longer beats a later sweep (user ruling 2026-08-20).** A kwarg on a
  wrapper block the wrapper does not accept shields its subtree only from the sweeps it
  OUT-POSITIONS; a bare key or `**` rider written after the wrapper wins, in both spellings.
  (`tests/test_broadcast_wrapper_override.py::test_a_shield_only_beats_the_sweeps_it_out_positions`,
  `::test_override_at_wrapper_shields_inner_classes`)
- **BC13 — `optimizer: {x: 1}` at a marker-holding slot is refused as ambiguous (user ruling
  2026-08-20).** Unless the key IS that node's instance name (the kwarg or the ctor-default
  `name`), the scanner raises a located `ConfigurationError` naming `c.optimizer.x: 1` (set an
  attribute) and `c.optimizer: !class:...` (replace). The root-dotted `optimizer.x: 1` is refused
  alike. Nothing more is built on top of the precedence engine for it.
  (`tests/test_broadcast_scoping.py::test_a_bare_mapping_at_a_marker_slot_is_refused_as_ambiguous`,
  `::test_an_instance_name_block_that_matches_the_node_is_not_ambiguous`,
  `::test_configure_refuses_the_ambiguous_bare_mapping_too`)
- **Zero-arg construction is a recommendation, not a requirement (user ruling 2026-08-11)** — see
  the next section.
- **Clone is removed (user ruling 2026-08-15).** There is no copy marker: independence is a second
  marker (`<<:` an anchored base for a long recipe), sharing is `_ref_` / `${ref:}`. Never
  reintroduce one. The removal stays LOUD: `_clone_` stays in `loader.RESERVED_KEYS` and `clone`
  in `resolver._MARKER_RESOLVERS` only so every spelling is refused with a location — they are not
  dead entries. (the Clone-removal group in `tests/test_plain_format.py`) §18.

## Lazy Initialization & Zero-Arg Construction

- **Post-construction paradigm.** Configuration MUST apply to already-built objects; never
  require constructor-time injection. (This is about configuration, not type validation.)
- **1 — Requirement.** The constructor only stores values: no I/O, network, file reads,
  dataset/model materialization or heavy compute.
- **2 — Recommendation (user ruling 2026-08-11, do not re-litigate).** Prefer `Cls()` working with
  every parameter defaulted and the value needed to *run* validated lazily where it is used. A
  class that keeps a required constructor parameter is NOT a violation: never report, count or
  track one. Suggest the lazy shape only for a new class or a refactor.
- **3 — Requirement.** Derived state is a read-only `@property` recomputed from current inputs; it
  may memoize into a private `_backing` field only when recomputation is expensive and the inputs
  are stable by first use.
- **4 — Requirement.** A slot is a constructor param OR an `__init__`-body attribute. A body slot
  needing runtime injection holds a FRESH `PartialClass(...)` per instance, annotated
  `Partial[T]` (a plain `Target` body attribute is built).
- **`configure()` never runs a property getter** — not in dump, discovery or apply; a setterless
  property is skipped by both accept-lists.
- Pins: `tests/test_lazy_convention.py`,
  `tests/test_pydantic_export.py::test_to_pydantic_surfaces_post_init_body_slots`,
  `tests/test_configurator.py::test_configure_never_executes_property_getters`. Usage:
  `docs/class-design.md`.
- **Eager classes are first-class.** A plain class (required params, real work in `__init__`) is
  supported by `load`/`flow`/`dump`: a missing param is a located `ConstructionError`, and `dump()`
  round-trips through the captured ctor kwargs (`__confluid_kwargs__`, stamped by the engine AND
  the validation wrap, in every validation mode). Such a class SHOULD declare
  `@configurable(eager=True)`. (`tests/test_eager.py`; `docs/eager-classes.md`)

## Validation

- Every `@configurable` `__init__` is validated against `to_pydantic(cls)`; three points (init /
  YAML / tool), each strict/warn/off via `set_policy()` or `CONFLUID_VALIDATE_INIT/_YAML/_TOOL`.
  Opt out with `validate=False` only for an intentionally untyped constructor. Put constraints on
  the annotation (`Annotated[T, Field(...)]`), never a hand-rolled `if x < 1: raise`.
- Pydantic is the optional `confluid[pydantic]` extra: without it all three points are off (one
  log line) and `to_pydantic`/`confluid_class_of` raise `ImportError` naming the extra. A package
  relying on validation or export depends on `confluid[pydantic]`.
  (`tests/test_optional_pydantic.py`)
- A class whose mirror cannot be built still constructs and warns ONCE: `validation._model_or_none`
  is the one place the hooks call `to_pydantic`, and it catches `Exception`. `set_policy()`
  normalizes through `_normalize_mode`, so a typo raises `ValidationModeError`.
  (`tests/test_validation.py::test_a_class_whose_mirror_cannot_be_built_still_constructs_and_says_so_once`,
  `::test_set_policy_refuses_a_typo_like_the_env_var_does`)
- **A string accepted for a number or a bool is passed as that value.** `validate_kwargs` returns the kwargs whose
  `str` validated into an `int`/`float`/`bool` (`validation._accepted_scalar`); both wrappers pass — and capture —
  those, and `validate_setattr` returns the value `configure()` sets. Only `str` → `int`/`float`/`bool`: never adopt
  pydantic's other coercions (an `int` for a `float` stays an `int`, a `Union[float, str]` or `Union[bool, str]` keeps
  its text); nothing under `off`, nothing after a `warn` failure. §28. (`tests/test_accepted_strings.py`)
- The YAML-mode switch is context-local: `override_init_mode` sets the `_init_override`
  ContextVar that `get_policy()` overlays; `set_policy()` reads `_process_policy()`. Never a global
  swap. (the X2 group in `tests/test_concurrency.py`; `docs/validation.md`, `docs/concurrency.md`)

## Engine structure

- `broadcast` materializes nothing (no `flow`, no `_flow_recursive`); code that builds belongs in
  `engine`. The engine imports nothing from `loader`.
  (`tests/test_load_stages.py::test_the_engine_no_longer_imports_the_loader`) §3.
- `flow()` is a dispatcher over `_flow_*` helpers; put new marker behaviour in a helper.
- Names that had users stay re-exported from `engine` (`# noqa: F401`); new code imports from the
  real home; zero-user re-exports are pruned (the CHANGELOG is the ledger). A test capturing
  broadcast diagnostics patches `confluid.broadcast.logger`.
- Engine state is ONE `ContextVar`: inherited by asyncio tasks and `asyncio.to_thread`, not by a
  raw `Thread`/`run_in_executor` (use `copy_context().run` or `active_context`). `active_context(ctx)`
  is the only sanctioned activation — no reach-ins — and it does not enable broadcasting (pass
  the document: `load(fluid, context=document)`). (`docs/concurrency.md`)
- **Every id()-keyed store pins the object it keys on**: the engine memos append to
  `_EngineState.memo_keepalive` at every write site (pass-7 `flow_memo` included);
  `configure()`'s visited store is `Dict[int, Any]` holding the object; `introspect._slots_cache`
  stores `(target, slots)`; the `_order_resolved` stamp goes on markers only. A new id-keyed store
  ships with its pin. (`tests/test_memo_pinning.py`,
  `tests/test_ref_identity.py::test_sibling_list_items_do_not_share_an_instance_via_recycled_ids`) §16.
- **Per-pass caches key on the target OBJECT** (`broadcast._cache_key`), never a dotted name;
  register with `broadcast.register_pass_cache(...)`; the one clear site is
  `broadcast.clear_pass_caches()` (fired by `materialize`, `settle` and `configure()`). Every
  read is ONE `.get()`, with `broadcast._CACHE_MISS` where `None` is a real answer.
  (`tests/test_duplicate_names.py::test_same_qualname_classes_do_not_share_an_accept_list`; the X4
  group in `tests/test_concurrency.py`)

## `load()` — the one door (§20, §25)

- Every stop point is `load(x, until=<Stage>)`; `Stage` is the Literal, `_STAGES = get_args(Stage)`
  the one runtime tuple, an unknown stage raises `ConfigurationError`. Every stage takes a path,
  YAML text or parsed data, and passes already applied are idempotent. Never add a second entry
  name, or a stage reachable for one input shape only.
- Parsed-data input is COPIED on entry (`merger.document_copy`; markers through a memo, an
  explicit `context` through the same memo).
- Passes 5–6 run ONCE, in `loader._load`, for `data` and an explicit `context`; `engine.materialize`
  / `engine.settle` run neither, so runtime kwargs handed to `flow()` keep their text.
  (`tests/test_load_stages.py::test_the_engine_entries_run_neither_pass_5_nor_pass_6`,
  `::test_runtime_kwargs_of_a_bare_type_are_code_not_document`)
- A `Path`, or a one-line `str` ending in `.yaml`/`.yml`, NAMES A FILE at any length — a missing
  one raises `ConfigFileNotFoundError`; any other `str` is probed on the search tiers, else parsed
  as text (`loader._names_a_file`). (`tests/test_load_stages.py`)
- `texts=` (held, unsaved files): read at exactly two sites — `loader._exists` and
  `_load_config_file` (via `_HeldStream`, so every location names the real file). A NEW site that
  reads a config file MUST read through `_held_text`. Never stage a held text on disk. A key
  matches the resolved path as spelled (documented limit on case-folding disks).
  (`tests/test_held_texts.py`)

## Slots, signatures and annotations

- **One slot enumeration:** `introspect.slots(target)` → `Slot(name, kind, annotation, default,
  source, owner)`. A reader states its rule as a KIND SET (`slot_names(target, {...})`), never a
  re-derived filter; `body_slot_names` answers "what does the body assign". Only a kind excludes a
  slot, never a name (projections `pydantic_export._FIELD_KINDS`, `dumper._DUMP_KINDS`).
  (`tests/test_introspection_agreement.py`, incl.
  `::test_a_param_literally_named_args_or_kwargs_is_a_slot_for_EVERY_reader`) §12.
- `var_positional` is not a slot anywhere; `positional_only` is settable (post-init setattr) on
  purpose; `var_keyword` makes the accept-list `None` (accept everything) and is never a declared
  name.
- A class with no `__init__` anywhere declares nothing (`slots()` skips `object.__init__`).
  `scan_init_body` unpacks tuple/list/starred targets; `for self.i …` and `with … as self.x` stay
  invisible ON PURPOSE (a counter or a resource is not a setting). (the I1/I3 groups in
  `tests/test_introspect.py`)
- The class-attribute scan: a public class attribute is a slot whatever its value (`None`, an
  assigned callable). Invisible: a method defined in the class body, a
  `functools.cached_property`, a `__slots__` descriptor (the body scan claims that name). A body
  slot assigned a literal carries it as `Slot.default`. (the N-tail group in
  `tests/test_introspect.py`; `tests/test_strict_attrs.py::test_strict_accepts_a_none_valued_and_an_assigned_callable_class_attr`;
  `tests/test_dumper.py::test_a_slots_class_round_trips_its_body_slot`;
  `tests/test_all_gaps.py::test_get_hierarchy_reports_a_literal_body_slot_default`)
- **Owner scope:** declared-options listings (`get_hierarchy`, `to_pydantic`, `dump()`) keep only
  body slots whose `Slot.owner` is `@configurable`; the accept-list keeps the whole MRO (the engine
  subtracts via `_get_parent_attr_blacklist`). A new body-slot reader chooses this filter
  explicitly, as an `owner` filter, never a second walk. A body slot's annotation is resolved once,
  in the enumeration. (`tests/test_pydantic_export.py::test_a_non_configurable_bases_body_slots_never_become_schema_fields`,
  `::test_slot_owner_names_the_declaring_class`, `::test_body_slot_ANNOTATIONS_reach_the_generated_model`)
- `broadcast._get_param_kinds` classifies `slots()` annotations (an annotated body slot takes a
  dict/list like a ctor param, on both paths); `_classify_annotation` peels `Annotated` first.
  (`tests/test_broadcast_robustness.py::test_param_kinds_reports_an_annotated_body_slot`,
  `tests/test_parity.py::test_an_addressed_list_at_an_annotated_body_slot_lands_on_the_LOAD_path`)
- **`introspect.init_callable(target)`** answers "whose signature governs the call" — a class's
  `__init__`, any other callable itself. Never read `target.__init__` (a function resolves to
  `object.__init__`, which accepts everything). (`tests/test_callable_target.py`)
- **An annotation is a type or `Any`, never text:** `introspect.resolve_string_annotation` is the
  one resolver for quoted annotations and PEP 563; a nested ForwardRef is evaluated
  (`_evaluate_forwardrefs`); `marked_param_names` projects from `slots()`; `resolve_ast_annotation`
  must `inspect.unwrap` before reading `__globals__`. (the string-annotation group in
  `tests/test_introspect.py`) §26.

## Parsing: two spellings, one IR (§11, §19, §27)

- The Fluid family is the only in-memory marker form; there is no dict marker. Code synthesizing
  a marker builds `Target(name)` and then `.kwargs.update(...)`.
- Tag constructors are registered once on `loader.ConfluidLoader`, never on the global
  `yaml.SafeLoader` (that would hand markers to every library's `safe_load`); `resolver.parse_value`
  stays on plain `yaml.safe_load`. (`tests/test_loader.py::test_global_safe_loader_stays_clean`)
- Both spellings MUST produce the same markers; a behaviour reachable from one spelling only is a
  bug in that spelling. The one asymmetry: `_scope_` takes a mapping, so it can carry several
  dimensions. (`tests/test_plain_format.py::test_both_spellings_agree`,
  `::test_the_two_spellings_can_be_mixed_in_one_document`)
- The reserved-key path is a parse-time conversion only: `loader._reserved_to_marker` is the one
  site; no reserved-key branches in `scopes`/`broadcast`/`engine`/`configurator`/`dumper`. It rides
  the default mapping tag and decides from the NODE's key names (`loader._node_key_names`, which
  sees through `<<:` by the merge TAG, handles `<<: [*a, *b]` and chains, and keeps
  `seen: Dict[int, node]` against self-merges); a mapping with no reserved key goes to PyYAML's
  own constructor. Never simplify `_node_key_names` back to a comprehension. (the merge-key group in
  `tests/test_plain_format.py`)
- A malformed marker raises a located `ConfigurationError`, never degrades — in both spellings
  (`_refuse_degrading_tag_shapes`, `_parse_inline_kwargs`, `_refuse_reserved_keys_in_tag_body`).
  A directory or a non-UTF-8 file at a config path is a located error too, never a raw
  `IsADirectoryError`/`UnicodeDecodeError`. (the malformed-marker group in
  `tests/test_plain_format.py`; `tests/test_loader.py::test_a_directory_is_refused_as_not_a_config_file`;
  `tests/test_cli.py::test_hydraide_prints_one_line_for_an_undecodable_file_and_for_an_engine_defect`)
- Every parse-time key-shape refusal lives in `loader._refuse_malformed_keys` (called from
  `map_constructor` and `_str_keyed_mapping`): duplicate keys (identity `(tag, value)`, merge keys
  skipped), a reserved key in any segment of a dotted key, an empty dotted segment, a `<<:` from a
  TAG-spelled anchor. A new key-shape rule goes there; a new mapping-building constructor goes
  through `_str_keyed_mapping`. Never re-convert a reserved key after expansion. A dotted write
  into a list is refused in `merger.expand_dotted_mapping`. (the P16 and duplicate-key groups in
  `tests/test_plain_format.py` / `tests/test_loader.py`;
  `tests/test_merger.py::test_a_dotted_write_into_a_list_is_refused`) §27.
- `_partial_` pairs with `_target_` only; the check sits in `_reserved_to_marker` right after the
  discriminator is chosen, before the per-discriminator branches. Never move it into a branch. It
  is parse-time (an inactive scope block still raises). (the P10 group in `tests/test_plain_format.py`)
- **A string is never parsed as a marker (user instruction 2026-08-18).** A value starting with a
  marker prefix (`resolver._STRING_MARKERS_ALL`, exact prefixes, never a bare `!`) is refused,
  naming the unquoted tag and the reserved-key form; `flow()` of a string passes it through. It
  carries no `file:line` because a scalar has none. Never re-add a string parser.
  (`tests/test_loader.py::test_a_marker_written_as_a_quoted_STRING_is_refused`,
  `::test_an_ordinary_value_starting_with_a_bang_is_untouched`,
  `tests/test_resolver.py::test_a_marker_written_as_a_string_is_refused_at_every_depth`)
- **Tags are the authoring form, reserved keys the machine form, and neither warns (user ruling
  2026-08-15).** No tag warning, no tag→plain migration tool, no spelling scan of examples in
  either direction. `!lazy:` stays an alias. (`tests/test_plain_format.py::test_neither_spelling_warns`)
- **The one codemod is `confluid.spelling.to_tags` (plain → tags).** It edits LINES (comments
  survive); what it cannot convert is reported as a `Finding`, never guessed; `convert_file`
  writes only when `hydraide.emit(before) == hydraide.emit(after)` under every declared scope
  activation. Generated documents (`dump()`, `regen_examples` output, `runs/…/final.yaml`) are
  never converted. (`tests/test_spelling.py`)

## hydraide (§19, `docs/hydraide.md`)

- `hydraide` is the one emitter of the plain form and a WRAPPER over passes 1–7:
  `dump(load(source, until="settled"))` plus named anchors and the re-emitted `import:`
  (`loader._IMPORT_ACCUMULATOR`). It adds no pass and re-derives no rule — a wrong emitted
  document is a defect in pass 7 or `dump()`, never in `hydraide.py`. It refuses exactly what
  `load()` refuses.
- The command is Click, imported by `confluid.cli` only (guarded `ImportError` naming the extra);
  the engine never imports Click. Contract: a located `ConfluidError` → one stderr line, exit 1;
  usage error → exit 2; stale `check` → exit 1 with the diff on stdout; any other exception → one
  `internal error while resolving …` line, exit 1. A relative CONFIG resolves through the search
  tiers first. `--scope` completion reads `discover_dimension_values(load(config, until="raw"))`,
  falling back to `COMP_WORDS` when Click aborts before binding CONFIG — never derive the config
  elsewhere. (`tests/test_cli.py`)
- `load(emit(x)) == load(x)` and `emit(emit(x)) == emit(x)`; output is byte-identical for both
  spellings. Identity is per marker, not per container; an anchor does NOT follow an
  include-overlay tune. (`tests/test_hydraide.py`)

## Construction and deferral

- **Exactly two construction modes.** `partial` alone decides whether a marker is built — not the
  parent, the depth or the position. `!class:Foo` == `!class:Foo()`; `!partial:`/`!lazy:`/
  `_partial_: true` is the only deferral. Never a third mode or a context-dependent build rule
  (pass 7 already reaches a built node before pass 8 builds it).
- A slot the receiving class declared deferred (`Partial[T]`, or a body slot holding
  `PartialClass(...)`) stays unbuilt whatever the value says — read via `partial_param_names` in
  `_flow_target` and on the post-init path. The one promotion site is `_apply_post_init_attrs`;
  the promoted marker keeps `_yaml_loc` and merge bookkeeping; it logs at DEBUG (user ruling
  2026-08-22). (`tests/test_partial.py::test_class_into_lazy_default_slot_is_deferred_SILENTLY`)
- **A `PartialClass` is never auto-flowed** (not by `load()`, not by external walkers), but it IS
  broadcast into like a `Target`; never re-add an `isinstance(v, PartialClass)` early return in
  `_resolve_kwarg_value`. An explicit `flow(node)` builds it; a mapping at its slot tunes it; a
  kwarg set in code is a default. (`tests/test_partial.py`, `tests/test_deferred_broadcasting.py`) §5.
- Both code spellings of a marker slot (body slot; ctor default via `engine._ctor_default_markers`)
  tune through `engine._tune_slot_marker`. A runtime kwarg is a call argument, never a tune; a full
  marker at the slot replaces the default. Never a third copy. (the ENG-1 group in
  `tests/test_dict_at_slot.py`) §15.
- A ctor-DEFAULT marker builds one child per host (`_broadcast_onto_instance` copies it); sharing is
  `!ref:`.
- **Repeat flows of a deferred marker are cached (user ruling 2026-08-24):** one recipe + one
  argument set = one object, one entry per marker in `engine._PARTIAL_BUILD_CACHE`. `random=True`
  re-executes; a marker copy never shares a build (no copy-site exclusion code); an eager `Target`
  keeps per-pass memoization; the store is NOT a `register_pass_cache` member.
  (`tests/test_partial_cache.py`; `docs/targets.md`) §24.
- **Dict-at-slot has ONE dispatch, `broadcast.dict_at_slot_kind`, on every path:** marker → tune;
  live `@configurable` → walk into it; plain data or nothing → assign; any other live object → a
  located refusal (user ruling 2026-08-13: never silently replace an object with a dict). The
  classifier reads `__dict__` values and the class mark only. Never a per-path arm.
  (`tests/test_dict_at_slot.py`) §15.
- **Composition tunes a marker:** in `merger.deep_merge` an overlay mapping over a `Target` tunes
  it (a `Reference` base is replaced); a marker over a marker of the same target and kind merges
  kwargs (`merger._same_marker_target`), a different target or kind replaces, a selector never
  tunes. It recurses itself — never `tune_marker` (single-level, and `broadcast` imports
  `merger`) — and COPIES, never mutates (base markers are shared). (the marker-merge group in
  `tests/test_merger.py`, incl. `::test_the_merge_does_not_mutate_the_BASE_marker`)
- An instance-name block matches the ctor-default `name` on load (`broadcast._receiver_for_target`);
  an explicit `name:` or a non-str default opts out.
  (`tests/test_broadcast_scoping.py::test_an_instance_name_block_matches_the_ctor_default_name_on_load`)
- A typo'd ADDRESSED key on an unregistered target warns (located) and records
  `unknown-attribute`; a BARE key reaching one is skipped at TRACE. `register()` the class to
  accept extra keys. (`tests/test_instantiate.py::test_a_typo_on_an_unregistered_target_is_dropped_AUDIBLY`,
  `::test_a_BARE_key_reaching_an_unregistered_kwargs_target_is_dropped_SILENTLY`)
- The reference-kwarg deferral catch is `ReferenceResolutionError`, never `ValueError`.
- **Naming:** `PartialClass` (the marker class), `Partial[T]` (the annotation), `partial_param_names`,
  `_partial_` — never give the class and the alias one name; discriminate by `marker.partial`.
  Subscript the alias with the interface (`Partial[Optimizer]`). `introspect._PARTIAL_CALL_NAMES`
  matches the call name in SOURCE — add a new spelling there.
  (`tests/test_partial.py::test_one_marker_type_two_modes`,
  `::test_the_body_slot_scan_matches_the_canonical_call_names_only`)
- **A target may be ANY callable** — class or builder function — for markers, `flow()`,
  `@configurable` and `register`. `register()` stays validation-free but carries `broadcast=False`,
  `broadcast_attrs` and `strict_attrs`. Marker-target normalization goes through
  `broadcast._settability_target`, never inlined. (`tests/test_callable_target.py`, the D6 pair in
  `tests/test_cross_path_pins.py`)
- **Solidify:** `flow()` calls `solidify()` on what it returns, the idempotency return included;
  `solidify()` is idempotent and takes no arguments; `solidify=False` suppresses it for the subtree.
  An expensive dataset-walking construction goes in an explicit `initialize()` called from `run()`,
  not in `solidify()`. (`tests/test_fluid.py::test_flow_solidifies_a_live_object`,
  `::test_flow_idempotent_returns_the_same_object_and_refires_the_hook`) §2.
- **Positional channel:** `flow(node, *args, **kwargs)` → `target(*args, **merged)`. Positional
  args are runtime-only — never stored, never dumped; give the target a keyword instead. An
  ADDRESSED key naming a `*args` parameter is refused via `broadcast.refuse_if_variadic_name`
  (called from `engine._apply_post_init_attrs` and `_MergeSink.unknown`); BARE keys stay exempt;
  a `**kwargs` name is never refused. (the positional group in `tests/test_fluid.py`, incl.
  `::test_a_BARE_key_colliding_with_a_variadic_name_still_loads`) §2.
- `load(x, until="settled")` constructs nothing, and no `Reference` can make it.
  (`tests/test_resolve.py`)

## Precedence & broadcasting (§3, §6, §8)

- **Position is the only precedence.** Every source of a value competes on position; `_yaml_loc`
  is a diagnostic and is never read for precedence.
- **Producers produce the author's reading order:** `loader._splice_includes` pastes an include at
  the directive's line; `deep_merge` re-anchors a restated key at the overlay's position;
  `merger.expand_dotted_mapping` anchors a fresh head where it was written and applies every key
  in ONE pass in document order, both branches symmetric. A later marker REPLACES an earlier dotted
  head (PA24, user ruling 2026-08-20). (the position group in `tests/test_merger.py`, incl.
  `::test_the_two_orderings_DISAGREE`; `tests/test_document_order.py::test_a_later_marker_replaces_an_earlier_dotted_head`)
- **A dotted line on a marker's kwarg keeps the LINE's position, per key (BC4, user ruling
  2026-08-19, option B — never move the whole marker).** `expand_dotted_mapping(stamp_positions=True)`
  (top level only) stamps it, `fluid.dotted_positions_of` reads it, `_dotted_protected` gates by the
  delivering top-level key, and `dump()` re-emits it as a dotted line (`_reemit_dotted_positions`).
  (the BC4 group in `tests/test_document_order.py`;
  `tests/test_hydraide.py::test_a_dotted_kwarg_position_survives_the_emit_round_trip`)
- **A block-delivered mapping is arbitrated at the BLOCK's position.** At a slot holding a marker,
  `_MergeSink.dict_at_slot` stamps every kwarg the block writes, at every depth
  (`_stamp_block_positions`, the BC4 stamp). At a slot holding no marker, the sink records
  `beaten_per_slot` and pass 7's `_view_for` narrows that slot only (children see the un-narrowed
  view). `_late_bare_keys_per_slot` still serves a marker's own dict kwargs and the post-init tune;
  deleting either half is wrong. (the C2 and BC1 groups in `tests/test_cross_path_pins.py`)
- The candidate set is the one `broadcast._cascade_scalar_positions`; the directional reads stay
  per caller. A verdict is call-scoped, never marker state.
  (`tests/test_document_order.py::test_a_second_configure_is_not_bound_by_the_first_ones_verdict`)
- The engine fields on `Fluid` are read through `fluid.addressed_keys_of` / `is_order_resolved` /
  `late_bare_keys_of`, never a bare `getattr`.
- **The walk is `broadcast._scan_view`.** A sink never branches on keys or scopes; a new rule is a
  `_Receiver` predicate in `_receiver_for_target`, with a pin. `_View` carries per-key `_KeyScope`
  tags — `copy()`/`update()` keep them, `dict(view)`/`{**view}` flatten them (root document only).
  (`tests/test_scanner.py`)
- Glob keys (`*`, `**`) are routing metadata — they never reach ctor kwargs, setattrs,
  `__confluid_kwargs__` or settled marker kwargs. (Scoping grammar: `docs/broadcasting.md`.)
- **A `**kwargs` constructor** accepts everything; what its CONSTRUCTOR receives follows the
  addressing: an addressed key (a marker value included) or a runtime kwarg is an argument, a bare
  cascading key is a post-init attribute — and a bare sweep restating an addressed key takes it to
  the attribute channel (BC14, user ruling 2026-08-20). Never feed bare keys to the constructor.
  (`tests/test_broadcast_scoping.py::test_var_keyword_class_receives_every_bare_key`, the BC14/BC15
  groups) `broadcast="declared"` closes the list to the declared/scanned slots without changing
  constructor routing. (`tests/test_broadcast_declared.py`) §22.
- An addressed LIST at a declared key is a value whatever the annotation; cascade lists stay
  filtered; the same-target guard's drop logs TRACE. (the BC16/BC17 groups in
  `tests/test_broadcast_scoping.py`)
- **Pass 7 settles, pass 8 builds.** `instantiate` builds every `Target` from its settled kwargs
  and never re-runs the cascade into a marker's own kwargs. The context's bare keys are read at
  construction only for markers born inside a constructor (`_broadcast_onto_instance`, the
  post-init tune). Never add a third reader of the context at construction.
  (`tests/test_instantiate.py`) §19.
- **A reference is settled once, in pass 7** (`engine._settle_reference`): nearest enclosing scope,
  then the root; never itself; a marker is shared, a plain value inlined; a miss is a located
  `ReferenceResolutionError` at `"settled"` and `"objects"` alike. CLI overrides go
  `load(until="document")` → merge → `load(document)`.
  (`tests/test_list_index_refs.py::test_e2e_drone_labels_index_pattern`)
- **Kwargs on a reference tune the shared referent (user ruling 2026-08-19, option A):**
  `resolver.fold_reference_kwargs` runs once in `loader._load`, after expansion, before
  interpolation; the kwargs compete at the REFERENT's position. Never tune at pass 7. (the
  reference-kwargs group in `tests/test_ref_identity.py`) §18.
- Pins for the whole section: `tests/test_document_order.py`, `tests/test_broadcast_scoping.py`,
  `tests/test_broadcast_wrapper_override.py`, `tests/test_cross_path_pins.py`, the four parity
  files (`test_basic_parity` / `test_names_parity` / `test_parity` / `test_transforms_parity`).

## `configure()` (§19, `docs/configure.md`)

- `configure()` = `dumper.to_markers(objects)` → merge the config → `load(until="settled")` →
  `_apply`. There is NO second walker; do not add one. A class attribute is not walked into, a
  marker attribute is tuned in place, the ctor-kwargs capture is only a dump fallback.
  (`tests/test_configure_via_document.py`, the C3/C4 groups in `tests/test_configurator.py`)
- It finalizes AFTER applying, post-order. (`tests/test_configurator.py::test_configure_applies_values_before_solidify_fires`,
  `::test_configure_does_not_rebuild_an_already_solidified_object`)
- `dumpable_kwargs` and `_apply` read a slot the same way: a property-shadowed slot never runs its
  getter and the captured kwarg is its value; every other slot is read with `getattr` (so an
  `nn.Module` submodule or a `__slots__` slot is found and configured in place).
- A key that NAMES an object is written as dotted lines at its own line
  (`configurator._overlay_as_dotted_lines`; a shape the dotted grammar cannot carry keeps the
  in-place fold) and recorded by `_record_named_overlay` — one applied record per accepted key.
  Never record in `_set`. `_apply` runs `_warn_undeclared` before every set. `scopes=` threads
  into the inner load.
- **A marker at a slot holding a live child of the SAME class tunes the child (user ruling
  2026-08-18)**; a different class is built; do not generalize to subclasses or containers
  without a measured case. (`tests/test_configure_live_child.py`)
- `configure()` skips a settled kwarg equal to the capture when the attribute does not exist (the
  capture is the dump fallback, not a config change).

## Paths, search, includes, imports

- **One path grammar, one policy:** `resolver._parse_path_segments` + `_walk_path_segments`. Never
  a fourth grammar or a second policy. A miss is `PATH_MISS`, never `None` (a found `null` is a
  hit); `_lookup_path` keeps the `None` contract for pass-6 aliasing and `$key`. An int segment
  steps into a dict (int key first, then the digit string).
- **Attribute references are removed (user ruling 1b, 2026-08-17).** The FIRST segment decides: a
  document key walks structure only; anything else is an import path (`!ref:posixpath.join`). A
  walk that leaves structure is refused by `resolver.refuse_attribute_reference` (in
  `_flow_recursive` and `_flow_reference`) with the location and the rewrite; the `${a.b}`
  placeholder refuses entering a marker the same way (`resolver._path_enters_marker`). Nothing is
  constructed for a `Reference`. Do not extend the walker into a marker's kwargs. Pinned con
  cases: a purely structural resolution is inlined by pass 7, and a document key literally named
  `a.b` wins over the walk. (`tests/test_attribute_refs_removed.py`, `tests/test_list_index_refs.py`)
- **One search resolver:** every relative config path (entry and each `include:`) goes through
  `loader.resolve_config_path`; existence is `loader._exists` (it sees held texts) — never a bare
  `.exists()`. Tests sandbox `XDG_CONFIG_HOME`/`XDG_CONFIG_DIRS`/`HOME` and chdir into `tmp_path`.
  (`tests/test_search_paths.py`; tiers in `docs/search-paths.md`)
- **`include:` works in every position, and scopes and includes settle by alternating**
  (`loader._settle_scopes_and_includes`, capped by `_MAX_SETTLE_PASSES`): a marker's include splices
  in the normal pass; a scope block's include is deferred until the block activates and is never
  opened otherwise; it is anchored to its block's file (`loader._anchor_deferred_include` — probes,
  never opens). (the P15 and PA1 groups in `tests/test_includes.py`) §17.
- `import:` is honoured in every mapping position; a failed import warns for any `Exception`
  (`KeyboardInterrupt`/`SystemExit` propagate). (the PA26 pair in `tests/test_loader.py`)

## Scopes (§1, §17, `docs/scopes.md`)

- A scope block is a tag (`!scope:` / `!notscope:`) or a reserved key (`_scope_` / `_notscope_`
  with a mapping), activated by `scopes=`. Plain dict keys are never scopes.
- `ScopeBlock.dims` is `Dict[str, Optional[str]]`, all of which must match; never back to one
  key/value pair. The wrapper key is inert and must stay so.
- **The splice is the include paste:** `scopes._splice_key` goes through `merger.deep_merge` per key
  (a mapping over a marker tunes it, a nested block merges, a restated key re-anchors). Never
  `out[k] = v`. Two active `include:` values combine into a list. (the splice group in
  `tests/test_scopes.py`)
- A list whose FIRST item is a `_scope_` mapping is a scope block (the only way to write a
  conditional list item); the first item is the condition only; `seq_constructor` builds a NEW
  `ScopeBlock` per sequence. Never add a body key. (`tests/test_plain_format.py::test_a_key_beside_the_scope_marker_in_a_list_block_is_refused`,
  `::test_an_anchored_scope_marker_reused_in_two_lists_keeps_each_body`)
- A dimension value YAML reads as a boolean is refused with a quote-it message.
- The three walkers — `scopes._walk_dimensions`, `scopes._resolve_value`,
  `loader._process_includes_recursive` — must agree on node kinds (dict, list,
  `ScopeBlock.contents`, `Fluid.kwargs`). Add a kind to all three.
- An active keyed scope must name a declared value; a bare activation of a keyed-only dimension is
  refused; a dimension that also has a boolean block accepts the bare name. The three exemptions
  of §1 stay. `discover_dimension_values` reports positive values only (a negation-only dimension →
  empty set — do not "fix" it) and takes the RAW document. (`tests/test_scopes.py`)
- **`default_scopes:` is static and keyed-only.** `scopes.default_scopes(raw)` is the one reader;
  `normalize_active` fills it after the caller's list; it passes the declared-value check (no
  bypass); a bare name is refused; it is never interpolated. `scopes.METADATA_KEYS` is the one strip
  list. (the `default_scopes` group in `tests/test_scopes.py`)

## Interpolation (§7, §21, `docs/interpolation.md`)

- `${...}` dispatches on name shape: an identifier is an environment variable, a dotted/bracketed
  name a config path. Bare `$IDENT` is env-only, read after the `${...}` pass, on author text only
  (substituted text is never re-scanned). Bare `{key.path}` is deliberately not supported.
- **`$$` is a literal `$` in the final objects:** pass 6 never expands or rewrites it; the collapse
  happens once, at the document→objects boundary (`engine.collapse_escapes` in `instantiate`, and
  in `configure()` after the settle); `dump()` escapes `$` → `$$`. Never collapse in the resolver;
  never add a dump parameter.
- **Interpolation is pass 6, after expansion, single-pass, and burns in.** Do not reorder the
  passes and do not add a second interpolation pass. A late-bound slot uses `!ref:`.
  (`tests/test_resolver.py`, `tests/test_load_stages.py`,
  `tests/test_loader.py::test_config_key_interpolation_end_to_end`)

## I/O contract and annotation markers (`docs/io-contract.md`)

- `@output` goes UNDER `@property` (it stamps `fget`); `output_specs` walks the MRO; an output is
  never a `to_pydantic` field. `Mandatory[T]` marks a slot required even when defaulted.
- `Partial[T]` and `Mandatory[T]` are `Annotated[Union[T, Fluid], marker]`; `NoBroadcast[T]` has no
  `Fluid` arm. Detection is the recursive `introspect.annotation_has_marker` (`List[Partial[T]]`
  marks the element); all three names come from `marked_param_names`; `to_pydantic` strips all
  three.
- `NoBroadcast` is enforced at the cascade gates (`broadcast._broadcast_blocked_keys`), never by
  removing the param from `_get_acceptable_keys`. A `'**'` mapping is gated by the slot param's
  shield, a `'**'` scalar by the target param's. (`tests/test_io_contract.py`,
  `tests/test_no_broadcast.py`)

## Schema export (`docs/schema-export.md`)

- `to_pydantic` keeps `Field` constraints; an un-JSON-schemable leaf (`torch.Tensor`, numpy) or a
  generic whose origin is one becomes `Any`.
- One model per class across threads: lock-free `_MODEL_CACHE.get()`, build under an `RLock`
  (reentrant on purpose). Never back to a bare `lru_cache`; `to_pydantic.cache_clear` stays.
  (`tests/test_concurrency.py::test_concurrent_first_calls_to_to_pydantic_hand_out_ONE_model_class`)
- The mirror builds for every legal signature: abc `Callable` and a non-runtime-checkable
  `Protocol` → `Any`; any other unschemable leaf → `Annotated[T, WithJsonSchema({})]` (decided by
  `_json_schemable`); a leading-underscore or `BaseModel`-attribute name gets a mangled field name
  with the real name as alias, and `field_name_for` is the one reverse map. Never widen
  `_OPAQUE_TOP_MODULES` to fix a new type. A marker default publishes its plain form; one that is
  not JSON-clean is left out silently. (the N-numbered groups in `tests/test_pydantic_export.py`)
- Range marks on a container's outer annotation are relocated to its numeric elements
  (`_spread_range_marks_into_container`, through `Optional`/`Union`).
- `_ITER_TYPES_AS_ANY` holds lazily-validated kinds only (`Iterable`, `Iterator`, `Generator`, async
  twins); never add `Sequence`/`Mapping`/`Collection`/`Container`. Test: does its contract permit
  a generator? (`::test_a_validated_sequence_is_re_iterable_while_an_iterable_is_not`)
- `parse_param_docs` reads Google and NumPy docstrings, MRO-wide.
  (`tests/test_parse_param_docs.py`)
- `sanitize_schema` (`llm_schema.py`) is pure, stdlib-only, recurses subschema positions only, and
  rewrites the advertised schema, never server-side validation. (`tests/test_llm_schema.py`)
- Warn-mode validation names the marker's location (`validation._construction_where`).
  (`tests/test_validation.py::test_warn_mode_names_the_yaml_location`)

## Serialization (`docs/serialization.md`)

- `dump()` → `load()` MUST rebuild an identical graph; every new feature gets a test that dumps and
  reloads.
- A target's name comes from the registry (`dumper._target_name`, both branches, `registry.key_for`
  first, dotted path fallback, a string verbatim). `dump(qualified=True)` uses the importable path
  except for `<locals>`/`__main__` classes; `hydraide` never passes it. (the F3 and qualified groups
  in `tests/test_dumper.py`)
- Opaque values dump by value where faithful (`_represent_opaque`: PathLike, Enum, numpy scalar);
  anything else is a placeholder plus one warning per type. `register_dump_spelling` is the
  type's document spelling: built-ins win over it, it wins over the generic reconstruction; it is
  dump-side only — no load-side hook, and `to_markers()`/`configure()` keep live values.
  (`tests/test_dump_spellings.py`) §23.
- Body slots are dumped (owner-filtered, `dumper._dump_slots`), and every value is dumped even when
  it equals its default — never optimize to "only what differs". Accepted cost (user triage
  2026-09-26): a non-`@configurable` base's attribute stays settable, but only a value the document
  set during `load()` reaches the dump (via `__confluid_extra__`). A body slot rebinding the object's
  own method is not dumped. A `**kwargs` class's captured extras are emitted. A `None` is omitted
  only when the default is also `None`. (the body-slot and N1 groups in `tests/test_dumper.py`)

## Registry, discovery, marks (§4, §10, `docs/discovery.md`)

- Only `@configurable` and `register`-ed callables join the graph; never traverse unregistered
  library internals.
- A name maps to a list of entries; the five reverse indices store entry KEYS; the public key is
  derived on read. `list_classes()` always returns a valid `get_class` argument. Lookup: 0 → `None`,
  1 → the class, N → `AmbiguousClassError` (a SIBLING of `UnknownClassError`). `resolve_class` is
  non-raising by default; `strict=True` only where a class is about to be built or settled — the
  construction funnels `_resolve_target_callable` / `_flow_generic_fluid` and pass 7's
  unresolvable-target refusal in `_flow_recursive`. (`tests/test_duplicate_names.py`)
- `register_class` names a class by its OWN `__dict__` mark (and `get_class(cls)` reads the same);
  `key_for(cls)` is computed live. Registration refuses what it cannot name or bind
  (`ConfigurableDefinitionError`); `@staticmethod` goes above `@configurable`. Enumeration iterates
  `list(...)` snapshots — no lock. `load_configurables` collects per-entry errors, is never called
  at import, and catches `Exception`, never `BaseException`. (`tests/test_registry.py`,
  `tests/test_load_configurables.py`)
- The `@axis=value` selector is parsed by `registry.parse_target_spec` over the five
  `SELECTOR_AXES`; an unknown axis raises. `$key` resolves at flow time. Do NOT build scope-aware
  resolution.
- **Taxonomy:** prefer `task=`/`role=`; `register_class` derives `category = f"{task}_{role}"`
  (an explicit `category=` wins). `framework=` is a third axis, not folded into `category`. `role`
  and `framework` are stated, never inferred from the class type. `group=` is presentation-only.
  An untagged class is absent from an index, so a dropped or renamed tag silently empties a picker.
  (`tests/test_task_role.py`, `tests/test_group.py`; the category vocabulary is in
  `docs/extending-discovery.md`)
- **Marks:** `registry.register_class` is the one stamping authority (`@configurable` delegates);
  `confluid.marks(target)` is the one public read surface — consumer code never reads the
  `__confluid_*__` dunders. `random` and `constant` are mutually exclusive; `broadcast` is the
  closed `Union[bool, Literal["declared"]]`, converted only in `decorators._broadcast_flags`.
  (`tests/test_task_role.py::test_marks_is_the_one_public_read_surface_for_the_stamps`)
- **Packaged mode — the bake is KEPT (user ruling 2026-08-09, do not re-flag as dead surface).**
  Body slots = scan ∪ `broadcast_attrs` ∪ baked (`confluid-bake`); wire `confluid-bake --check`
  into the first frozen consumer's CI. The cannot-scan warning fires once per class per process
  (a dataclass `__init__` logs at DEBUG). (`tests/test_bake.py`, `tests/test_broadcast_attrs.py`)
- Zero-user surface is kept until 1.0 (§10): an audit that finds a census member unused stops
  there.

## Reports (`docs/report.md`)

- `confluid/report.py` is a dependency leaf (stdlib + loggair); only `ConfigurationReport` and
  `collect_report` are top-level; every instrumentation site is `if report is not None`-guarded.
- An undeclared ADDRESSED key warns and records `unknown-attribute` on both paths; the own-kwarg
  form still applies it. Exempt: a `**kwargs` target, a bare key (`unused`, never `failed`), a
  declared body slot. `strict_attrs=True` is the only way to refuse instead
  (`broadcast.refuse_if_undeclared`, called from `engine._warn_undeclared` and
  `_MergeSink.unknown`; binds `configure()`; `register()` carries it). (the undeclared-key group in
  `tests/test_report.py`; `tests/test_strict_attrs.py`)
- `materialize()`/`active_context()` carry an ambient report into their fresh state; a nested
  `configure()` adopts it; "failed" is configure-path only.
- The contest ledger: a `Candidate` holds a bounded string, never the value; the scanner appends
  raw tuples and `record_applied` renders them only when there is more than one;
  `_MergeSink.apply` records before its own-kwarg early return. (the `explain` group in
  `tests/test_report.py`)

## Public surface

- `confluid/__init__.py` exports the consumer-facing API only; ask which consumer reads a name
  before adding it. Front-ends call `accepts_key` / `accepts_broadcast` / `accepts_any_key` /
  `declares_key` instead of reading marks or accept-lists; a new gate goes beside them.
  (`tests/test_no_broadcast.py::test_accepts_any_key_matches_what_the_constructor_actually_receives`)
  Public by design despite zero users: the exception hierarchy, `configure`/`configure_from_file`,
  the `ValidationPolicy` knobs, `format_yaml_loc`, `InputSpec`/`OutputSpec` (§10).

## Typed exceptions

- Every error comes from `confluid/exceptions.py` (root `ConfluidError`), and each class
  dual-inherits the builtin it replaces (a class outside the hierarchy silently breaks
  `except ValueError:` callers). New config-content errors subclass
  `ConfigurationError` and are exported. Builtin survivors: the PEP-562 `AttributeError`, the
  optional-dependency `ImportError`s, and `flow()`'s re-raise of the constructor's own class.
- **Every error about a document names its `file:line:col` (user instruction 2026-08-11).** A
  helper that raises about a node receives the NODE (or its location), never the string pulled off
  it — e.g. `engine._resolve_target_callable(node)`, so `UnknownClassError: Cannot resolve class: …`
  names the YAML line. (`tests/test_exceptions.py`, incl.
  `::test_unknown_class_names_the_yaml_file_and_line`; `docs/errors.md`)

## Dependencies, logging, tests

- Runtime dependencies: `pyyaml`, `loggair`, `typing-extensions` only. Pydantic, python-dotenv and
  Click are the `pydantic`/`env`/`cli` extras, imported lazily with an `ImportError` naming the
  extra. A new hard dependency needs a reason that holds for an engine-only consumer.
- Log through `from loggair import get_logger`; stdlib `logging` is prohibited. Messages are
  f-strings (`%`-style args are silently dropped). `caplog` cannot see loggair: patch the module
  `logger` with a `SimpleNamespace` collector and assert on its list.
- Broadcast diagnostics log at TRACE (`LOGGAIR_CONSOLE_LEVEL=TRACE`). A per-key log site is gated
  (`if _trace_on:` in `broadcast`, `trace_enabled(logger)` elsewhere) — the f-string is otherwise
  built for every key. A swapped-in test logger is never gated. (the log-gate group in
  `tests/test_broadcast_scoping.py`)
- An autouse fixture snapshots and restores the registry (and the dump-spelling store) around
  every test. Every test file passes alone and under pytest-randomly; never use ordering tricks
  (`test_zz_*`).

## Documentation and release

- The README is a landing page; each topic is a `docs/<topic>.md` with a runnable companion in
  `examples/` (standalone, zero-arg, exit 0, run by CI); a new topic adds doc + example + README
  row in one change. `docs/architecture.md` has no example twin. README links are absolute GitHub
  blob URLs; links between `docs/*.md` stay relative. (`tests/test_docs_links.py`)
- A directory example (`examples/<name>/`: `run.py`, a `README.md` that IS its doc page, the YAML,
  a one-line `__init__.py`) is for scenarios needing a file tree; one that must not run in CI has
  no `run.py`.
- An example that demonstrates a rule asserts it.
- Guides and examples show TAG input and, where the machine form matters, PLAIN output labelled as
  output (user ruling 2026-08-15).
- Release: bump `version` in `pyproject.toml`, push tag `v<version>`; `release.yml` builds,
  verifies (twine strict, a clean-venv wheel smoke test from a neutral cwd) and publishes by
  Trusted Publishing. Order: loggair → confluid → liquifai.
