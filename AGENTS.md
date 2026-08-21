# Confluid Mandates

## How to read this file

Every mandate is three parts, and each answers a different question:

- **Rule** — what you MUST or MUST NOT do. Imperative, and the only part that binds you.
- **Pins** — the tests that fail if you break it. Run them; don't re-derive the behaviour.
- **Why** — one line, plus a pointer. The full reasoning lives in `docs/architecture.md`
  (numbered decision records) and the usage in `docs/*.md`. **Do not restate rationale here** —
  this file was 108 KB of interleaved rule/history/measurement before 2026-08-11, which is how a
  rulebook stops being read.

`docs/lifecycle.md` is the map: the nine passes a document goes through, in order. Read it first
if you are new to this codebase — most rules below are about one pass.

---

## Current state

**Feature-complete.** Confluid is the hierarchical configuration + dependency-injection engine:
plain-YAML reserved-key markers (`_target_` / `_partial_` / `_ref_` / `_scope_` /
`_notscope_`) loaded through the ONE door `load(x, until=…)` (`"raw"` / `"document"` /
`"settled"` / `"objects"`) and built node-wise by `flow()`, scoped broadcasting, post-construction `configure()`,
recursive DI, and the introspection surface (`to_pydantic` / `parse_param_docs` /
`sanitize_schema`) that every AI- and GUI-facing consumer reads for tool schemas and form specs.

The TAG spelling (`!class:` / `!partial:` (alias `!lazy:`) / `!ref:` / `!scope:`) is the
PREFERRED AUTHORING form; the reserved-key spelling is the MACHINE form that the `hydraide`
preprocessor EMITS (`confluid.hydraide.emit(cfg, scopes=[...])`; the `hydraide` COMMAND is `confluid/cli.py`, Click, the `confluid[cli]` extra — user instruction 2026-08-17). Both are first-class input,
neither warns (user ruling 2026-08-15, architecture record 19). Attribute references are gone, the
runtime consumes the settled document, and `configure()` runs through it (record 19); there is no
copy marker (record 18).

**Published on PyPI — v0.1.0 and v0.2.0. v0.3.0 is prepared and DELIBERATELY HELD** (user
instruction 2026-08-04): do not tag it until confluid's functionality is verified complete against
every downstream consumer. The hold is a decision, not an oversight — ask before tagging. Keep
adding to the `[0.3.0]` CHANGELOG section rather than minting a second unreleased minor; two
versions that exist only in the working tree cannot be told apart by any consumer. Update this
line in the same change as a version bump.

Consequence: confluid is a standalone product. Feature/fix PRs go on `Gearlux/confluid`
(`main` ← `dev/main`), never bundled into a workspace PR, and its README/`docs/` stay
consumer-agnostic.

**Performance baseline:** `examples/performance.py` + `docs/performance.md` — a print-only
engine-timing run over a ~2,500-marker tree (`CONFLUID_BENCH_PROFILE=1` adds a cProfile pass).

---

## Class design & construction

### Post-Construction Paradigm

**Rule.** Configuration MUST be applicable to already-instantiated objects. Never require
constructor-time configuration injection. (This covers *configuration*, not *type validation* —
see Schema Enforcement.)

### Partial initialization & zero-arg construction (the class-design convention)

**Rule.** A `@configurable` class in this workspace should be cheap to build and configured
afterwards. Rules 1, 3 and 4 are REQUIREMENTS; rule 2 is a RECOMMENDATION.

1. **The constructor does no functional work** — it only stores values. No I/O, network, file
   reads, dataset/model materialization, or heavy compute. (Requirement.)
2. **Zero-argument construction is RECOMMENDED, not required** (user ruling 2026-08-11 — do not
   re-litigate). The preferred shape is `Cls()` succeeding, every parameter defaulted, with a value
   genuinely required to *function* still defaulted and validated LAZILY in the property/method
   that needs it. **A class that keeps a genuinely required constructor parameter is NOT a
   violation and MUST NOT be reported, counted or tracked as one** — the engine has always
   supported it. Suggest the lazy shape for a NEW class or a refactor; never file an existing
   required-arg constructor as a defect.
3. **Derived state is a read-only `@property`**, recomputed from current inputs — never a stored
   attribute that can go stale. It MAY memoize into a private `_backing` field when recomputation
   is prohibitively expensive AND the inputs are stable by first use. (Requirement.)
4. **A configurable slot may be a constructor param OR an `__init__`-body attribute** — both are
   introspected. A body slot needing runtime injection or pre-flow kwarg mutation MUST hold a
   `PartialClass(...)` (a plain `Target` post-init attr is built); give it a `Partial[T]`
   annotation; build a fresh `PartialClass(...)` per instance. (Requirement.)

**Why.** The preferred shape is what makes "never require constructor-time injection" structural,
and keeps `configure()` reconfiguration, cheap introspection (`load(until="settled")` / `solidify=False`) and
partial-arg GUI building possible. Rule 3 keeps derived state out of the config surface: confluid
skips a setterless `property` in both accept-lists. Rule 4's body slots are surfaced by
`to_pydantic` via the `introspect.slots` enumeration, so a GUI still enumerates them.

**Pins.** `tests/test_lazy_convention.py`,
`tests/test_pydantic_export.py::test_to_pydantic_surfaces_post_init_body_slots`,
`tests/test_configurator.py::test_configure_never_executes_property_getters`.
**Docs.** `docs/class-design.md`.

### Eager classes are first-class

**Rule.** A plain Python class — required params, real work in `__init__`, params not stored
verbatim — is fully supported for `load()`/`flow()`/`dump()`. The load path constructs eagerly, a
missing required param raises a located `ConstructionError`, and `dump()` round-trips via CAPTURED
ctor kwargs. Such a class SHOULD declare `@configurable(eager=True)`, which makes `configure()`
warn when a post-construction setattr of a ctor param cannot re-run the `__init__` work (value
still applied).

**Detail.** `__confluid_kwargs__` is stamped by the engine at flow AND by the `@configurable`
validation wrap at direct construction, independent of validation mode; the dumper prefers the live
same-named attribute and falls back to the capture. `validate=False` + direct construction degrades
to the attribute heuristic; `__slots__` skips the stamp gracefully.

**Pins.** `tests/test_eager.py`. **Docs.** `docs/eager-classes.md`.

### Schema enforcement is on by default

**Rule.** Every `@configurable` class has its `__init__` wrapped to validate kwargs against
`to_pydantic(cls)`. The three validation points (constructor / YAML materialization / MCP tool
entry) are `ValidationPolicy` knobs — strict / warn / off — set via `confluid.set_policy(...)` or
`CONFLUID_VALIDATE_INIT` / `_YAML` / `_TOOL`. Opt out per class with `@configurable(validate=False)`
only when the constructor is intentionally untyped. Express a constraint an
`Annotated[T, Field(...)]` can carry ON THE ANNOTATION, not as a hand-rolled `if x < 1: raise`.

**Rule.** Pydantic is the OPTIONAL `confluid[pydantic]` extra. Without it all three points degrade
to `"off"` with one log line, and `to_pydantic` / `confluid_class_of` raise `ImportError` naming
the extra. A package relying on schema enforcement or export MUST depend on `confluid[pydantic]`.

**Rule — the skip is LOUD, the knob is CLOSED (2026-08-19, BUGS-2026-08-19 N4/N13).** A class whose
mirror cannot be built (`to_pydantic` raises — an `IntrospectionError` by contract, or pydantic's
own error) is never blocked from constructing, but `validation._model_or_none` — the ONE place the
validation hooks call `to_pydantic` — logs ONCE per class at WARNING that validation is OFF for it
and why. It catches `Exception`, not `TypeError`: the narrow clause let pydantic's `SchemaError`
escape a constructor. `set_policy()` normalizes its modes through `_normalize_mode` exactly like
the env-var reader — `set_policy(init="stict")` raises `ValidationModeError` instead of being
stored and read as warn-mode.
**Pins.** `tests/test_optional_pydantic.py`,
`tests/test_validation.py::test_a_class_whose_mirror_cannot_be_built_still_constructs_and_says_so_once` /
`::test_set_policy_refuses_a_typo_like_the_env_var_does`. **Docs.** `docs/validation.md`.

---

## The engine

### Module map — one-directional layering

**Rule.** The layering is `fluid → state → broadcast → engine → loader`. Do not add cross-layer
imports; extend the right module instead.

| Module | Owns |
|---|---|
| `fluid` | the marker DATA classes (`Fluid`/`Target`/`PartialClass`/`Reference`/`ScopeBlock`) + `format_yaml_loc`. A dependency LEAF — imports no other confluid module. |
| `state` | `_EngineState` / `_ENGINE_STATE` (one `ContextVar`) + the public `active_context` / `collect_report`. Exists so `broadcast` can read the ambient report without importing the engine. |
| `broadcast` | the ONE precedence rule and all its machinery — see "Precedence & broadcasting". Materializes NOTHING. |
| `engine` | `flow`/`cast`, `materialize` (passes 7–9) / `settle` (pass 7) — the PREPARED-data entries `load()` calls, not public names — `_flow_recursive` (pass 7 — settle) / `instantiate` (pass 8 — build), and the two post-construction deliveries pass 7 cannot see (body-slot / ctor-default markers). |
| `loader` | YAML parsing and composition ONLY (`ConfluidLoader`, includes/imports/scopes glue) — and `load`, the ONE public door, which stages every input through passes 1–6 and hands the engine PREPARED data. |
| `introspect` | stdlib-only AST/signature scanning — the ONE `scan_init_body`, `init_callable`, `marked_param_names`. |
| `configurator` | `configure` / `configure_from_file` — the objects → `dumper.to_markers` → merge → `load(until="settled")` → `_apply` back onto the objects; no walker of its own. |
| `dumper` | `dump()` and the ONE object→document reconstruction (`dumpable_kwargs`), plus `to_markers` (the in-memory document `configure()` resolves). |

**Rule.** `broadcast` MUST NOT materialize anything — no `flow`, no `_flow_recursive`. Code that
BUILDS an object belongs in `engine`. That prohibition is why the edge is one-directional.

**Rule.** NO lazy seam: the engine imports nothing from `loader` — it works on PREPARED data
only — and `tests/test_load_stages.py::test_the_engine_no_longer_imports_the_loader` pins it.
`broadcast → engine → loader` is the only direction.

### `load()` is the ONE door (2026-08-17)

**Rule.** Every stop point on the document pipeline is `load(x, until=<stage>)`, with `Stage =
Literal["raw", "document", "settled", "objects"]` (`loader.Stage`; `_STAGES = get_args(Stage)` is
the ONE runtime tuple, an unknown value raises `ConfigurationError`, never defaults). `load` accepts
a path, YAML text or already-parsed data (dict / list / marker) and runs the passes the input still
needs — passes already applied are idempotent, so `load(load(x, until="document")) == load(x)`.
**Parsed-data input is COPIED on entry** (`merger.document_copy`, 2026-08-19 — SR3): the passes
write in place (pass 4 rewrote a marker's kwargs, `import:` was popped from the caller's dict),
so a raw document loaded twice answered with the FIRST call's activation and
`discover_dimension_values(raw)` went empty after one load. Markers copy through a memo (a
shared marker stays ONE marker) and an explicit `context` rides the SAME memo — the
slot-identity search across `data`/`context` depends on it; non-document leaves keep identity.
`return_paths=True` returns `(result, paths)`, `paths` being every file read for that call in read
order (a scope-spliced include included). Do NOT add a second entry name for a stage, and do
not make a stage reachable for one input shape only — every stage takes a path, text or parsed
data. `engine.materialize` (7–9) and `engine.settle` (7) are the engine's PREPARED-data
functions behind `load` — importable, not exported — and **passes 5–6 run ONCE, in
`loader._load`, for `data` and for an explicit `context`; the engine entries run neither**
(pinned: `test_the_engine_entries_run_neither_pass_5_nor_pass_6`). Consequence: interpolation
is a DOCUMENT pass — runtime kwargs handed to `flow()` from code keep their text, for a bare type
exactly as for a code-built marker (`test_runtime_kwargs_of_a_bare_type_are_code_not_document`).

**Rule — a `Path`, or a one-line `str` ending in `.yaml`/`.yml`, NAMES A FILE.** A missing one
raises `ConfigFileNotFoundError` — a typo'd path must not parse as YAML text and load as
nothing. Any other `str` keeps the exists-under-the-search-tiers probe and otherwise parses as
text (`loader._names_a_file`).

**Pins.** `tests/test_load_stages.py` (the four stages on one document, the list-root pro/con, the
idempotence, `return_paths` incl. the scope-spliced include and the stage that never opened it,
the exact signature, the seam, the missing-file rule and its con case).
**Docs.** `docs/lifecycle.md` → "Where you can stop", `docs/api-index.md`,
`docs/architecture.md` record 20.

**Rule.** The engine state rides ONE `contextvars.ContextVar`, so it is inherited by asyncio tasks
and `asyncio.to_thread` workers — NOT by a raw `Thread` / `run_in_executor`, which need
`contextvars.copy_context().run(...)` or an `active_context` inside the worker. `active_context(ctx)`
is the ONE sanctioned way for external code to activate a resolution context; downstream reach-ins
into the engine state are PROHIBITED. Note it does NOT enable broadcasting — pass the document
explicitly (`load(fluid, context=document)`) when a flat config's keys must reach the object.

**Rule.** `flow()` is a DISPATCHER over `_flow_*` phase helpers. Keep new marker behaviour in a
helper; never re-inline the mega-function.

**Rule.** Names that HAD users stay re-exported from `engine` (`# noqa: F401`); NEW code imports
from the real home. Zero-user re-exports are pruned as found — the ledger is the CHANGELOG. A test
capturing broadcast diagnostics monkeypatches `confluid.broadcast.logger`, not `confluid.engine.logger`.

**Why.** The ordered-merge rule was implemented twice — engine and configurator — and the copies
diverged four ways in one day, three silently. `docs/architecture.md` records 3 and 8.
**Docs.** `docs/concurrency.md`.

### Every slot reader projects from `introspect.slots`

**Rule.** "Which slots does this target have?" has ONE answer: `introspect.slots(target)`,
returning `Slot(name, kind, annotation, default, source)` in signature order. A reader states its
rule as a KIND SET (`slot_names(target, {"keyword", "var_keyword"})`), never as a re-derived
"minus self/cls" filter. `body_slot_names(target)` is the sibling projection for the different
question "what does the ``__init__`` body assign" — `slots()` reports a name once and lets the
signature claim it, so `self.model = model` is a parameter there and a body assignment here.

**Rule.** `var_positional` is NOT a slot on any surface: a `*args` name can never be passed by
keyword, so a config key of that name addresses nothing. `positional_only` IS settable and that
asymmetry is deliberate — it falls through to a post-init `setattr`, which is what `configure()`
has always done for it, so both paths agree. `var_keyword` makes the accept-list `None`
(accept-everything) and is kept by `_ctor_params`; it is never a DECLARED name.

**Rule.** Only a KIND may exclude a slot — a NAME never does (2026-08-13). `to_pydantic`'s
`_SKIP_PARAMS` name-set dropped an ordinary parameter literally named `args`/`kwargs` from the
model alone among the readers, and `extra="forbid"` then refused the legal constructor call.
The projections are the module constants `pydantic_export._FIELD_KINDS` and `dumper._DUMP_KINDS`;
`broadcast._get_param_kinds` classifies `slots()` annotations (so an ANNOTATED body slot takes a
dict/list value like the equivalent ctor param, on BOTH paths), and `_classify_annotation` peels
`Annotated` first because `slots()` resolves hints WITH extras.

**Why.** Six readers each walked the signature with a different kind filter and gave FIVE
different answers for one class — including the public `declares_key`, which answered OPPOSITELY
for the same parameter depending on whether the class also took `**kwargs`. None of it raised:
it produced a form with a missing field, a CLI flag that does nothing, a schema that omits a knob.
`docs/architecture.md` record 12.

**Rule — a DECLARED-OPTIONS listing filters body slots by `owner`; the accept-list does not.**
`get_hierarchy` and `to_pydantic` both report only body slots whose `owner` is `@configurable`.
The accept-list deliberately does not — a bare key may legitimately set a framework base's
`self.compiled`, and the engine subtracts those later (`_get_parent_attr_blacklist`). Adding a
body-slot reader means choosing that filter EXPLICITLY: shipping one without it put twelve
`keras.Model` internals (`predict_function`, `supports_jit`, `compiled`, …) into `--docs` as
configuration options, measured at 522 of 859 body slots workspace-wide. The live walker never
had the bug — it reads `get_configurable_attrs`, which applies the same subtraction.

**Rule — `Slot` carries the DECLARED type and the DECLARING class.** A body slot's `annotation`
is resolved once, in the enumeration (`resolve_ast_annotation`), never again per reader.
`Slot.owner` is the MRO class that declared it, and it is what lets two readers take different
MRO SCOPES from one walk: the accept-list wants a non-`@configurable` base's `self.training`
(a bare key may set it), `to_pydantic` must NOT (it would become a field on every schema in a
torch/Lightning tree). Express a scope as an `owner` filter — never as a second walk.

**Pins.** `tests/test_introspection_agreement.py` — the three-answer table, one test per
difference stating whether it is deliberate, and
`::test_a_param_literally_named_args_or_kwargs_is_a_slot_for_EVERY_reader` (the name-vs-kind rule,
`to_pydantic`'s field set included so a private walk cannot drift unnoticed).
`tests/test_pydantic_export.py::test_a_non_configurable_bases_body_slots_never_become_schema_fields`
/ `::test_slot_owner_names_the_declaring_class` / `::test_body_slot_ANNOTATIONS_reach_the_generated_model`
/ `::test_an_unresolvable_annotation_still_raises_introspection_error` (the probe: `slots()` is
best-effort by design, `to_pydantic` raises by contract).
`tests/test_broadcast_robustness.py::test_param_kinds_reports_an_annotated_body_slot` /
`::test_param_kinds_peels_annotated_metadata`;
`tests/test_parity.py::test_an_addressed_list_at_an_annotated_body_slot_lands_on_the_LOAD_path`;
`tests/test_dumper.py::test_a_stored_variadic_bundle_is_not_dumped`.

### Every signature reader goes through `introspect.init_callable`

**Rule.** "Whose signature governs calling this target?" has ONE answer: `init_callable(target)` —
a class's `__init__`, any other callable itself. NEVER read `target.__init__` directly.

**Why.** For a function that resolves to `object.__init__` = `(*args, **kwargs)`, so the reader
answers "accepts everything" and every real kwarg is dropped. It has bitten four separate readers:
the accept-lists, the three `accepts_*` predicates, the marker-name scans, and — until 2026-08-11 —
`schema.get_hierarchy`, which returned `{}` for every registered builder function while
`input_specs`/`to_pydantic`/`parse_param_docs` reported its params.

**Pins.** `tests/test_callable_target.py` (incl.
`::test_get_hierarchy_reads_a_registered_functions_own_signature`).

### Per-pass caches key on IDENTITY

**Rule.** Every per-pass introspection cache keys on `broadcast._cache_key(target)` — the target
OBJECT — never on `f"{module}.{qualname}"`. Register a new one with
`broadcast.register_pass_cache(...)` at its declaration; the ONE clear site is
`broadcast.clear_pass_caches()`, fired by `engine.materialize()`, `engine.settle()` AND `configure()`. Never add
a hand-rolled `.clear()` block. Cache ownership follows module ownership — a cache another module
owns registers itself.

**Why.** The dotted name is not unique — the registry's `_claim_key` suffixes `~N` for exactly this
case — so two classes defined in one scope shared an accept-list: the second was built on its
defaults and had the first one's key setattr'd onto it, silently (2026-08-11).

**Pins.** `tests/test_duplicate_names.py::test_same_qualname_classes_do_not_share_an_accept_list`.

### Tag-based IR — Fluid objects are the ONLY intermediate representation

**Rule.** All serialization uses YAML tags; in memory the ONLY marker representation is the Fluid
family. The legacy marker DICTS (`{"_confluid_class_": …}`) are GONE — never reintroduce a dict
branch "for compatibility"; they were never persisted, so there is nothing to be compatible with.
Code synthesizing a marker builds `Instance(cls_name)` then `.kwargs.update(...)`.

### Tags live on `ConfluidLoader` — NEVER the global `yaml.SafeLoader`

**Rule.** Tag constructors are registered ONCE at module import on `loader.ConfluidLoader`.
Confluid's entry points parse via `yaml.load(..., Loader=ConfluidLoader)`. `resolver.parse_value`
deliberately stays on plain `yaml.safe_load` (scalar coercion). A test needing raw tagged parsing
uses `ConfluidLoader` explicitly.

**Why.** Registering globally would make every `yaml.safe_load` in the process parse confluid tags,
handing Fluid markers to unrelated libraries and masking bugs behind import order.

**Pins.** `tests/test_loader.py::test_global_safe_loader_stays_clean`.

### Config-file search paths — ONE resolver, local beats XDG

**Rule.** Every relative config path (the entry path AND each `include:`) resolves through
`loader.resolve_config_path` — the ONLY path-probe site. Never add a second `.exists()` chain.

**Detail.** Tier order, first existing wins: including file's directory (includes only) → CWD →
`./config/` → XDG base dirs (`$XDG_CONFIG_HOME` then each `$XDG_CONFIG_DIRS`; empty == unset).
Under an XDG base dir: app name set → `<base>/<app>/` then `<base>/confluid/`; unset → the bare base
dir (a documented footgun — CLI frameworks set their app name at startup). Absolute paths bypass the
search; a total miss returns the input so the caller's not-found handling fires. Resolution happens
BEFORE `Path.resolve()`, so circular-include detection operates on the real file.

**Rule.** Tests MUST sandbox `XDG_CONFIG_HOME`/`XDG_CONFIG_DIRS`/`HOME` and chdir into `tmp_path` —
a developer's real `~/.config` must never leak into a run.

**Pins.** `tests/test_search_paths.py`. **Docs.** `docs/search-paths.md`.

### ONE path grammar, ONE policy — the FIRST segment decides

**Rule.** Every dotted/bracketed path — `!ref:` targets, `${key.path}`, `configure()`'s dotted
candidates — is tokenized by `resolver._parse_path_segments` and walked by `_walk_path_segments`.
Extend the shared walker; never add a fourth grammar — and never re-add a second POLICY.
The walker's MISS is the `PATH_MISS` sentinel, never `None` (2026-08-19, BUGS-2026-08-19
SR6/SR7): a walk that FOUND a legal `null` is a hit — conflating the two refused
`!ref:cfg.x` on `cfg: {x: null}` as an "attribute reference" naming an object that does not
exist, and left `${a.b}` to the same null as literal text. The three consumers where the
difference matters read the sentinel (`refuse_attribute_reference`'s probe, the `${...}`
placeholder, pass 7's `_settle_reference`); `_lookup_path` keeps the legacy None contract for
pass-6 aliasing and the `$key` selector, which act only on non-null hits. The literal-int
segment steps into a DICT exactly as the `idxref` segment always did (int key first,
digit-string fallback), so an int-keyed table (`class_names: {1: DJI}`) is addressable by
every spelling.

**Rule — attribute references are REMOVED (record 19, user ruling 1b, 2026-08-17).** The
walker is STRUCTURAL: a `key` segment steps into a dict, an `idx` segment into a list, and
anything else — a marker, a live object, a scalar — ends the walk. What a dotted `!ref:` MEANS is
decided by its FIRST segment: a DOCUMENT KEY walks structure only (`!ref:cfg.lr`,
`!ref:packs[1].name`); anything else is an IMPORT PATH (`!ref:posixpath.join` — the spelling
`dump()` emits for a function-valued param, and the reason import refs stay). A reference whose
first segment is a document key and whose structural walk misses was asking for the object policy
that no longer exists — reading `.train` off the object built at `split`, or calling `.build()` on
it — and is REFUSED by `resolver.refuse_attribute_reference` with the node's `file:line:col` and the
rewrite (a selector parameter on the referent's class + `!ref:split`, or the marker written again
with the selector set). The refusal fires in `_flow_recursive` AND `_flow_reference`, i.e. under
`load(until="settled")` as well as `load()` — that is how `hydraide` reports it (the same message; the `hydraide` command renders it as one line, exit 1).
Nothing is constructed on behalf of a Reference. Two things did NOT
change and are pinned as CON cases: a PURELY structural resolution still returns `None` from the
rich path (pass 7's `_settle_reference` walks it and INLINES the value), and a
document key literally named `a.b` still wins over the walk. Do NOT "extend" the walker into a
marker's kwargs (`!ref:model.hidden`) — census 2026-08-17: zero uses; it is refused like an
attribute, not silently invented.
**Pins.** `tests/test_attribute_refs_removed.py` (the refusal on both paths + hydraide, the
method-call refusal, every CON row, the deletion pin, the OmegaConf-parseability pin),
`tests/test_list_index_refs.py`,
`tests/test_ref_identity.py::test_a_dotted_attribute_ref_is_refused_not_resolved`.

### Two spellings, ONE intermediate representation

**Rule.** Confluid reads a document written with YAML TAGS (`!class:` / `!partial:` (alias
`!lazy:`) / `!ref:` / `!scope:` / `!notscope:`) or with RESERVED KEYS (`_target_` / `_partial_` /
`_ref_` / `_scope_` / `_notscope_`). Both MUST produce the SAME Fluid markers. A behaviour reachable from
only one spelling is a BUG in that spelling — never a feature of it. The ONE deliberate asymmetry:
`_scope_` takes a MAPPING and can therefore carry several dimensions, which a tag suffix (a string)
cannot.

**Rule.** The reserved-key path is a PARSE-TIME conversion and nothing else. Do not add
reserved-key branches to `scopes`, `broadcast`, `engine`, `configurator` or `dumper` — by the time
those run, only markers exist. `loader._reserved_to_marker` is the ONE conversion site.

**Rule.** The conversion rides the DEFAULT MAPPING tag and MUST decide from the YAML NODE's key
names, never by constructing values first. A mapping carrying no reserved key delegates to
PyYAML's own constructor — that fast path is what preserves ordinary-mapping performance and alias
/ recursion behaviour. Markers built this way are stamped via the shared `_stamp_loc`, so the new
spelling keeps the tag form's located diagnostics.

**Rule — the key-name read SEES THROUGH merge keys, and that is `loader._node_key_names`.** A
`<<:` node's own literal key is `<<`, so reading the literal scalar keys made an anchored marker
(`<<: *base`, the standard many-variants-of-one-node idiom, core YAML 1.1) read as "carries no
reserved key" and load as an inert dict carrying a literal `_target_` — which `flow()` did not
rescue either (P2). Never simplify the helper back to a comprehension over `node.value`. Three
properties are load-bearing and each is pinned: it matches the MERGE TAG, never the text `<<`
(a quoted `"<<"` is an ordinary string key); it handles both `<<: *a` and `<<: [*a, *b]`, and
recurses for a chained merge; and its `seen` store is `Dict[int, Any]` whose value is the node,
because a self-referential merge composes fine in PyYAML (`a: &x {<<: *x, k: 1}` → `{'k': 1}`)
and would otherwise recurse forever. Note the conversion itself never needed fixing —
`construct_mapping(deep=True)` always resolved the merge, which is why ONE unrelated literal
reserved key on the node was enough to make the anchor's `_target_` work.
**Pins.** the merge-key group in `tests/test_plain_format.py`.

**Rule.** A malformed marker raises a located `ConfigurationError` at load. Never degrade one
silently — that failure mode is precisely what this format replaces (`!class:Model(a=1, b=2)`,
with one space, produced a target named `Model(a=1,` and dropped both kwargs, with no error).
The TAG spelling enforces it too (2026-08-19, BUGS-2026-08-19 PA11/PA13/PA14/PA19/PA20/PA28):
`loader._refuse_degrading_tag_shapes` refuses a scalar/sequence body and the spaced-inline shape
above; `_parse_inline_kwargs` refuses a fragment without `=` (which is also how a `,` inside an
inline value surfaces — lists go in the block body); `_refuse_reserved_keys_in_tag_body` refuses
a reserved key inside a `!class:`/`!partial:` body (a scope body's keys are ordinary config keys
and are exempt); an empty `!scope:` suffix and a non-string `_scope_` dimension value are refused
like the boolean always was; `import:` refuses non-string shapes like `include:` does; empty TEXT
loads as `{}`, and a DIRECTORY at a config path is a located `ConfigFileNotFoundError`, never a
raw `IsADirectoryError`.

**Rule — a RESERVED key inside a DOTTED key is refused (P16).** `model._target_: Box` cannot
become a marker: conversion happens at PARSE time and `merger.expand_dotted_keys` runs afterwards,
so by the time `_target_` is a key of its own mapping there is nobody left to convert it — the
document kept a literal `_target_` as data and `flow()` did not rescue it. The refusal covers a
reserved segment in ANY position, because every shape degraded the same silent way: a NESTED
dotted key is never expanded at all (expansion is top-level only) and stayed a literal
`inner._target_`, and inside a marker's kwargs it became a constructor kwarg named `sub._target_`.
Do NOT "fix" this by re-converting after expansion — that would be a SECOND conversion site, and a
marker built there has no YAML node, so every later error about it loses its `file:line:col`. The
false-positive guard that matters: only the six reserved names are special, so `model._custom_: 3`
and `model.size: 3` are untouched — and a dotted kwarg merging into an already-nested marker
(`model: {_target_: Box}` plus `model.size: 3`) still builds `Box(size=3)`, which is exactly what
made the defect invisible.
**Pins.** the P16 group in `tests/test_plain_format.py`, incl.
`::test_a_dotted_kwarg_still_merges_into_an_existing_marker` and
`::test_a_non_reserved_underscore_wrapped_key_is_untouched`.

**Rule — both parse-time key-shape refusals go through `loader._refuse_malformed_keys`**, called
from `map_constructor` (untagged mappings, before the reserved-key gate's fast path) and from
`_str_keyed_mapping` (the four tag constructors). A new key-shape rule is added THERE, never to
one caller — that is what keeps the two spellings from diverging.

**Rule — a DUPLICATE mapping key is refused, in BOTH spellings.** The YAML spec restricts a
mapping's keys to be unique and lists non-unique keys among its loading failure points, leaving
the processor's response open; PyYAML keeps the LAST value silently. Confluid raises a located
`ConfigurationError` naming BOTH lines (`duplicate key 'include' at f.yaml:3:1 (first written at
line 1)`), because the discarded value is otherwise invisible — two `include:` directives lost a
whole FILE before the loader ran, and the survivor spliced at the FIRST occurrence's position (a
collapsed duplicate keeps first-insertion order), inverting the documented later-line-wins rule
(P11). Three properties, each pinned: identity is `(tag, value)`, never text — `1:` and `"1":`
are an int key and a str key and PyYAML keeps both; MERGE keys are skipped, because a `<<:`-merged
key the node also writes literally is override semantics, not duplication; and the check binds the
TAG spelling too, via `loader._str_keyed_mapping`, the ONE mapping-construction site the four tag
constructors share (`map_constructor` deliberately refuses EARLIER — an ordinary mapping takes the
fast path to PyYAML and never constructs through that helper). Adding a fifth constructor that
builds a mapping means routing it through `_str_keyed_mapping`, not re-inlining the dict
comprehension.
**Pins.** the duplicate-key group in `tests/test_loader.py`, incl.
`::test_same_text_different_TAG_keys_are_not_duplicates`,
`::test_a_merge_key_overridden_by_a_local_key_is_NOT_a_duplicate` and
`::test_the_refusal_binds_the_TAG_spelling_too`.

**Rule — `_partial_` pairs with `_target_` and NOTHING else, refused at ONE site.** It modifies
CONSTRUCTION and `_target_` is the only key that constructs, so beside `_ref_` /
`_scope_` / `_notscope_` it raises a located `ConfigurationError` naming the working spelling
(put the modifier on the node being constructed, then reference it). The check sits in
`_reserved_to_marker` immediately after `key = present[0]` — BEFORE the per-discriminator
branches, because each of those returns before the `_target_` branch's `PARTIAL_KEY` read, which
is exactly how the key used to be stripped into oblivion and flowed EAGERLY with no error (P10).
Never move the check into a branch. Two related properties: the refusal is PARSE-time, so an
unactivated `_scope_` block carrying the modifier still raises (a document must not be valid or
invalid depending on the run's `--scope` flags), and the lone-modifier message must name
`_target_` ALONE — it advertised all five discriminators while honouring one, which is a program
emitting false documentation about itself.
**Pins.** the P10 group in `tests/test_plain_format.py`, incl.
`::test_the_lone_modifier_error_names_only_the_key_that_works` and
`::test_a_scope_block_without_the_modifier_is_untouched` (the false-positive guard).

**Rule — scope grammar.** `loader._parse_scope_suffix` is the ONE splitter for every activation
spelling (the tag suffix, the `_scope_:` value, a CLI `--scope` argument). Never add a second.

**Rule — a marker is a TAG or a reserved-key MAPPING; a STRING is never parsed as one.** There
is no third grammar: `Resolver.resolve` does not read markers out of text (user instruction
2026-08-18 — "do not allow this, simplifies the parser, the user has to use the block form").
A string value starting with a marker prefix (`!class:` / `!partial:` / `!lazy:` / `!ref:` /
`!scope:` / `!notscope:` — `resolver._STRING_MARKERS_ALL`) is a quoted tag and is REFUSED with a
`ConfigurationError` quoting the text and naming the two lines that work — the tag unquoted (block
body for nested values) and the reserved-key mapping (`resolver._refuse_marker_string` /
`_plain_yaml_for`), at the top level and inside a marker's own kwargs alike. The refusal matches
the exact prefixes, NEVER a bare leading `!` — an ordinary value may start with one. It carries no
`file:line` because a scalar has none to carry (only markers are `_stamp_loc`-ed); that is tracked
in `TASKS.md`, not worked around. `flow()` of a string is a pass-through (a string is a value);
`Resolver._resolve_ref` returns `None` on a miss, not a sentinel string. Do NOT re-add a string
parser "for a nested `!ref:` inside an inline tag" — that is what the block body is for.

**Pins.** `tests/test_loader.py::test_a_marker_written_as_a_quoted_STRING_is_refused` (every
prefix) / `::test_a_quoted_marker_inside_a_markers_own_kwargs_is_REFUSED` /
`::test_an_ordinary_value_starting_with_a_bang_is_untouched` (the false-positive guard),
`tests/test_resolver.py::test_a_marker_written_as_a_string_is_refused_at_every_depth`,
`tests/test_fluid.py::test_flow_of_a_string_is_a_pass_through`.

**Rule — tags are the PREFERRED AUTHORING form; the reserved keys are the MACHINE form; NEITHER
warns** (user ruling 2026-08-15, architecture record 19). Both spellings are first-class input
and may be mixed in one file. `!partial:` is the tag for a deferred marker (the same name as the
key it emits); `!lazy:` is an alias — whether it is ever removed is a later ruling, not a
standing plan. Do not add a tag warning, a tag→plain migration tool, or a "no tags in
examples" scan. The two-spellings-one-IR invariant
is unchanged and has a THIRD witness now — `hydraide` emits byte-identical output whichever
spelling produced the document.

**Rule — the ONE codemod runs plain → tags, and it is `confluid.spelling.to_tags`** (record 19).
It converts a file a human wants to EDIT again (a committed hydraide artefact, a config
written before the ruling) and it edits LINES — tag on the key line, reserved-key line deleted,
every other byte kept — because these files are mostly comments and a parse-and-rewrite would
reformat them. Three properties are load-bearing: (1) what the grammar cannot convert is
REPORTED as a `Finding(path, line, text, reason)` and left in place — never guessed at (a
multi-dimension `_scope_`, a `<<:` merged into a marker's keys); (2) `convert_file` writes ONLY
when `hydraide.emit(before) == hydraide.emit(after)` under every scope activation the document
declares — the safety is the check, not the grammar; (3) inside a FLOW container a tag must be
followed by whitespace (measured: PyYAML scans `!c:N(h=8)}` as one tag and `[!ref:a, !ref:b]`
reads `ref:a,`), so a nested marker takes the flow-body form `!class:X {…}` / `!class:X {}` and a
`${ref:}` inside `[…]` stays. Generated documents (a `regen_examples` output, a `dump()`, a
`runs/…/final.yaml`) are the MACHINE form and are NOT converted — a consumer's freshness gate
compares them byte-for-byte to what its serializer renders.
**Pins.** `tests/test_spelling.py` — the shape matrix, the three findings, idempotence, tag-form
no-op, and `convert_file`'s activation-gated write.

**Rule — `hydraide` is the ONE emitter of the plain form, and it is a WRAPPER over passes 1–7.**
`hydraide.emit(source, scopes=…)` is `dump(load(source, until="settled"))` plus named anchors AND the
re-emitted `import:` directive (`loader._IMPORT_ACCUMULATOR`, the include accumulator's sibling —
pass 2 consumes the key, and without it the artefact could not reload in a fresh process,
BUGS-2026-08-19 CD13); it adds NO pass and
re-derives NO rule — if the emitted document is wrong, the defect is in pass 7 (`engine.settle`) or `dump()`,
never in `hydraide.py`. It refuses exactly what `load()` refuses (a located `ConfigurationError`) —
including an unresolvable STRING target, which pass 7 refuses since 2026-08-19 (CD14: it used to
sail through settle with the accept-EVERYTHING list and absorb every bare key, and `check` blessed
the typo with exit 0; nothing imports between pass 7 and pass 8 inside one load, so the refusal
loses nothing). Anchor names are deterministically UNIQUE (`&m_0`, `&m_0-2` — CD15), and the
COMMAND renders PyYAML's own syntax errors and output-write failures as one line + exit 1 (CD16).
**The `hydraide` COMMAND is `confluid/cli.py` — Click, the OPTIONAL `confluid[cli]` extra,
console script `hydraide = confluid.cli:main` (user instruction 2026-08-17).** Three verbs — `emit CONFIG [--scope DIM=VALUE]… [-o FILE]`,
`check CONFIG`, `completion bash|zsh|fish` — and the contract: a located `ConfluidError` is ONE
line on stderr + exit 1 (`click.ClickException`), a usage error exits 2, a stale `check` exits 1
with the diff on stdout; a relative CONFIG resolves through `resolve_config_path` (the loader's
tiers) BEFORE the exists check. **Completion is part of the contract**: `--scope` completes
`dim=value` from `discover_dimension_values(load(config, until="raw"))`; because Click's resilient
parse aborts on a dangling `--scope` BEFORE binding the positional CONFIG, the completer falls
back to the shell's `COMP_WORDS` (exported by all three of Click's scripts) — never re-derive the
config from anywhere else. Click is imported by `confluid.cli` ONLY (guarded `ImportError` naming
the extra); the engine never imports it. Named anchors ride `dump(anchor_names=…)` — a `generate_anchor` hook
keyed on the shared value's SHORTEST path (`&preprocess_0`); the map is computed on the resolved
tree, where identity already means what the document meant. Two engine facts are pinned in the
tool's suite because the tool exposes them: identity is per MARKER, not per container
(`load(until="settled")` copies a list reached via `${ref:}` and shares its elements); and an anchor does NOT
follow an include-overlay tune (P1's COPY at the key; alias sites keep the original — the earlier
"measured correct" probe was seeing bare-key broadcast through a same-named parameter).
**Pins.** `tests/test_hydraide.py` — byte-identical output for both spellings, idempotence
(`emit(emit(x)) == emit(x)`, what `check` rests on), no-warning for either spelling,
`!partial:` == `!lazy:`, and the two identity pins; `tests/test_cli.py` — the verbs, the three
exit codes, the one-line failure contract, the search tiers, `completion` for each shell, and
`--scope` completing from the document (incl. the no-config con case).
**Docs.** `docs/hydraide.md`. **Example.** `examples/hydraide.py`.

**Why.** `docs/architecture.md` records 11 and 19. The tag format is not YAML anything else can
read, which locks out `yq`, editor schemas and linters — so the plain form has to exist; and a
human writing YAML by hand prefers one line per node — so the tag form has to exist. One
preprocessor turns the one into the other, once, into a file.

**Pins.** `tests/test_plain_format.py` — the `test_both_spellings_agree` matrix, the malformed-marker
group, `::test_a_full_document_is_readable_by_plain_yaml`, `::test_neither_spelling_warns`,
`::test_yaml_anchors_and_aliases_still_work`, `::test_the_two_spellings_can_be_mixed_in_one_document`.
**Docs.** `docs/plain-format.md`. **Example.** `examples/plain_format.py`.

### There are exactly TWO construction modes

**Rule.** `partial` decides whether a marker is built, and NOTHING else does — not the parent's
configurability, not the nesting depth, not document position. `Target` is built by
materialization; `PartialClass` never is. `!class:Foo` and `!class:Foo()` are the SAME thing now (the
trailing `()` is inert); `!lazy:` / `_partial_: true` is the only spelling that defers.

**Rule.** A slot the RECEIVING class declared deferred (`Partial[T]`, or a body slot holding
`PartialClass(...)`) keeps its value unbuilt whatever the value says — the receiver's declared
contract, read via `partial_param_names(target)` in `_flow_target` AND on the post-init path
(2026-08-19, BUGS-2026-08-19 ENG-17: the post-init promotion consulted only the VALUE signal, so
an annotation-only slot — `self.optimizer: Partial[Optim] = None` — built its `_target_:` value
eagerly, and the slot-tune re-resolve built a tuned `Partial[T]` marker whenever ONE later bare
key existed). That is a slot declaration, not parent-context guessing: static, local to the
class, and readable. The ONE promotion site (marker → `PartialClass`, with the warning) stays
`_apply_post_init_attrs` — and the promoted marker KEEPS the original's `_yaml_loc` and merge
bookkeeping, so a later `flow()` failure still names the document line (ENG-11); the warning
names it too.

**Rule — a ctor-DEFAULT marker builds ONE child per host (2026-08-19, ENG-16).** A default is
evaluated once at class definition, so the id()-keyed instance memo handed every host in one pass
the FIRST host's built child (two Cars, one Engine) — where the body-slot spelling and the same
code outside a pass both build per host. `_broadcast_onto_instance` copies a marker whose slot
DEFAULT it is (identity against `slots()`' defaults) before resolving, so the memo keys a
per-host object. Sharing keeps its one spelling, `!ref:` — pinned as the con.

**Rule — a typo'd key on an UNREGISTERED target is DROPPED, audibly (2026-08-19, ENG-7).**
An unregistered target does not participate in the config graph, so nothing is applied — but the
drop warns (located) and records `unknown-attribute`, instead of vanishing with an empty report
while the same typo on a `@configurable` class warned. `register()` the class to accept extra keys.

**Rule — the reference-kwarg deferral catch is `ReferenceResolutionError`, never `ValueError`
(2026-08-19, ENG-3).** `ConfigurationError` dual-inherits `ValueError`, so the broad catch also
swallowed the REFERENT's own constructor crash and `UnknownClassError`, silently leaving the
`Reference` in the slot. A genuine miss still defers; everything else propagates.

**Rule.** Never reintroduce a third mode or a context-dependent build rule. The deleted middle state
(`Class`) was justified as "so broadcasting can still reach it", which is not a reason: broadcasting
is pass 7 and construction is pass 8, so a BUILT node already receives every cascading key before
its constructor runs.

**Rule — EVERY id()-keyed store pins the object whose address keys it.** An `id()` is unique
only while the object is ALIVE; CPython reuses freed addresses, so an unpinned entry answers for
the OLD object when a NEW one lands on the address. The rule binds every such store, each with
its own pin mechanism (2026-08-13 — four unpinned stores were measured serving wrong answers;
X1/X3/E1/E2/I7 in the CHANGELOG):

- engine memos (`flow_memo` / `instance_memo`) — pin in `_EngineState.memo_keepalive`; all FOUR
  write sites pin: the three `instance_memo` writes (the public-`flow()` write and the nested-build
  write included — an unpinned ctor-local marker handed one maker ANOTHER maker's widget) AND the
  pass-7 `flow_memo` write in `_flow_recursive` (2026-08-19: it keyed on a `tune_marker` copy that
  died with its `merged_kwargs`; 22 of 300 trainers got another trainer's optimizer — BC1);
- `configure()`'s visited store — a `Dict[int, Any]` whose VALUE is the object (recording an id
  IS the pin); a plain `Set[int]` silently skipped 450 of 512 objects under DEFAULT gc;
- `introspect._slots_cache` — the value is `(target, slots)`; the first element pins an
  id-keyed unhashable callable;
- the ordering stamp `_order_resolved` is written to MARKERS only (`isinstance(..., Fluid)`) —
  the tune path used to stamp the BUILT instance, crashing `__slots__` targets.

A NEW id-keyed dict/set gets its pin in the same change, or it does not merge.
**Pins.** `tests/test_memo_pinning.py` (all five) and
`tests/test_ref_identity.py::test_sibling_list_items_do_not_share_an_instance_via_recycled_ids`.

**Detail.** Inline `(k=v)` scalars are coerced via `parse_value` in both the unquoted and quoted
forms, and MERGE with a mapping body (the block wins on conflict). The unquoted form cannot contain
spaces or a nested tag (YAML allows one tag per node). `1e-3` is not a YAML float — write `1.0e-3`.
A kwarg literally named `target` is legal (the loader assigns kwargs post-construction).

**Pins.** `tests/test_loader.py`, `tests/test_partial.py`. **Docs.** `docs/targets.md`.

### Clone is REMOVED (user ruling 2026-08-15)

**Rule.** There is no copy marker. `Clone` / `_clone_` / `${clone:}` / the `!clone:` tag are gone;
independence has ONE spelling — write the marker again (`<<:` an anchored base into each site if
the recipe is long). `_ref_` / `${ref:}` remain the ONE sharing spelling. Do NOT reintroduce a
copy marker (architecture record 18 — a zero-user census, ruled).

**Rule — the removal is LOUD, in every spelling.** `_clone_` stays in `loader.RESERVED_KEYS`
purely so a mapping carrying it still reaches the marker gate and is REFUSED with a location,
instead of loading silently as plain data with a literal `_clone_` key (the degradation the format
forbids). `clone` stays in `resolver._MARKER_RESOLVERS` for the same reason — `${clone:x}` routes
to the marker resolver and raises there rather than surviving as a literal string. The `!clone:`
constructor is unregistered, so the tag fails at parse.
**Pins.** the Clone-removal group in `tests/test_plain_format.py` (all four spellings + the
`_ref_` con case).

### A target may be ANY callable

**Rule.** `!class:`/`!lazy:` targets, `Target`/`PartialClass`/`flow()` targets, AND the
`@configurable` / `register` decorators all accept any callable — a class OR a builder function.
Introspect the target's OWN signature (see `init_callable`).

**Detail.** `@configurable` on a function wraps its CALL for validation and lands markers +
registration on the WRAPPER. `register()` stays validation-free but DOES carry the accept-list
controls `broadcast=False` / `broadcast_attrs=[...]` — a class you don't own is exactly the one you
cannot fix by declaring parameters, and a third-party `**kwargs` constructor is the case with no
accept-list at all. `to_pydantic` is callable-aware; two JSON-Schema landmines are coerced to `Any`
(an Enum whose member VALUES are not JSON primitives, and a `Callable[...]` param). A callable
returning a `__dict__`-less value is tolerated (the post-build broadcast step is skipped).

**Rule.** Marker-target normalization goes through the ONE `broadcast._settability_target`; never
re-inline it. Five of six inlined copies degraded a function OBJECT to `None`, so a code-built
`PartialClass(builder_fn, …)` ran the cascade with no NoBroadcast gates.

**Pins.** `tests/test_callable_target.py` (incl.
`::test_settability_predicates_read_a_functions_own_signature`), `tests/test_cross_path_pins.py`
(the D6 pair), `tests/test_no_broadcast.py` (the two `test_register_can_*` cases).

### Deferred initialization — `_partial_: true` / `Partial[T]`

**Rule.** A `PartialClass` is never AUTO-flowed — not by `load()`, not by external deep-flow
walkers. **Deferral withholds CONSTRUCTION only: a `PartialClass` IS broadcast into, exactly like a
`Target`.** Never re-add a blanket `isinstance(v, PartialClass)` early return in `_resolve_kwarg_value`.

**Rule.** An EXPLICIT `flow(node)` builds it, even with no runtime kwargs. A mapping addressed at a
slot already holding a deferred marker TUNES it, never replaces it. A marker kwarg set in CODE is a
DEFAULT and does not block a bare key.

**Rule — dict-at-slot has ONE dispatch, `broadcast.dict_at_slot_kind`, on BOTH paths (2026-08-13).**
A mapping addressed at a slot means, by what the slot HOLDS: a MARKER → tune (`tune_marker`); a
live `@configurable` object → walk into it (`engine._apply_mapping_onto_live` on the load path,
the block recursion under `configure()`); plain data / nothing → assign; any OTHER live object →
a located `ConfigurationError` (user ruling: refuse, never silently replace an object with a
dict). `_MergeSink.dict_at_slot` applies the marker arm pre-construction, so a class-block
override tunes a nested ctor-param recipe instead of deleting it. The classifier reads
`__dict__`-sourced values and the CLASS mark only — no property getter runs. Never add a
per-path arm: two arms on one path and three on the other is exactly how C1/C1b shipped
(`docs/architecture.md` record 15).

**A THIRD site is document COMPOSITION** (P1, 2026-08-15). `merger.deep_merge` recursed only when
BOTH sides were dicts, and a `Fluid` is not a dict — so an overlay mapping REPLACED the whole
marker: the commonest composition in the system (a base file defines the model, an experiment file
overrides one knob) silently produced a plain dict with no target and no error, while the DOTTED
spelling of the same override tuned it correctly. It now tunes, restricted to `Target` to mirror
the classifier's marker arm — a `Reference` base keeps the replace behaviour, since the
classifier calls those opaque. A **marker over a marker of the SAME target** tunes the same way
(2026-08-20, CD5): `trainer: !class:Trainer {lr: 0.1}` over the object's own marker used to
REPLACE the document and drop the unmentioned children (a bare `layers:` beside it went unused,
while the mapping spelling worked); same target + same marker kind → kwargs merge (copy, never
mutate); a DIFFERENT target or kind (a `!partial:` over a `!class:`) is a genuine swap and still
replaces. Target sameness is by registered/last-segment name (`merger._same_marker_target`); a
selector spelling (`@axis=$key`) never tunes. It does NOT call `tune_marker`: that helper is single-level by
design and lives above this module anyway (`broadcast` imports `merger`, so the reverse is a
cycle), while composition must RECURSE — the dotted and class-block spellings both reach a marker
nested inside the overridden one. Recursing through `deep_merge` also keeps the re-anchoring rule
applying at every depth, and the marker is COPIED, never mutated: `_preserve_identity_copy` shares
markers with the base document, so tuning in place would rewrite the included file for every other
consumer.
**Rule — an instance-name block matches the ctor DEFAULT `name` on load (2026-08-20, CD19).**
`m: {layers: 5}` matched on `configure()` (the live attr) but selected nothing on `load()` when
`name="m"` was only the constructor default: the load-path receiver
(`broadcast._receiver_for_target`) now falls back to the class's ctor default for `name` when the
marker carries no `name` kwarg — the value the built instance will hold, so both paths give one
answer. An explicit `name:` kwarg opts out; a non-str default (None, a sentinel) opts out.
**Pins.** `tests/test_broadcast_scoping.py::test_an_instance_name_block_matches_the_ctor_default_name_on_load`
(+ the explicit-name con beside it).

**Pins.** `tests/test_dict_at_slot.py` (all nine) and the marker-merge group in
`tests/test_merger.py`, incl. `::test_the_merge_recurses_into_a_NESTED_marker` and
`::test_the_merge_does_not_mutate_the_BASE_marker`.
**Docs.** `docs/broadcasting.md` → "What a mapping at a slot means".

**Rule.** Subscript the alias with the INTERFACE the slot flows into (`Partial[Optimizer]`, never
`Partial[Any]` when the type is known). `partial_param_names(cls)` is the single authority and reports a
body slot deferred by EITHER signal — a `PartialClass(...)` value or a `Partial[T]` annotation.

**Rule — a STRING annotation resolves or degrades to `Any`; it NEVER reaches a reader as a
`str`.** Two spellings put a string where a type belongs, and both used to leak it: a QUOTED
annotation, whose AST node is a string constant so `eval` returns the TEXT and the ForwardRef
check waves a plain `str` through (I4); and PEP 563, which stringifies every annotation in a
module — where `get_type_hints` is ALL-OR-NOTHING, so one `TYPE_CHECKING`-only import emptied the
map and sent EVERY parameter back to its raw string (I5). `introspect.resolve_string_annotation`
is the ONE resolver for both, with the same scope and degradation as `resolve_ast_annotation`.
A leaked `str` is the outcome that must not happen: every reader asks a question OF the annotation
(is this slot deferred? does it take a list?) and a string silently answers no to all of them —
so one unrelated bad name cost a class its deferral marks AND its container routing.
**Consequence:** `marked_param_names` PROJECTS from `slots()` instead of re-reading
`get_type_hints` — that private read was a third copy of the same all-or-nothing failure, and the
"every slot reader projects from `introspect.slots`" rule already said it should not exist. It
filters `source == "signature"` to stay a parameter scan.
**Pins.** the string-annotation group in `tests/test_introspect.py` (fixtures in
`tests/pep563_helpers.py`), incl. `::test_a_quoted_CONSTRUCTOR_PARAM_annotation_is_unchanged`
(the half that always worked) and `::test_an_UNRESOLVABLE_quoted_annotation_degrades_to_Any`.

**Rule.** `resolve_ast_annotation` MUST `inspect.unwrap` before reading `__globals__` — the
validation wrapper's globals are confluid's own, which silently typed every body slot `Any`.

**Why.** `docs/architecture.md` record 5. The engine flag that settles ordering is
`Fluid._order_resolved`, never `_yaml_loc`.

**Pins.** `tests/test_partial.py`, `tests/test_deferred_broadcasting.py`.

### Post-flow auto-solidification

**Rule.** `flow()` calls any `solidify()` method on the object it returns — including on the
IDEMPOTENCY return, so an object that arrived already built is finalized too. `solidify()` MUST be
idempotent (build-once-and-cache) and take no arguments. `flow(obj, solidify=False)` /
`load(..., solidify=False)` suppress it for the whole subtree.

**Rule — `configure()` has no walk of its own (record 19).** The objects' DOCUMENT (declared
slots — ctor params and `__init__`-body attributes, `dumpable_kwargs`) is what pass 7 sees and
`_apply` writes back. Three things are therefore structural, not rules: a class attribute is
not a slot (never walked into — C3); a marker held as an attribute is TUNED in place, never
flowed into a throwaway (C4); the ctor-kwargs capture is the DUMP FALLBACK for an eager class's
transformed param, not a thing that gets configured (a discarded ctor argument is not in the
object's document, so it is not configured). Do not add a walker to change any of them.
**Pins.** `tests/test_configure_via_document.py`, the C3/C4 groups in `tests/test_configurator.py`
(they pass by construction), `tests/test_memo_pinning.py::test_configure_reaches_every_object_even_when_gc_recycles_walk_temporaries`
(markers in a DECLARED list slot — a dynamically set attribute is not part of any document).

**Rule.** `configure()` finalizes AFTER applying: `_apply` writes the settled values and fires the
hook POST-ORDER (children first), matching the load path's ordering.

**Why.** `docs/architecture.md` record 2. Note: a task runnable needing an expensive dataset-walking
construction uses an explicit `initialize()` from `run()`, NOT this hook — so construction never
fires merely because a config was materialized.

**Pins.** `tests/test_fluid.py::test_flow_solidifies_a_live_object` /
`::test_flow_idempotent_returns_the_same_object_and_refires_the_hook` /
`::test_flow_solidify_false_leaves_a_live_object_unbuilt`;
`tests/test_configurator.py::test_configure_applies_values_before_solidify_fires` /
`::test_configure_does_not_rebuild_an_already_solidified_object`.

### Runtime injection has a POSITIONAL channel

**Rule.** `flow(node, *args, solidify=True, **kwargs)` calls `target(*args, **merged_kwargs)`.
Positional args are RUNTIME-ONLY — never stored on a marker, never emitted by `dump()`. Do NOT add
a way to store them; give the target a keyword instead.

**Detail.** They suppress `Instance` memoization; a registry-configurable bare type raises
`ConstructionError` naming the two ways out; an already-live object DROPS them. `_ctor_params`
correspondingly excludes `VAR_POSITIONAL` and `POSITIONAL_ONLY` names — a name that can never be
passed by keyword is not a keyword slot. `VAR_KEYWORD` is deliberately KEPT (see the `**kwargs`
routing rule).

**Rule — a config key naming a `*args` parameter is REFUSED, not absorbed.** An ADDRESSED key
(on the marker, or in a class-name block) whose name matches a VAR_POSITIONAL parameter raises a
located `ConfigurationError` naming the positional channel. It used to be silently set as a
post-init ATTRIBUTE: the constructor never saw it, and `obj.loaders` held a value the object
ignored. Hydra refuses the same spelling (measured, 1.3.5 — `loaders: [...]` on a `*loaders`
target raises `InstantiationException`) and offers `_args_`; confluid's channel is
`flow(node, a, b)`, so the config-side answer is the same.

**Rule — BARE keys are EXEMPT and must stay so.** A bare key is an implicit `**.key` that
cascades tree-wide and legitimately matches nothing, so a document whose top-level key collides
with some class's variadic name must keep loading. Hydra never faces this — it has no bare-key
broadcast. The exemption is structural, not a check: the accept-list drops the key before either
call site. The ONE predicate is `broadcast.refuse_if_variadic_name`, called from the two sites a
measurement identified — `engine._apply_post_init_attrs` (own kwargs) and `_MergeSink.unknown`
(class block). A `**kwargs` NAME is never refused: such a class accepts everything by design.

**Pins.** the "positional runtime args" group in `tests/test_fluid.py`, incl.
`::test_a_BARE_key_colliding_with_a_variadic_name_still_loads` and
`::test_an_ordinary_unknown_addressed_key_still_lands_as_an_attribute` (the two must-NOT-raise
cases).
**Docs.** `docs/targets.md` → "Runtime injection that has no keyword".

### Introspection without cost — `load(until="settled")` and `solidify=False`

**Rule.** `load(x, until="settled")` = pass 7 (`engine.settle`) with NO `instantiate`: broadcasting
and `!ref:` applied, markers returned, NOTHING constructed. `flow(obj, solidify=False)` constructs
but suppresses the finalize. Neither changes default behaviour.

**Detail.** The settled stage constructs NOTHING — and no Reference can make it: a structural
dotted `!ref:a.b` stays a `Reference` just as a plain `!ref:name` does, and an attribute
reference is REFUSED on this path exactly as under `load()`.

**Why.** Until 2026-08-11 reading `split.train` BUILT `split` on this path too, so the
"introspection without cost" API walked a dataset: 3.9s on a real config whose split scans 37
archives, for a call documented as constructing nothing. Two consumers already claimed behaviour
they were not getting (a visual editor's YAML importer — "no instantiation, every node is a Fluid
marker"; a flow-graph builder — "step markers stay UNbuilt"), and two projects' shipped-config
suites hand-rolled a tag-stubbing parser because the settled stage "is not an escape either".
Steady-state cost is now 10ms.

**Pins.** `tests/test_resolve.py::test_resolve_refuses_a_dotted_ATTRIBUTE_ref_exactly_as_load_does`
(one message on both paths, nothing built on either).

**Pins.** `tests/test_resolve.py`. **Docs.** `docs/lifecycle.md` → "Where you can stop".

---

## Precedence & broadcasting

### There is ONE precedence rule: DOCUMENT ORDER, last spec wins

**Rule.** Every source of a value — a marker's own kwargs, a mapping addressed at a slot, a dotted
key, a bare broadcast, a glob rider, a class-name block — competes on POSITION and nothing else.
There are no provenance tiers, no "addressed beats bare", no "code default loses to config".
`_yaml_loc` is a DIAGNOSTIC and MUST NEVER be read for precedence.

**Rule.** Anything that PRODUCES a document produces its precedence order, so it must produce the
author's reading order:

- `loader._splice_includes` pastes an included document AT the `include:` directive's line — the
  same "splice at the wrapper's slot" rule `!scope:` blocks follow. The directive's position is
  meaningful: lines below it override the paste, lines above it are overridden by it.
- `merger.deep_merge` re-anchors a key the overlay re-states at the OVERLAY's position, so a key
  written on both sides survives once, at the later position, with the later value.
- `merger.expand_dotted_mapping` anchors a fresh head where the dotted spelling was WRITTEN, never
  appended at the end — and applies every key in ONE pass, in DOCUMENT ORDER. It used to run two:
  every plain key, then every dotted key sorted by depth and then ALPHABETICALLY, so a dotted LEAF
  was always applied last and could not lose — `a.b: 1` beat a later `a: {b: 0}`, and the two
  orderings of one leaf gave the same answer (P7). The head half was fixed in August; this is the
  same bug one level down. Both branches of the single pass are SYMMETRIC: a dict landing on a dict
  merges via the `merge_leaf` hook, anything else replaces. The plain branch needs that too — in
  the old form it never met a dotted result, and an unconditional assign there drops `a.**.x` when
  a later `a: {'**': {y}}` block arrives.
  **Pins.** the position group in `tests/test_merger.py`, incl. `::test_the_two_orderings_DISAGREE`
  (the rule-level pin) and
  `tests/test_broadcast_scoping.py::test_top_level_dotted_glob_pair_merges_in_merger` (the
  symmetry guard).

**Rule — a dotted line landing DIRECTLY on a marker's kwarg keeps the LINE's position, per key
(BC4, user ruling 2026-08-19: option B — per-key, never move the whole marker).** Pass 6's fold
into the marker's kwargs would silently move the value to the MARKER's position (`t.lr: 9.0` as
the last line lost to a bare `lr:` between — `explain` said "own 9.0 — beaten, earlier"). Three
pieces, one identity: `expand_dotted_mapping(stamp_positions=True)` (the document top level ONLY —
the in-block caller does not stamp) records per kwarg the sibling keys the line out-positioned
(`fluid.dotted_positions_of` is the read surface); the scanner's `_dotted_protected` gate skips a
cascade delivery arriving VIA an out-positioned key — `via` is the delivering TOP-LEVEL key (the
bare key itself, a block's name, the rider's `'**'`), the same identity
`_cascade_scalar_positions` orders by; and `dump()` RE-EMITS a stamped kwarg as a dotted line
after the keys it beat (`_reemit_dotted_positions`), because plain YAML cannot carry the stamp
and the bare mapping would replay the contest differently — that is what keeps
`load(emit(x)) == load(x)` and `emit(emit(x)) == emit(x)`. A dotted path ending in a PLAIN dict
(fresh head, dict-valued slot) is UNTOUCHED — those contests are ordered by the existing
machinery and pinned. The marker's OTHER kwargs keep the marker's position: the dotted and
instance-block spellings of one override agree on every key (the momentum pin — the reason B was
chosen over re-anchoring the whole marker).
**Pins.** the BC4 group in `tests/test_document_order.py` (three later-spec spellings, both bare
cons, the one-kwarg-not-the-marker pin, two-marker depth, document-stage idempotence),
`tests/test_hydraide.py::test_a_dotted_kwarg_position_survives_the_emit_round_trip`.

**Rule.** The candidate set both paths' verdicts draw from is the ONE
`broadcast._cascade_scalar_positions` (bare keys at their own index, a `'**'` rider's scalar
contents at the RIDER's index). The directional reads stay per-caller (load: keys AFTER the slot;
configure: keys BEFORE the block); the candidate set may not differ again.

**Rule.** A verdict is CALL-SCOPED, never marker state — a marker-stamped verdict outlives the
document that produced it. Under `configure()` the document is rebuilt per call from the objects
(`to_markers`), so nothing carries over between calls (pinned:
`tests/test_document_order.py::test_a_second_configure_is_not_bound_by_the_first_ones_verdict`).

**Rule — a BLOCK-delivered mapping is arbitrated at the BLOCK's position, and only the scanner
still knows it.** `_scan_view` hands every `dict_at_slot` emission a `bare_before` computed at the
delivering block's own index. `_MergeSink` MUST record it (`beaten_per_slot`) and `_prepare_kwargs`
carries it out on the returned `_View`, because by the time the engine sees the merged kwargs they
have been spliced at the MARKER's slot and the block's position is gone. Pass 7 applies the verdict
ITSELF for the descent into that slot (`engine._flow_recursive._view_for` pops the beaten bare
keys and rider entries) — and the verdict binds the ADDRESSED node only: the narrowed view
protects that marker's own kwargs, while its child view is rebuilt from the UN-narrowed context
(`descend_context`), because a grandchild the block never addressed must still see the cascade
(2026-08-19, BUGS-2026-08-19 BC5 — the pop used to cut a later bare key off from the whole
subtree). So the settled document already carries the answer;
`_late_bare_keys_per_slot` still computes the verdict for a marker's OWN dict kwargs and the
engine's post-init tune consumes it for a body slot pass 7 cannot see. Deleting either half is
wrong: the second answers a question the first cannot.
**Why.** Both EDGE orderings of the block-vs-bare contest agreed across paths and were pinned; the
middle one — node first, bare key, then the block, i.e. the ordinary layout — had no pin and
diverged, `load()` answering 99 where `configure()` answered 50 (C2).
**Pins.** the C2 group in `tests/test_cross_path_pins.py` — all three orderings, per path AND
compared across paths.

**Rule.** The three engine fields on `Fluid` are read through `fluid.addressed_keys_of` /
`is_order_resolved` / `late_bare_keys_of`, NEVER a bare `getattr`.

**Why.** `docs/architecture.md` records 6 (position settled once, incl. the include-merge
corollary) and 8 (D7).

**Pins.** `tests/test_document_order.py` (the spelling × ordering matrix, the D7 rider matrix, the
rule-level pin that two orderings of one spelling must DISAGREE, and
`::test_a_second_configure_is_not_bound_by_the_first_ones_verdict`), `tests/test_includes.py`
(the ordering group), `tests/test_ordered_merge.py`.

### `configure()` runs THROUGH the document (record 19, 2026-08-17)

**Rule.** `configure(*objs, config=…, **named)` = `dumper.to_markers(objs)` (the objects as a
marker document — the SAME reconstruction rule `dump()` uses, `dumpable_kwargs`) → the config
merged after it (a key naming an object tunes that object's marker IN PLACE — P1's `deep_merge`
— so a bare key beside it still competes on position; other keys append) → `load(until="settled")` (pass 7,
recording into the call's report) → `_apply`: a settled plain value is set (validated under the
init policy; the eager-class note rides the pass-7 record); a settled marker at a slot holding a
MARKER is tuned in place (`kwargs.update`, identity kept, markers inside it stay markers); a
settled marker standing for a live child (`__confluid_live__`, stamped by `to_markers` and kept by
the pass-7 copy) recurses; a marker the config introduced is built (`flow`) or kept deferred; a
mapping at a slot holding an OPAQUE live object is refused (`dict_at_slot_kind`); `solidify()`
fires post-order. There is NO second walker; `tests/test_*_parity.py` and
`test_cross_path_pins.py` are BEHAVIOUR pins of the one path.
Consequences, each pinned: a NAMED object is addressable by a dotted ATTRIBUTE path
(`trainer.model.lr`); a bare list/dict reaches a body-slot marker the document shows; a chain of
INSTANCE names past the first level (`a.b.c.value`) is not a spelling (use the attribute path
from a named object). Warnings for a typo'd block key come from
`broadcast.logger` (the scanner) — patch THAT in a test.

**Rule — ONE read rule on both sides of the document (2026-08-19, BUGS-2026-08-19 CD1/CD2).**
`dumpable_kwargs` (the document's writer) and `_apply` (its reader-back) read a slot the SAME way:
a slot shadowed by a class-level `property` is derived state — the getter NEVER runs (dump,
discovery walk, and apply alike), a setterless slot is never written, and the CAPTURED ctor kwarg
is the document's value (the eager-class fallback); every other slot is read via `getattr`, so a
child living beyond `__dict__` — an `nn.Module` submodule in `_modules`, a `__slots__` slot — is
FOUND and configured in place. Before: the two sides disagreed (`to_markers` used `getattr`,
`_apply` used `__dict__`), so configure() REPLACED an `nn.Module` child with a fresh build,
crashed on a private-backing property host, and `dump()` emitted a getter's derived object that
neither reloaded nor re-applied.

**Rule — B1 binds the named-overlay path, and `configure()` takes `scopes=` (2026-08-19,
CD6/CD18).** A key folded in by a NAMED overlay is the marker's OWN kwarg by the time pass 7
runs, so the accept-list never gated it: a typo was set in silence and `strict_attrs` ignored.
`_apply` now runs `_warn_undeclared` (warn + `unknown-attribute` + still applied; `strict_attrs`
refuses) before every set, under the call's ambient report — the engine-state token spans the
APPLY phase, not just the settle. `configure(*objs, config=…, scopes=[…])` and
`configure_from_file(..., scopes=[…])` thread the activation into the inner
`load(until="settled")`, so scope blocks resolve exactly as under `load()`; a scope WRAPPER key
is structure and never registers as an unused override.
**Pins.** `tests/test_configure_via_document.py`; `tests/test_configurator.py`;
`tests/test_report.py` (the report vocabulary is the scanner's: a class block that delivers a
mapping to a child records at the RECEIVER the block addressed).

**Rule — a config key that NAMES an object is a delivery, and is reported as one (2026-08-18).**
The naming key (`trainer: {…}` / `trainer: !class:Trainer {…}` / `trainer.model.lr`) is folded
into the object's marker BEFORE pass 7 (P1's `deep_merge`, so a bare key beside it still
competes on position), which means pass 7 sees its keys as the marker's OWN and records
nothing — so `configure()` writes the record itself, in the scanner's vocabulary
(`_record_named_overlay`): each key the overlay hands the object that the object accepts is
ONE applied record at `"Class 'name'"`, origin `"addressed"`, and the raw config key (dotted
spellings included) is marked used. The leaf sets this causes on children are NOT records —
exactly as for a class-block delivery. Never record in `_set` "what was actually set": that
is a second vocabulary, and it broke the block pin above.

**Rule — a MARKER at a slot holding a LIVE `@configurable` child of the SAME class TUNES the
child (user ruling 2026-08-18)** — `_apply` recurses into the child (`_tunes_live_child`:
`_resolve_target_callable(marker) is type(current)`), identity kept, exactly what a MAPPING at
that slot does. A marker naming a DIFFERENT class is a request for another object and is
built (a typed slot's validation refuses a wrong class); a marker at an EMPTY slot is built; a
marker at a slot holding a MARKER tunes that marker (unchanged). Do not "generalize" to
subclasses or to markers inside containers without a measured case.
**Pins.** `tests/test_configure_live_child.py` (both rules, every con case).

**Rule — four pass-7 defects the document path exposed are FIXED in pass 7, so `load()` gets them
too (F7/F8/F10/F11 — the CHANGELOG's pass-7 ordering entry):** a same-named child slot (`child:` inside `child:`)
lost its slot in the parent view (`_splice_kwargs_at_slot._parent_wins` now returns False for the
receiver's own key), so a third-level marker's own kwargs were appended after every root key and a
later bare key could not beat them; `tune_marker` was single-level, so `root: {child: {child:
{lr}}}` REPLACED the second-level marker with a dict (it recurses into markers now); a marker
nested in a LIST kwarg had no slot (`holds_marker` + the slot KEY threaded through
`_flow_recursive(slot_key=)` / `_scan_view(self_key=)`), and an instance-name block whose name
equals the marker's attribute key displaced the marker from its slot (the own kwargs unroll at
`self_key` and the block is then processed as the later spec). Also: the C2 verdict is applied
INSIDE pass 7 (`_view_for(slot)` pops the beaten bare keys — and a `'**'` rider's — for the
descent into a block-delivered slot), so the settled document IS the answer for that contest.
**Pins.** `tests/test_document_order.py`, `tests/test_broadcast_scoping.py`,
`tests/test_broadcast_wrapper_override.py::test_inner_overrides_beat_an_EARLIER_glob_cascade` (+
its con case — a LATER rider wins; the old pin passed only because of F10),
`tests/test_names_parity.py`.

### The rule runs ONCE — pass 7 settles, pass 8 builds (record 19, 2026-08-17)

**Rule.** `load()` ends in `engine.materialize()` = `_flow_recursive` (pass 7: the ONE broadcast/reference pass — what
`hydraide` emits) → `instantiate` (pass 8: build every `Target` at ANY depth from its SETTLED
kwargs). Construction does NOT re-run the cascade into a marker's own kwargs: `_flow_target` feeds
`_resolve_kwarg_value` the marker's own glob blocks only. The active context's bare keys are read
at construction for exactly what pass 7 cannot see — a marker born inside the constructor (a body
slot `self.optimizer = PartialClass(...)`, a ctor default) — via `_broadcast_onto_instance` and the
post-init dict-at-slot tune, fed from the document's top-level keys. That is the one delivery the
emitted document cannot show; `load(emit(x))` reproduces it because those keys are IN the document.
Never add a third reader of the context at construction.

**Rule — a reference is settled ONCE, in pass 7 (`engine._settle_reference`).** Nearest enclosing
scope first (an included fragment's internal `!ref:` finds the fragment's key), then the root —
and a scope NEVER answers with the reference itself (`r: {x: !ref:x}` with a root `x` recursed
forever, F6; the self-hit is skipped on both the pass-6 marker-aliasing probe and here). A reference
to a MARKER shares it (identity via `flow_memo`); a reference to a plain VALUE is INLINED — after
`load()` nothing is a late-bound `Reference` any more (F5); a walk that leaves structure is refused;
anything else is an import path; a miss raises a located `ReferenceResolutionError` on
`until="settled"` and `"objects"` alike, so hydraide reports it instead of emitting `_ref_`. The CLI-override
contract is therefore `load(until="document")` → merge into the DOCUMENT → `load(document)` (what the app
framework does), never "materialize a Reference the caller kept". `instantiate` recurses through
plain dicts and lists (F4).
**Pins.** `tests/test_instantiate.py` (the `load(emit(x)) == load(x)` contract, the closed
document, F4/F5/F6 rows, the two must-not-change deliveries),
`tests/test_list_index_refs.py::test_e2e_drone_labels_index_pattern` (the override contract).

**Rule — kwargs on a reference TUNE the shared referent, folded BEFORE pass 7 (user ruling
2026-08-19, "option A"; BUGS-2026-08-19 PA10/BC8/SR9).** `{_ref_: proto, k: 5}`, `!ref:proto`
with a mapping body (the tag constructor keeps it), and a dotted path walking THROUGH a reference
(`a.optimizer.lr: 9.0`) all carry kwargs on the `Reference`; `resolver.fold_reference_kwargs`
moves them into the referent marker's OWN kwargs (`deep_merge`, in place — it is the one shared
object) and clears the reference. `loader._load` runs it ONCE, after
expansion (pass 5) and before interpolation (pass 6): the dotted route has landed by then, and
pass 6's aliasing of a bare top-level reference runs with the kwargs already folded (2026-08-20 —
expansion moved ahead of interpolation, see the Interpolation rules). Consequences, each pinned: the kwargs compete at the REFERENT's position like any own
kwarg — a later bare key still wins — so there is NO second precedence rule; of two references
tuning one referent the later wins per key; a reference to a PLAIN VALUE with kwargs is refused
with its location; a reference without kwargs is untouched; the fold is idempotent
(`load(load(x, until="document")) == load(x)`); hydraide emits the tuned referent and a bare
anchor. Resolution mirrors `_settle_reference` (exact key in the enclosing mapping, else root); a
miss is left for pass 7's located error. Do NOT tune at pass 7 instead — a tune applied when the
reference settles would beat a later bare key regardless of document order.
**Pins.** the reference-kwargs group in `tests/test_ref_identity.py`,
`tests/test_hydraide.py::test_reference_kwargs_are_folded_into_the_referent_in_the_emitted_document`.

### Flat-view ordered matching — the ONE rule, for materialization AND `configure()`

**Rule.** A class's visible context is the document with the descent-path keys popped. Matching
uses the receiving class's accept-list; values apply in document order; last write wins. Receivers
are located in the parent's ambient view by Python identity.

**Detail — scoping.** A BARE top-level key is an implicit `**.key` and cascades tree-wide. An
ADDRESSED key (dotted, nested block, or a marker's own kwargs) applies to the matched node ONLY.
Globs opt back in: `*` = exactly one level, `**` = zero or more. The first named path segment
FLOATS (so deeper same-name nodes rematch); later segments are STRICT one-level hops. Glob-delivered
keys are gated by the NoBroadcast opt-outs like bare keys; exact addressed keys bypass them. A kwarg
on a wrapper block the wrapper does NOT accept shields its subtree from an outer `**` rider.

**Rule.** A `**kwargs` constructor makes the accept-list `None` = accept-EVERYTHING (announced once
per class at TRACE). What such a class's CONSTRUCTOR receives follows the ADDRESSING: a key written
on the marker or delivered by a block naming it is an ARGUMENT; a runtime kwarg is an argument by
construction; a BARE cascading key stays a post-init ATTRIBUTE. Do NOT feed bare keys to the
constructor — every bare key in the document reaches such a class.

**Rule.** `_View` carries the per-key `_KeyScope` tags. `copy()` and `update()` PRESERVE the
side-table; plain-dict syntax (`dict(view)` / `{**view}`) FLATTENS it and is correct only for the
root document — construct a `_View` instead.

**Rule.** A change to the walk is a change to `broadcast._scan_view`. Sinks may NOT grow branches
on keys or scopes — a new rule goes behind a `_Receiver` predicate in `_receiver_for_target`,
with a pin.

**Rule.** Glob keys are routing metadata: they never reach ctor kwargs, post-init setattrs,
`__confluid_kwargs__`, or `load(until="settled")` marker kwargs.

**Detail — configure().** `configure()` runs the document through the SAME scanner (see "`configure()` runs THROUGH the document"). A present
`null` SETS `None`; a typo'd non-dict key inside an object's block warns ONCE from the scanner
(glob-delivered and bare keys never warn).

**Why.** `docs/architecture.md` record 8. **Docs.** `docs/broadcasting.md`, `docs/configure.md`.

**Pins.** `tests/test_broadcast_scoping.py` (incl.
`::test_var_keyword_class_receives_every_bare_key`), `tests/test_broadcast_wrapper_override.py`,
`tests/test_scanner.py`, `tests/test_cross_path_pins.py`, `tests/test_configurator.py` + the four
parity files (`test_basic_parity` / `test_names_parity` / `test_parity` / `test_transforms_parity`);
`examples/broadcasting.py` demonstrates the `**kwargs` split.

### Configuration reports

**Rule — naming.** The marker, the annotation and the YAML key are ONE word: `PartialClass` (the
marker CLASS — `fluid.PartialClass`, the same name in source and at the top level, 2026-08-17) /
`Partial[T]` (the slot ANNOTATION — `partial.Partial`) / `partial_param_names` / `_partial_`. Never
give the class and the alias one name again: two `Partial`s in two modules made every reader of
`fluid.py` meet the wrong one first. Built-vs-deferred is discriminated by `marker.partial`, never
by an isinstance ladder.

**Rule.** `introspect._PARTIAL_CALL_NAMES` is the pinned list of call names that mark a body slot
deferred, and it matches on the NAME IN THE SOURCE — a name missing from it does not raise, the
slot is simply BUILT and a runtime-injection target reaches its constructor without its argument.
Adding a spelling means adding it there in the same change.

**Pins.** `tests/test_partial.py::test_one_marker_type_two_modes` /
`::test_the_body_slot_scan_matches_the_canonical_call_names_only`.

**Rule.** `confluid/report.py` is a dependency LEAF (stdlib + loggair). Only `ConfigurationReport` and
`collect_report` are top-level exports. Every instrumentation site is `if report is not None`-guarded
so the default path stays zero-cost.

**Rule — `strict_attrs=True` closes the surface, and is the ONLY way to.** The default is
permissive and must stay so. A class opting in refuses any addressed key naming nothing it
declares, via the ONE `broadcast.refuse_if_undeclared`, called from the two sites that report an
undeclared key (`engine._warn_undeclared`, `_MergeSink.unknown`). It binds
`configure()` as well as the load path — a mark meaning different things per path is the exact
asymmetry B1 removed — and `register()` carries it for the reason it carries `broadcast=False`:
a class you do not own is the one you cannot close by editing its declaration. A `**kwargs`
target is NEVER refused (no accept-list, so nothing is undeclared for it) and BARE keys never
reach the gate (the accept-list drops them first), which is what keeps a strict class usable in
a document that also configures something else. **Pins.** `tests/test_strict_attrs.py`.

**Rule — an UNDECLARED addressed key is reported on BOTH paths (B1, 2026-08-12).** A key naming
nothing the target declares (not a ctor param, not a settable class attribute, not an
`__init__`-body slot) WARNS and records `"unknown-attribute"` — on the load path as well as under
`configure()`. The own-kwarg form STILL APPLIES the value: `engine._apply_post_init_attrs` IS the
post-init attribute mechanism, so refusing there would delete documented behaviour from the
commonest spelling. Refusing is the opt-in `strict_attrs` mark instead (`TASKS.md`).

**Rule — three exemptions, each pinned.** A `**kwargs` target has no accept-list (`None` =
accept-everything) and is NEVER reported. A BARE key legitimately matches nothing and is `unused`,
never `failed` — reporting it would fire on every sweep document. A DECLARED body slot is silent,
which is what the class-design convention's rule 4 exists for.

**Why.** One typo had three behaviours: `load()` with the key on the marker SET it silently,
`load()` with the key in a class block IGNORED it silently, and only `configure()` reported it.
The load-path sink justified its silence as "constructor validation is this path's typo
enforcement" — measured FALSE: the key is not a ctor param, so `_ctor_params` filters it out and it
reaches a post-init setattr, going AROUND the constructor. `Node(pathh=…)` raises `ValidationError`;
the same key via `load()` did not. Measured impact before shipping: 0 real warnings across 96
workspace configs / 412 markers.

**Pins.** the "undeclared key" group in `tests/test_report.py`, incl.
`::test_the_two_paths_now_report_the_same_failure_for_the_same_typo` and the three exemptions.

**Detail.** `configure()` RETURNS a report; `collect_report()` (in `state`) installs one on the
engine state for the load path. `engine.materialize()`/`active_context()` MUST carry an ambient report into
their fresh states. A nested `configure()` adopts the ambient report; the owner alone logs the
unused summary, at DEBUG. "Failed" is deliberately configure()-path-only — engine-side ctor
validation fires below the engine in the layering and already raises located errors. A LEAF marked
used satisfies its glob-registered spellings (`mark_used("lr")` also marks `**.lr`).

**Rule — the contest ledger.** `AppliedKey.contest` records every source that competed for a key,
in document order, last entry winning. Three invariants:

1. A `Candidate` holds a BOUNDED STRING (`report._short_repr`), never the value — the report
   outlives the pass and a config value may be a dataset or a model.
2. The scanner appends RAW `(origin, value, pos)` tuples; `record_applied` renders them, and only
   when `len > 1`. A single candidate is not a contest and costs a `repr()` per applied key —
   measured as 4.6 ms of the 5.7 ms the ledger first added to a 2,500-marker `configure()` pass.
3. `_MergeSink.apply` records BEFORE its own-kwarg early return. A marker's own kwarg is a
   competitor like any other, and "my kwarg lost to a bare key" is the contest most worth
   explaining; recording after the return would leave exactly that case unexplained.

**Pins.** `tests/test_report.py` (the `explain` group, incl.
`::test_explain_agrees_across_the_load_and_configure_paths` and
`::test_an_uncontested_key_still_explains_itself_and_stores_no_candidates`).
**Docs.** `docs/report.md`, `docs/broadcasting.md` → "What this costs you".

---

## Registry & discovery

### Registry discipline

**Rule.** Only `@configurable` classes and explicitly `register`-ed third-party callables
participate in the config graph. Never traverse into unregistered library internals.

**Rule.** `load_configurables(group="confluid.configurables")` is the entry-point bootstrap:
per-entry error collection (a broken entry logs ONE warning and is returned as the exception
instance), explicit-only — never invoked at confluid import.

**Pins.** `tests/test_load_configurables.py`. **Docs.** `docs/extending-discovery.md`.

### A registered NAME may map to SEVERAL classes

**Rule.** The registry is `_entries: Dict[str, List[_ClassEntry]]` + `_by_key` (canonical
`module.QualName`, `<locals>` stripped, tag-legal). The five reverse indices store ENTRY KEYS,
never names; the public key is derived on READ (bare while unique, dotted once shared).

**Rule.** `list_classes()` returns a key that is ALWAYS a valid `get_class` argument. That contract
is what the whole enumerate-then-look-up idiom rests on; breaking it silently empties pickers.

**Rule.** Lookup narrows by the same five filters `list_classes` intersects. 0 matches → `None`,
1 → the class, N → `AmbiguousClassError` (a sibling of `UnknownClassError`, NOT a subclass — code
catching "unknown" to fall back to an import must not swallow "ambiguous"). `resolve_class` stays
NON-raising by default; `strict=True` is set at exactly the two construction funnels
(`_resolve_target_callable`, `_flow_generic_fluid`).

**Rule — clobber.** Same class object → replace, silent. Same name + all five tags equal +
different object → warning + last-write-wins. Any tag differing → coexist.

**Rule.** `register_class`'s `name` falls back to the class's OWN `__dict__` mark, never an
inherited one (a plain `getattr` registers a subclass under its parent's custom name) — and the
READ side follows the same rule: `get_class(<class object>)` falls back to the own mark after the
identity lookup misses, so an unregistered SUBCLASS answers `None`, never its parent
(2026-08-19, BUGS-2026-08-19 R4). `key_for(cls)` is live-computed, never a second stamp — the
dumper asks it so `dump()` emits a key that reloads THIS class.

**Rule — registration REFUSES what it cannot name or bind (2026-08-19, R1/R2/R3).** A positional
non-callable to `configurable()` (`@configurable("Named")`), a target with no `__name__` and no
`name=` (a `functools.partial`, a callable instance — checked on the ORIGINAL callable, before the
validation wrap, so a function genuinely named `wrapper` is untouched), and a
`staticmethod`/`classmethod` DESCRIPTOR all raise `ConfigurableDefinitionError` naming the fix.
They used to crash with a raw `AttributeError` from an unrelated line, silently register under the
literal name `wrapper` (each clobbering the last), or register a broken target (a wrapped
staticmethod lost its descriptor; a classmethod object is not callable). A provided `name=` always
suffices for a nameless callable — a named partial registers and flows (pinned con). The working
descriptor order is `@staticmethod` ABOVE `@configurable`.

**Rule — enumeration takes SNAPSHOTS (2026-08-19, R9).** `list_classes` /
`list_categories`-family / `_entry_for_object` iterate `list(...)` copies of the backing dicts,
never live views — a concurrent registration during an enumeration (an MCP server listing while a
lazy import registers) raised `RuntimeError: dictionary changed size during iteration`. There is
deliberately NO lock: `list(dict)` copies in one C-level step under the GIL. A NEW enumeration
over a registry index copies first.

**Rule — `load_configurables` catches `Exception`, never `BaseException` (2026-08-19, R10).**
Per-entry tolerance is for a BROKEN entry point; `KeyboardInterrupt`/`SystemExit` propagate, or a
slow bootstrap of many entry points cannot be interrupted.

**Why.** `docs/architecture.md` record 4. **Docs.** `docs/discovery.md`, `docs/errors.md`.
**Pins.** `tests/test_duplicate_names.py`.

### Config-side disambiguation — the `@axis=value` selector

**Rule.** A `!class:`/`!lazy:` target may carry `@axis=value[,axis=value]`, parsed by the ONE
`registry.parse_target_spec` and restricted to the five `SELECTOR_AXES`. An unknown axis raises
`ConfigurationError` rather than selecting nothing.

**Detail.** The document-key form is the unbraced `$key` (dotted paths allowed), resolved at FLOW
time against the active context — `${...}` is impossible in a tag suffix because `{` is not a legal
YAML tag character. The `Target(...)` grammar is ONE `resolver._TARGET_CALL_RE` serving all four
spellings; value coercion stays per-caller policy. `_parse_scope_suffix` keeps its own pattern (a
scope key is an identifier, not a class target).

**Rule.** Do NOT build scope-aware resolution: the active scope map is a local in `load()` that
`_EngineState` never carries, and a scope block declaring its own dimension as a plain key already
makes every `$` selector follow the verb.

### Discovery taxonomy

**Rule.** Tag with `@configurable(task=…, role=…)` in preference to a hand-concatenated
`category` — confluid derives `category = f"{task}_{role}"` and indexes all three. The derivation
lives in `register_class`, the ONE stamping authority, and fires whenever the taxonomy is
(re)stated: restating `role=` alone re-derives from the effective task+role (a subclass turning a
loss into a metric leaves the loss picker — R5), and a direct `register_class(task=…, role=…)`
derives exactly like the decorator (R6). An explicit `category=` argument always wins; a call
restating neither half keeps the existing mark. Consequence: re-registering with `role=`/`task=`
and no `category=` REPLACES a hand-set category from an earlier call — derived-never-typed is the
rule. `framework=` is
the THIRD orthogonal axis: which ENGINE's API a class belongs to (the API, not the tensor runtime),
deliberately NOT folded into `category`. Untagged classes are ABSENT from an index, so a filter
returns only what claims that value.

**Rule.** `role` / `framework` are stated EXPLICITLY, never inferred from the class type — model
and loss are both `nn.Module`, registered builder FUNCTIONS have no MRO, and framework-native
runnables inherit no marker base. A consumer MAY infer as a fallback for untagged classes; an
explicit tag always wins.

**Detail — canonical category strings** (each tagged repo pins them in its `tests/test_categories.py`):

| Bucket | Values |
|---|---|
| Models (task-scoped) | `classification_model`, `segmentation_model`, `detection_model` (incl. registered builder FUNCTIONS) |
| Losses (task-scoped) | `classification_loss` (off-the-shelf `nn.Module` losses tagged via `register`). There is no `detection_loss` — native detectors compute loss inside `forward`. |
| Datasets / sources / engines (split by ROLE) | `<task>_dataset` for task-specific datasets; `source` for generic task-agnostic sources that yield Records; `engine` for composition primitives that compose sources + ops |
| Ops | `op` = concrete `Record → Record` ops |

Generic single-word buckets (`trainer`, `metric`, `sink`) remain available where no task
distinction exists. Roles in use: `model` / `loss` / `dataset` / `metric` / `trainer` / `evaluator`.
Categories are task-scoped wherever a task distinction constrains compatibility — a classification
trainer's `model` slot must never offer a segmentation model. A visual editor uses a POSITIVE
allowlist `category ∈ {op, source, engine}`, so a callable becomes a canvas node only once tagged. A
dropped or renamed tag silently empties the corresponding picker.

**Rule.** `group=` is presentation-only (palette nesting within a category) and does NOT gate what a
consumer is offered.

**Pins.** `tests/test_task_role.py` (incl. the R5/R6 derivation group), `tests/test_group.py`,
`tests/test_registry.py` (the refusal + snapshot groups), `tests/test_duplicate_names.py` (R4),
`tests/test_load_configurables.py` (R10). **Docs.** `docs/discovery.md`.

### Behavioral marks

**Rule.** `registry.register_class` is the ONE stamping authority for every `__confluid_*__` mark.
`@configurable` DELEGATES its whole mark set there — never reintroduce a second stamping block in
`decorators.py`. Each mark follows the fallback template (argument wins, else the existing mark
survives), so a partial re-register never drops tags.

**Rule.** `confluid.marks(target)` is the ONE public read surface for the stamps. Consumer code
reads `marks(cls).role`, never `getattr(cls, "__confluid_role__", …)` — the dunder names are
INTERNAL. In-repo stamp pins may keep raw reads (they pin the mechanism).

| Mark | Meaning |
|---|---|
| `lazy=True` | the constructed value should stay DEFERRED (a runtime-injection slot) |
| `random=True` | non-deterministic output — a node must re-execute every run |
| `constant=True` | a PURE value producer; foldable into an exported document. Mutually exclusive with `random` (raises at decoration time). Currently ZERO producers; the consuming machinery remains. |
| `eager=True` | a plain constructor doing real work from its params; read by `configure()`'s staleness warning |
| `broadcast=False` | no bare or glob-delivered key ever lands; addressed blocks and `configure()` still work |
| `capture=False` | skips ctor-kwargs capture on BOTH stamp paths, for heavy/disposable ctor args. Costs dump fidelity for transformed params. |
| `broadcast_attrs=[...]` | declares post-init body-slot names, UNIONED with the AST scan — never a replacement. `[]` declares "none" and silences the cannot-scan warning. |
| `strict_attrs=True` | a key the class declares NOWHERE is REFUSED, not absorbed as a post-init attribute. Opt-in; binds the load path AND `configure()`; `register()` carries it too |
| `strict_typing=True` / `display_name=…` | presentation hints for a GUI |

**Rule — packaged mode.** Body slots are found by AST-scanning `__init__` SOURCE, which is absent in
compiled/frozen/zip deployments. The PRIMARY mechanism is the build-time bake
(`confluid-bake <package>` / `python -m confluid.bake`, `confluid/bake.py` → `<pkg>/_confluid_baked.py`), consulted per MRO class only when the live
scan finds nothing, so the effective set is `scan ∪ declared ∪ baked` and fresh source always
governs dev. **KEPT by user ruling 2026-08-09** despite no consumer baking yet — do not re-flag it
as dead surface; wire `confluid-bake --check` into the first frozen consumer's CI when one lands.
The engine warns ONCE per class per process when it can scan nothing and nothing is declared (the
warned-set is deliberately NOT cleared per pass).

**Pins.** `tests/test_task_role.py` (incl.
`::test_marks_is_the_one_public_read_surface_for_the_stamps`), `tests/test_no_broadcast.py`,
`tests/test_broadcast_attrs.py`, `tests/test_bake.py`, `tests/test_eager.py`.

---

## Scopes

**Rule.** A scope block is `!scope:KEY[=VAL]` / `!notscope:…` (tag, `KEY(VAL)` ≡ `KEY=VAL`) or
`_scope_:` / `_notscope_:` taking a MAPPING of dimension → value (reserved key), activated via the
`scopes=` kwarg on `load()`. Bare dict keys are NEVER treated as scopes. `!notscope:` uses the
unset-⇒-active convention.

**Rule.** `ScopeBlock.dims` is a `Dict[str, Optional[str]]` — the block is active only when ALL of
them match (an AND). `None` is a boolean dimension. Never re-add the single `key`/`value` pair: the
mapping is what lets one block replace a block nested inside another.

**Rule.** The WRAPPER KEY is inert and MUST stay so. It cannot become the dimension name: several
blocks routinely share a dimension (`lightning:`/`torch:`/`keras:` all on `framework`) and YAML
forbids duplicate keys in one mapping.

**Rule — the splice IS the include-paste rule, through `merger.deep_merge`, per key (2026-08-19).**
`scopes._splice_key` is the ONE way a key lands in a resolved mapping — a block's contents AND a
plain key written after a block both go through it: a mapping over a marker TUNES it (a copy),
a nested block deep-merges, anything else replaces, and a restated key is RE-ANCHORED at the
later writer's position. Never go back to `out[k] = v`: that deleted a marker a block re-stated
with a mapping, lost a nested block's other keys, and kept the EARLIER writer's position so a
later bare key won or lost depending on the activation (BUGS-2026-08-19 SR1/SR2/PA2/PA3). The ONE
special key is `include:` — two active blocks each carrying one COMBINE into a list (the directive
accepts one), so both files are read. The sibling arm in `deep_merge` merges two `ScopeBlock`s
under one key with IDENTICAL `dims` + `negate` (the same wrapper on both sides of an include —
PA4); a different condition is a different block and still replaces.
**Pins.** the splice group in `tests/test_scopes.py` (`::test_a_block_mapping_at_a_marker_slot_TUNES_the_marker`
… `::test_two_active_blocks_each_carrying_an_include_read_BOTH_files`, incl. the scalar-replace and
marker-replace con cases), `tests/test_merger.py::test_two_scope_blocks_with_the_SAME_dims_merge_their_contents`
(+ the DIFFERENT-dims con), `tests/test_includes.py::test_the_same_wrapper_key_in_an_included_and_the_including_file_merges`.

**Rule — a LIST whose FIRST item is a `_scope_` mapping IS a scope block**, the remaining items its
body. That is the ONLY way to write a conditional list ITEM, because a YAML node is a mapping or a
sequence and never both. Registered as a DEFAULT SEQUENCE tag constructor that tests the first
node's keys, so an ordinary list keeps PyYAML's own path. Do NOT add a body key (a `_content_`-style
reserved key): it would need a second body-shape rule AND a scalar special case, both of which this
shape avoids (a scalar body is a one-item list, and `_resolve_list` already extends).

**Rule.** A dimension VALUE that YAML reads as a boolean is REJECTED with a quote-it message.
`{extra: yes}` becomes `True` and then never matches the `extra=yes` string an activation carries —
silently never firing. `{debug: }` is the boolean DIMENSION spelling.

**Rule — `include:` is honoured in EVERY position, and scopes+includes SETTLE by alternating.**
A marker's kwargs and a scope block's contents used to be walked VALUE-wise by
`_process_includes_recursive`, so their own `include:` key never reached `_splice_includes`:
inside a marker it became a constructor kwarg literally named `include`, inside a scope block it
leaked into the config as literal data (P15). `Fluid.kwargs` now goes through the DICT branch (a
marker is unconditional — always part of the document — so its include is spliced in the normal
pass). A `ScopeBlock`'s own include deliberately is NOT: processing it before activation would
open a file the block may be about to discard, and a framework overlay must not be mandatory in a
checkout that never activates that framework. `load()` therefore calls
`_settle_scopes_and_includes`, which alternates `resolve_scopes` → `_process_includes_recursive`
until a pass splices nothing. It must REPEAT — an activated block can splice a file carrying its
own scope block carrying its own include — and it is capped at `_MAX_SETTLE_PASSES` because two
files including each other from inside scope blocks expose one another's directive on every pass
(the per-splice `_included` set cannot see across passes). Cost is ONE extra document walk per
load: measured 0.11 ms against a 9–23 ms load of a 27 KB config. The two malformed spellings
raise instead of degrading — `include: {path: …}` consumed the key and spliced NOTHING, and a
non-string list entry was skipped among its siblings.
**Pins.** the P15 group in `tests/test_includes.py`, incl.
`::test_include_inside_an_INACTIVE_scope_block_is_never_opened` (the property the alternation
exists for), `::test_an_included_file_may_itself_carry_a_scoped_include` (why once is not enough)
and `::test_a_scoped_include_cycle_is_bounded`.

**Rule.** There are THREE walkers that must agree on which nodes can carry a block —
`scopes._walk_dimensions`, `scopes._resolve_value`, and `loader._process_includes_recursive` — over
dict, list, `ScopeBlock.contents`, and **`Fluid.kwargs`**. Adding a node kind to one means adding it
to all three.

**Why.** The asymmetry was a real bug: dimensions were advertised from `Fluid.kwargs` while
resolution ignored them, so a CLI consumed `--model convnet` and silently trained the default.

**Rule.** A block's BODY may be a mapping (keys spliced at the wrapper's slot), a sequence (EXTENDS
the surrounding list — the only way to write a conditional list ITEM), or a scalar (substitutes one
entry). An EMPTY body yields `{}` — "declares nothing" is a real state. A sequence/scalar body at a
MAPPING slot raises a located `ScopeError`, but ONLY when the block is ACTIVE (an inactive block is
dropped without its contents being examined).

**Rule.** An ACTIVE keyed scope MUST name a value the document declares, else `ScopeError` —
and a BARE activation of a dimension declared with keyed values alone is refused the same way,
naming the values and their lines (2026-08-19, SR13: it selected nothing AND suppressed the
`default_scopes:` value, so the run silently used neither). Three
exemptions are load-bearing: an UNDECLARED dimension is an inert no-op; an UNSET dimension resolves
to defaults; a dimension carrying ANY `!notscope:` block accepts EVERY value. Consequently
`discover_dimension_values` reports POSITIVE values only and maps a negation-only dimension to an
EMPTY set — do NOT "fix" that.

**Rule.** `discover_dimension_values` takes the RAW document, never a `load()` result (whose blocks
are already spliced away).

**Rule — `default_scopes:` is a STATIC, KEYED-ONLY load input (2026-08-18).** A top-level
`default_scopes: [dim=value, …]` names the value a keyed dimension takes when the caller's
`scopes=` carries no entry for it; `normalize_active(scopes, aliases, defaults)` fills it in per
dimension AFTER the caller's list (a caller value always wins), and the loader reads it beside
`scope_aliases:` at pass 4 through `scopes.default_scopes(raw)` — the ONE reader, public (a CLI shows
the default beside `discover_dimension_values`), validated by `parse_default_scopes`;
`scopes.METADATA_KEYS` is the ONE strip list).
Three properties are load-bearing: it goes through `_check_active_values_are_declared` unchanged
(a typo'd default raises like a typo'd flag — never add a bypass); a bare name (a boolean scope,
an alias) is REFUSED, because nothing the caller passes could switch such a default off, so it
would be always-on — `!notscope:` is the spelling for "active while unset"; and it is never
interpolated (a `${a.b}` may read a key an active block provides, so activation must settle
first — do NOT move the read past the scope pass). Do not add a second reader of the key beside
`default_scopes()`.
**Pins.** the `default_scopes` group in `tests/test_scopes.py` (fires when unset, caller wins,
per-dimension fill, undeclared/boolean/alias/malformed refused, not interpolated, deactivates a
`!notscope:`, intact at `until="raw"`, hydraide emits the defaulted variant).

**Why.** `docs/architecture.md` record 1. **Docs.** `docs/scopes.md`.
**Pins.** `tests/test_scopes.py` — the "active value must be declared" and "body shapes" groups,
plus `::test_keyed_scope_inside_a_markers_own_kwargs_swaps_the_slot` and the four
`*_inside_a_markers_kwargs_*` cases. **Example.** `examples/scopes.py`.

---

## Interpolation

**Rule.** `${...}` dispatches on the NAME SHAPE: a plain identifier is an environment variable; a
dotted/bracketed name is a config-key path resolved against the config tree (local context first,
then global). That test is what keeps every pre-existing `${VAR}` an env var.

**Rule.** `$$` is a literal `$` (2026-08-20, CD10): it suppresses every interpolation spelling
(`$$NAME` → `$NAME` even when set, `$${a.b}` → the literal text, `$$5` → `$5`), collapses in
mapping keys too, and is what `dump()` emits — the round-trip escape. Each side of a `$$` split
interpolates independently with embedded semantics.

**Rule.** Bare `$IDENTIFIER` expands as an ENV-ONLY read after the `${...}` pass — no dotted form,
no `:default`, no exemptions (a tag TARGET's `@axis=$key` selector lives on the marker, never in a
string value, so nothing needs a leading-`!` escape) — and on AUTHOR text only: text a `${...}`
substitution just produced is never re-scanned (2026-08-20, BUGS-2026-08-13 P8 — an env value
carrying `$HOME` used to expand it, an injection channel).

**Rule — interpolation is pass 6, AFTER expansion (2026-08-20; BUGS-2026-08-19 PA6/PA7/PA8 +
BUGS-2026-08-13 P8/P9).** A `${train.lr}` reads the EXPANDED tree — the same answer the returned
document gives (a dotted line and its nested twin merge FIRST, document order, last wins). The
pieces, each pinned in `tests/test_resolver.py` / `tests/test_load_stages.py`: (a) a whole-string
container hit resolves before it is handed back (`c: ${a.b}` no longer receives the raw subtree,
placeholders and all) and a container reaching itself through the placeholder is a refused cycle;
(b) an env variable SET to the empty string is `""` in every spelling — only UNSET is a miss;
(c) a marker's kwargs are walked once per `Resolver` (instance-level seen set) — an anchored
marker aliased at two slots is no longer interpolated twice; (d) whole-string `${ref:...}`
STRINGS hoist to `Reference` markers BEFORE expansion (`resolver.hoist_marker_placeholders`),
so a dotted write through the placeholder spelling tunes the shared referent exactly as the
`!ref:` tag does; (e) a dotted write through any OTHER unresolved `${...}` string is refused in
`merger.expand_dotted_mapping` — value substitution is not sharing, `${ref:}` is; (f) a dotted
write's VALUE interpolates where it LANDS, so a competing path in the landing block wins there
(the same answer a literal key written inside the block gets). Do not reorder the passes back,
and do not add a second interpolation pass — single-pass is the contract the P8/P9 fixes rest on.

**Rule.** Interpolation is a SINGLE PASS and the substituted value BURNS IN — `dump()` emits it and
a deferred slot flowed later sees it. Marker kwargs are IN the pass (walked in place, identity
preserved, text-only). A slot that must stay late-bound uses `!ref:`, not `${...}`.

**Rule.** Bare `{key.path}` was deliberately NOT adopted — it collides with per-record templates and
checkpoint filenames that pass through configs as literals.

**Why.** `docs/architecture.md` record 7. **Docs.** `docs/interpolation.md`.
**Pins.** `tests/test_resolver.py` — the `${key.path}` block plus the bare-env group
(`::test_bare_env_var_expands_embedded_in_a_path` / `::test_bare_env_var_unset_stays_literal` /
`::test_bare_env_pass_expands_in_any_ordinary_string` /
`::test_braced_default_behavior_unchanged_by_bare_pass` /
`::test_bare_env_var_expands_in_marker_kwargs_walk`); the marker-kwargs group in
`tests/test_loader.py` + `::test_config_key_interpolation_end_to_end`.

---

## The I/O contract and the marker family

**Rule — outputs.** Mark a read-only `@property` getter with `@output`, applied UNDER `@property`
so it stamps the getter (`fget`). `output_specs(cls)` walks the MRO (subclass override wins). An
`@output` property is already excluded from `to_pydantic` — it never becomes a config knob.
(`readonly_config` was DELETED: its mark had zero readers. Do not reintroduce it without wiring the
mark into the accept-lists.)

**Rule — inputs.** `Mandatory[T]` flags a slot mandatory EVEN WHEN defaulted for zero-arg build.
`input_specs.required` is `no-default OR is_mandatory_annotation`.

**Rule — the three aliases.** `Partial[T]` and `Mandatory[T]` are `Annotated[Union[T, Fluid], marker]`
— subscript the flow-target INTERFACE; the `Fluid` arm is what lets a `Class(...)` default
type-check under strict mypy. `NoBroadcast[T]` deliberately has NO `Fluid` arm: it gates
generically-named SCALAR knobs, where a Fluid arm would misdescribe the value.

**Rule.** Marker detection is the RECURSIVE `introspect.annotation_has_marker` (walks `Annotated`
payloads + `Union` arms, so composed spellings and `Optional[Partial[T]]` are detected; deliberately
does NOT recurse into other generics — `List[Partial[T]]` marks the ELEMENT). All three names come
from the ONE `introspect.marked_param_names`. All three markers are STRIPPED by `to_pydantic`.

**Rule.** `NoBroadcast` is enforced at the cascade gates via `broadcast._broadcast_blocked_keys` —
NEVER by removing the param from `_get_acceptable_keys`, which also gates addressed blocks. The
delivery kind is the closed `broadcast._Delivery` Literal (`addressed`/`glob_one`/`rider`); a
`'**'`-delivered MAPPING is gated by the SLOT param's shield, a `'**'`-delivered SCALAR by the
TARGET param's — each shield gates deliveries at its OWN key.

**Pins.** `tests/test_io_contract.py`, `tests/test_no_broadcast.py`. **Docs.** `docs/io-contract.md`.

---

## Schema export (`to_pydantic`)

**Rule.** `to_pydantic` preserves code-side tightening: an `Annotated[int, Field(gt=0)]` param keeps
its constraints in the generated schema. A param typed with an un-JSON-schemable leaf
(`torch.Tensor`, numpy arrays) — or a PARAMETERIZED generic whose ORIGIN is one — is coerced to
`Any` so schema generation never crashes.

**Rule — the mirror is built for EVERY legal signature, and it JSON-schemas (2026-08-19,
BUGS-2026-08-19 N1/N2/N3/N9/N10/N11).** Three leaf rules in `_convert_annotation_unwrapped`: a
bare `collections.abc.Callable` is `Any` like `typing.Callable`; a non-`@runtime_checkable`
`Protocol` is `Any` (pydantic's is-instance schema raised a raw `SchemaError` from the constructor
of every class typing a param with one); any other plain leaf class pydantic cannot JSON-schema
(decided by the cached probe `_json_schemable`) rides as `Annotated[T, WithJsonSchema({})]` —
the isinstance check is KEPT, only the schema is opaque. Two name rules in `_field_name`: a
leading-underscore name and a `BaseModel`-attribute name (`model_config`, `schema`, `copy` …) get
a mangled field name (`seed_`, `model_config_`) with the real name as the field ALIAS — never
dropped (the alias is what `model_validate(kwargs)` and the JSON schema speak); `field_name_for`
is the ONE reverse map and `validation.validate_setattr` / the mixed-kwargs path go through it.
`_spread_range_marks_into_container` looks through `Optional`/`Union` and relocates into each
container arm. Do NOT "fix" a new un-schemable type by widening `_OPAQUE_TOP_MODULES`: the probe
answers for any leaf; the allow-list exists only to keep torch/numpy leaves `Any` (pinned).
**Pins.** `tests/test_pydantic_export.py` — the N1/N2/N3/N9/N10/N11 group (incl. the
runtime-checkable-Protocol-keeps-its-check and isinstance-kept con cases).

**Rule — container range marks.** The workspace convention puts `annotated_types` marks on the
OUTER annotation of a `(min, max)` container param, because that is where a GUI reads widget bounds.
Pydantic applies such constraints to the field VALUE and raises, so
`_spread_range_marks_into_container` relocates them onto the container's numeric elements (Ellipsis
skipped, non-range metadata untouched). Scalar marks pass through verbatim.

**Rule — `_ITER_TYPES_AS_ANY` holds the LAZILY-VALIDATED kinds ONLY.** Coerce `Iterable` /
`Iterator` / `Generator` and their async twins, because pydantic wraps those in a one-shot
`ValidatorIterator` — a slot read twice silently sees an empty collection. Do NOT "complete the
set" with `Sequence` / `Mapping` / `Collection` / `Container`: those validate into a real
`list`/`dict` holding the IDENTICAL objects, so coercing them buys nothing and costs the element
type. The test to apply when adding a type: does its CONTRACT permit a generator?

**Rule — `sanitize_schema`.** The LLM-safe downgrade (`llm_schema.py`): inline `$ref`/`$defs`
(cycles truncated), flatten `allOf`, collapse nullable `anyOf` → `T` + `nullable`, `const`→`enum`,
strip unsupported keywords, drop non-allow-list `format`s, ensure every object/array declares a
`type`/`items`. It is PURE (no input mutation, stdlib only) and recurses ONLY subschema positions —
a `default` payload that is itself a dict is copied verbatim. It rewrites only the ADVERTISED
schema, never server-side validation.

**Why.** Full JSON Schema is fine for one vendor's tool API and uncallable for another's
OpenAPI-3.0 subset. It lives in confluid because confluid already owns AI-facing schema
introspection.

**Pins.** `tests/test_pydantic_export.py` — the range-mark trio
(`::test_to_pydantic_container_range_mark_relocates_to_elements` /
`::test_to_pydantic_variadic_tuple_range_mark_skips_ellipsis` /
`::test_to_pydantic_scalar_range_mark_validates_and_bounds_schema`), the abstract-collection group
(`::test_a_sequence_slot_keeps_its_element_type` /
`::test_a_mapping_slot_keeps_its_key_and_value_types` /
`::test_an_iterable_slot_is_still_coerced_to_any` /
`::test_a_validated_sequence_is_re_iterable_while_an_iterable_is_not`, the last pinning the
pydantic property the split rests on), and
`::test_convert_annotation_coerces_parameterized_opaque_generic_to_any`;
`tests/test_llm_schema.py`. **Docs.** `docs/schema-export.md`.

---

## Serialization

**Rule.** `dump()` followed by `load()` MUST reconstruct an identical object graph. Round-trip
fidelity is non-negotiable. A literal `$` is emitted as `$$` — the loader's escape — so
interpolation-active text (`echo $RUN_USER`) reloads verbatim instead of substituted (2026-08-20,
CD10); the escape is uniform over keys and values (a representer cannot tell them apart), and the
loader collapses `$$` in mapping keys during the interpolation pass.

**Rule — a target's dumpable name comes from the REGISTRY, for a marker as well as a live
instance.** `dumper._target_name` asked neither, emitting a raw `module.qualname`, and both
targets whose qualname is not importable broke (F3): a FACTORY-built class kept the `<locals>`
the registry strips, and a registered FUNCTION is not a `type` so it fell through to `str()` and
emitted `<function build at 0x108420fe0>` — a memory address, which makes two dumps of the SAME
object differ as well as failing to reload. Ask `registry.key_for()` first; fall back to the
dotted path for an unregistered target (still importable), and pass a STRING target through
verbatim (it is already the document's spelling). BOTH naming branches go through
`_target_name` — the `__confluid_class__` branch carried an inline copy and emitted a memory
address for a registered FUNCTION target (2026-08-19, BUGS-2026-08-19 CD12).

**Rule — opaque VALUES dump by value where a faithful spelling exists (2026-08-19, CD9/CD11).**
`_represent_opaque` spells a `PathLike` as its string, an `Enum` member as its value, and a numpy
SCALAR as `.item()` — each reloads through the constructor that took it; the bare
`{_target_: X}` placeholder reloaded DEFAULT-constructed, silently (a sink pointing at `'.'`).
Any other opaque keeps the placeholder and WARNS once per type. A `**kwargs` class's captured
extras are emitted as marker kwargs (the declared-slot projection cannot see them; the presence
test reads the UNfiltered `slots()` — `var_keyword` is not a `_DUMP_KINDS` member). And
`configure()` SKIPS a settled kwarg that equals the capture when the attribute does not exist on
the object (CD8) — the capture is the dump fallback, not a config change; re-setting it planted
new attributes and fired the eager staleness warning for keys the config never mentioned.
**Pins.** the F3 group in `tests/test_dumper.py`, incl.
`::test_a_marker_targeting_a_registered_FUNCTION_reloads` (which asserts no `0x` in the document)
and `::test_a_marker_and_a_live_instance_name_the_same_class_alike`.

**Rule — BODY SLOTS are dumped, and every value is dumped ALWAYS.** `dump()` modelled the
constructor alone, so an `__init__`-body attribute vanished from the document — `self.epochs = 1`
configured to 50 dumped as bare `_target_: BodyHost` and reloaded as 1 (F2). Every OTHER surface
treats a body slot as first-class (`configure()` sets it, `to_pydantic` types it, the accept-list
admits it), so the omission contradicted the round-trip rule above. `dumper._DUMP_KINDS` therefore
carries `body_slot`. Never "optimize" the dumper to emit only what differs from a default: a value
is dumped even when it equals its default (as ctor params always were), because that is what makes
a dumped document self-contained — otherwise editing a default in the source later would silently
change what every existing dump reloads to. `__confluid_extra__` stays and is complementary: this
projection covers DECLARED slots, that list covers names nothing declares.
**Pins.** the body-slot group in `tests/test_dumper.py`, incl.
`::test_a_body_slot_is_dumped_even_when_it_equals_its_default` and
`::test_a_setterless_property_is_still_not_dumped` (derived state recomputes; it stays out).

**Detail.** Two mechanisms serve it: the dumper reconstructs kwargs per param (live same-named
attribute preferred, CAPTURED ctor kwargs as the fallback for eager classes that transform params),
and a `None` value is omitted ONLY when the param's default is also `None` — any other default dumps
`param: null`, or reload silently restores the default.

**Pins.** `tests/test_eager.py`, `tests/test_dumper.py`. **Docs.** `docs/serialization.md`.

---

## Public surface, errors, typing

### Curated top-level surface

**Rule.** `confluid/__init__.py` re-exports ONLY the consumer-facing API. Internal machinery stays
importable from its home module but is NOT top-level: validation plumbing (`confluid.validation`),
scope resolution (`confluid.scopes` — only `discover_dimensions` / `discover_dimension_values` /
`default_scopes` are public), annotation predicates (`confluid.partial` / `confluid.mandatory` / `confluid.pydantic_export`),
marker internals (`ScopeBlock` → `confluid.fluid`), and `load_workspace_env` (`confluid.env`).

**Rule.** Before adding a name to `__all__`, ask which consumer reads it. Do not re-grow the surface
by accretion.

**Rule — the settability predicates.** External front-ends MUST call `accepts_key` (accept-list only
— ADDRESSED writes), `accepts_broadcast` (accept-list MINUS the opt-outs — BARE keys),
`accepts_any_key` (does this target discriminate between keys at all?) and `declares_key` (does it
NAME this key — the `**kwargs` catchall never counts) rather than reading the marks or accept-lists
themselves. A NEW gate belongs beside them, never in a downstream copy.

**Why.** A CLI re-derived the same answer and diverged on `**kwargs` targets, body slots and both
opt-outs — a bare `--run_name` reached a metric's constructor from a call site nowhere near the
config, and reached a dataset loader where nothing raised at all.

**Rule.** Zero-downstream-user names that stay public BY DESIGN: the typed exception hierarchy,
`configure`/`configure_from_file`, the `ValidationPolicy` knobs, `format_yaml_loc`, and
`InputSpec`/`OutputSpec`. See `docs/architecture.md` record 10 (the census ruling) before opening
another dead-surface audit.

**Pins.** `tests/test_no_broadcast.py::test_accepts_any_key_separates_declaring_from_being_unable_to_refuse`
/ `::test_accepts_any_key_matches_what_the_constructor_actually_receives` (the drift pin against the
engine's real behaviour), `tests/test_validation.py::test_confluid_validation_exports_available`,
`tests/test_env.py::test_not_reexported_from_top_level`. **Docs.** `docs/api-index.md`,
`docs/broadcasting.md` → "Asking whether a key may land".

### Typed exceptions

**Rule.** Every error confluid raises MUST come from `confluid/exceptions.py` (root
`ConfluidError`), never a bare builtin, and each concrete class DUAL-INHERITS the builtin it
replaces — a new class outside that hierarchy is a silent breaking change for `except ValueError:`
call sites. New config-content errors subclass `ConfigurationError` and are exported top-level.

**Detail.** Deliberate builtin survivors: the PEP-562 `__getattr__` `AttributeError`, the
optional-dependency `ImportError`s (pydantic, dotenv), and `flow()`'s constructor-failure wrapper,
which re-raises `type(exc)(msg)` to PRESERVE the original class — only its can't-rebuild fallback
is `ConstructionError`.

**Rule — EVERY error raised while processing a document NAMES ITS `file:line:col`** (user
instruction 2026-08-11, asked repeatedly). A config error without a location is not a usable
error: the user gets a 30-frame traceback through `engine.py` and still has to grep 76 YAML files
for the offending string. The marker carries `_yaml_loc`, `format_yaml_loc(fluid)` renders it, and
`ConstructionError` already does this (`Failed to construct RFUAVSource at
.../evaluate_yolo26.yaml:20:5`) — so the machinery is not the obstacle, DROPPING the marker on the
way to the raise is.

**Consequence for helpers.** A helper that raises about a node MUST receive the NODE, never just
the string pulled off it. `_resolve_target_callable(obj.target)` is the live counterexample: it
takes the target STRING, so `UnknownClassError: Cannot resolve class:
waivefront.sources.HDF5WindwoSource` names no file and no line, while the marker holding the
location sits one frame up in `_flow_target`. When you add or touch such a helper, pass the marker
(or the loc) and render it into the message.

**Sweep 2026-08-19 (BUGS-2026-08-19 SR14/SR15/ENG-4/ENG-10/PA25).** Five raises had the marker or
the file in hand and dropped it; all are located now and pinned by message: a `@axis=$key`
selector miss re-locates through `_resolve_target_callable`'s `except ConfigurationError` (the
same funnel that re-locates `AmbiguousClassError`); the undeclared-scope-value `ScopeError` lists
where the values are DECLARED (`scopes._DECLARATION_LOCS`, filled by the one `_walk_dimensions`
walk); a malformed `default_scopes:` leads with its file (`parse_default_scopes(where=…)`);
`flow()` of an unresolvable `Reference` names its line; a validating `__setattr__`'s `TypeError`
joins the `AttributeError` wrap at BOTH post-init setattr sites; an include miss names the
INCLUDING file and the search tiers actually probed (base_dir threaded into `_load_config_file`),
and `CircularIncludeError` renders the whole chain (`_included` is an insertion-ordered
`Dict[Path, None]` — the chain IS the store).

**Pins.** `tests/test_exceptions.py` (incl. the located-error sweep group). **Docs.** `docs/errors.md`.

### Dependencies

**Rule.** Confluid's runtime dependencies are `pyyaml`, `loggair`, `typing-extensions` — nothing
else. Pydantic (`confluid[pydantic]`), python-dotenv (`confluid[env]`) and Click (`confluid[cli]`,
the `hydraide` command only) are OPTIONAL extras, imported lazily with an `ImportError` naming
the extra. A new hard dependency needs a reason that
holds for a consumer who uses only the configuration engine.

### Type safety

**Rule.** Strict mypy (`disallow_untyped_defs=true`); all public APIs fully annotated. Run mypy from
the WORKSPACE ROOT (`cd <workspace> && mypy confluid`), never from this subdirectory.

**Rule.** `Any` only where naming the real type is genuinely impractical, with a comment saying why.

---

## Logging

**Rule.** Every module logs via `from loggair import get_logger`. Stdlib `logging` is PROHIBITED.

**Rule.** loggair (loguru) uses brace/`str.format` interpolation — `%`-style printf args are
SILENTLY DROPPED. Always f-string the message.

**Rule.** loggair does not propagate into stdlib logging, so pytest's `caplog` CANNOT capture
confluid logs. Tests assert log output by monkeypatching the module `logger` with a
`SimpleNamespace` collector; a negative assertion checks the collected list, never `caplog.records`
(an empty caplog is a FALSE green).

**Rule.** Broadcast diagnostics log at TRACE (`broadcast: <key> -> <Class> (<origin>)`); enable with
`LOGGAIR_CONSOLE_LEVEL=TRACE`. Per the workspace log-level mandate, per-key normal-operation events
are TRACE/DEBUG fodder — never `warning`/`info`.

**Rule — per-key sites are GATED.** A diagnostic emitted once per KEY MUST be wrapped in
`if _trace_on:` (inside `broadcast`) or `if trace_enabled(logger):` (elsewhere). The gate is
recomputed by `clear_pass_caches()` from `loggair.get_active_config()`, and a swapped-in logger is
never gated so log-asserting tests keep working.

**Why.** Python evaluates the f-string before the logger can filter, and loggair's handlers sit at
TRACE with a filter, so loguru's own min-level fast path never fires. Measured 2026-08-11: 10,000
discarded TRACE records per 2,500-marker materialize, 30 % of the pass.

**Pins.** the log-gate group in `tests/test_broadcast_scoping.py`.

---

## Testing & validation

**Rule.** Every new feature includes a test that dumps and reloads the configured object graph.

**Rule.** Registry isolation is automatic: an autouse fixture SNAPSHOTS the registry's backing
indices before every test and RESTORES them after — snapshot/restore, not `clear()`, so
module-level registrations survive while anything a test adds is undone. Every test file must pass
in ISOLATION and under pytest-randomly. Never reintroduce alphabetical-ordering tricks
(`test_zz_*`); fix the isolation instead.

**Rule.** Line length 120 (Black, isort, flake8).

---

## Documentation structure

**Rule.** The README is a compact LANDING PAGE. Every substantive topic lives in its own
`docs/<topic>.md`, and every docs page has a runnable companion in `examples/` — standalone,
zero-arg, exit 0, executed by CI. A new topic gets a docs file + an example + a README index row,
in the same change. `docs/architecture.md` is the one exception (decision records, no twin).

**Rule — no obsolete-code documentation, no pre-1.0 backward compatibility** (user instruction
2026-08-17). A removed name is removed: no strikethrough rows in `docs/api-index.md`, no "was X /
migrate to Y" notes, no deprecation aliases, no test whose job is to recount the old name.
`docs/` and the CHANGELOG describe the surface that exists. Nobody outside this workspace consumed
0.x, so there is nobody to migrate — until 1.0, backward-compatibility scaffolding or migration
notes are added ONLY when the user asks in that change; when in doubt, ask.

**Rule.** README links MUST be absolute GitHub blob URLs (the README doubles as the PyPI landing
page, where relative links do not resolve); links BETWEEN `docs/*.md` files stay relative.

**Rule — public docs are STANDALONE** (user instruction 2026-07-14). The README and `docs/` must
never name workspace consumer projects or corporate-specific names. Describe consumers generically
("a visual editor", "an MCP discovery service"). Naming confluid's own DEPENDENCIES is fine.

**Rule — directory examples.** A scenario example needing a config-file tree lives in
`examples/<name>/`: `run.py` (zero-arg, exit 0, all paths `__file__`-relative), a `README.md` that
IS its documentation page (no `docs/*.md` twin — topic guides cross-link to it), the YAML files, and
a 1-line `__init__.py` (two sibling `run.py` files without package markers collide as duplicate
module `run` under mypy; keep `run.py` free of sibling `.py` imports). An example that must NOT run
in CI ships no `run.py`. Current members: `examples/modular_includes/` (the include-tree companion
to `docs/interpolation.md`) and `examples/ml_experiments/` (the Hydra-style scenario). The gin-style
scenario `examples/deep_injection.py` stays a flat script — inline YAML, no file tree needed.

**Rule.** An example that DEMONSTRATES A RULE should assert it, not just print it — a print-only
script exits 0 while printing the wrong number, and these files double as the documentation's proof.

**Rule — examples and guides show TAG input beside PLAIN output** (user ruling 2026-08-15,
record 19). A guide's config sample is written the
way a reader would write it — the tag form — and where the emitted/machine form matters (a
`hydraide` artefact, a `dump()`, `confluid.example.yaml`) it is shown as OUTPUT, labelled as such.
An example that demonstrates both spellings agree (`plain_format.py`) keeps both. Do not
reintroduce a spelling scan in either direction: the property worth pinning is that both
spellings resolve identically, and `tests/test_hydraide.py` pins it.

---

## Releasing to PyPI

Tag-driven: bump `version` in `pyproject.toml`, then `git tag v<version> && git push origin
v<version>` — `.github/workflows/release.yml` builds, verifies (twine strict + a clean-venv wheel
smoke test from a neutral cwd, since the repo root would shadow the installed package) and publishes
via PyPI Trusted Publishing. Publish ORDER across the trio: loggair → confluid → liquifai (each
depends on the previous being on PyPI). Once published, feature/fix PRs go on THIS repo
(`main ← dev/main`), never bundled into workspace PRs.
