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
plain-YAML reserved-key markers (`_target_` / `_partial_` / `_ref_` / `_clone_` / `_scope_` /
`_notscope_`) materialized by
`flow()` / `materialize()` / `resolve()`, scoped broadcasting, post-construction `configure()`,
recursive DI, and the introspection surface (`to_pydantic` / `parse_param_docs` /
`sanitize_schema`) that every AI- and GUI-facing consumer reads for tool schemas and form specs.

The equivalent TAG spelling (`!class:` / `!lazy:` / `!ref:` / `!scope:`) still parses in 0.3.0 and
is REMOVED in 0.4.0 — it warns on every document it loads. Write the reserved keys; convert an
old file with `confluid-migrate`.

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
and keeps `configure()` reconfiguration, cheap introspection (`resolve()` / `solidify=False`) and
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

**Pins.** `tests/test_optional_pydantic.py`. **Docs.** `docs/validation.md`.

---

## The engine

### Module map — one-directional layering

**Rule.** The layering is `fluid → state → broadcast → engine → loader`. Do not add cross-layer
imports; extend the right module instead.

| Module | Owns |
|---|---|
| `fluid` | the marker DATA classes (`Fluid`/`Target`/`Partial`/`Reference`/`Clone`/`ScopeBlock`) + `format_yaml_loc`. A dependency LEAF — imports no other confluid module. |
| `state` | `_EngineState` / `_ENGINE_STATE` (one `ContextVar`) + the public `active_context` / `collect_report`. Exists so `broadcast` can read the ambient report without importing the engine. |
| `broadcast` | the ONE precedence rule and all its machinery — see "Precedence & broadcasting". Materializes NOTHING. |
| `engine` | `flow`/`cast`, `materialize`/`resolve`, `_flow_recursive`/`_deep_flow`, the construction-side broadcast consumers. |
| `loader` | YAML parsing and composition ONLY (`ConfluidLoader`, `load`/`load_config`, includes/imports/scopes glue). |
| `introspect` | stdlib-only AST/signature scanning — the ONE `scan_init_body`, `init_callable`, `marked_param_names`. |

**Rule.** `broadcast` MUST NOT materialize anything — no `flow`, no `_flow_recursive`. Code that
BUILDS an object belongs in `engine`. That prohibition is why the edge is one-directional.

**Rule.** Exactly TWO lazy seams are sanctioned, both documented at the site: `engine.resolve()`
body-imports `loader.load`, and `resolver._materialize_cursor` body-imports `engine`.

**Rule.** The engine state rides ONE `contextvars.ContextVar`, so it is inherited by asyncio tasks
and `asyncio.to_thread` workers — NOT by a raw `Thread` / `run_in_executor`, which need
`contextvars.copy_context().run(...)` or an `active_context` inside the worker. `active_context(ctx)`
is the ONE sanctioned way for external code to activate a resolution context; downstream reach-ins
into the engine state are PROHIBITED. Note it does NOT enable broadcasting — pass the document
explicitly (`materialize(fluid, context=document)`) when a flat config's keys must reach the object.

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
`broadcast.clear_pass_caches()`, fired by `materialize()`, `resolve()` AND `configure()`. Never add
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

### ONE path grammar, two policies

**Rule.** Every dotted/bracketed path — `!ref:` targets, `${key.path}`, `configure()`'s dotted
candidates — is tokenized by `resolver._parse_path_segments` and walked by `_walk_path_segments`.
Extend the shared walker; never add a fourth grammar.

**Detail.** Two policies share it: **structural** (default — dict keys / list indices only) and
**object** (`getattr_fallback=True` — a `key` segment on a non-container cursor falls back to
`getattr`, flowing Fluid cursors through the engine's `flow_memo` so dotted refs share one
instance). Dict-key lookup ALWAYS wins on containers, so `${train.split}` can never grab
`str.split`. A trailing `()` CALLS the resolved attr per resolution, never memoized. A PURELY
structural resolution returns `None` from the rich path so the deferred-Reference machinery keeps
it late-bound for post-load overrides.

**Pins.** `tests/test_method_ref.py`, `tests/test_list_index_refs.py`,
`tests/test_ref_identity.py::test_dotted_attribute_ref_reuses_single_instance`.

### Two spellings, ONE intermediate representation

**Rule.** Confluid reads a document written with YAML TAGS (`!class:` / `!lazy:` / `!ref:` /
`!clone:` / `!scope:`) or with RESERVED KEYS (`_target_` / `_partial_` / `_ref_` / `_clone_` /
`_scope_` / `_notscope_`). Both MUST produce the SAME Fluid markers. A behaviour reachable from
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

**Rule.** A malformed marker raises a located `ConfigurationError` at load. Never degrade one
silently — that failure mode is precisely what this format replaces (`!class:Model(a=1, b=2)`,
with one space, produced a target named `Model(a=1,` and dropped both kwargs, with no error).

**Rule — scope grammar.** `loader._parse_scope_suffix` is the ONE splitter for every activation
spelling (the tag suffix, the `_scope_:` value, a CLI `--scope` argument). Never add a second.

**Rule — the QUOTED-STRING spelling refuses what it cannot honour.** A marker written as a
quoted string (`optimizer: "!class:Adam(lr=!ref:base)"`) is a THIRD grammar, parsed by
`Resolver.resolve` / `_parse_class_string`, not by a tag constructor. It parses `!class:` and
`!ref:` ONLY, and only OUTSIDE a marker's own kwargs. Every other combination now raises a
`ConfigurationError` quoting the offending text and naming the plain-YAML line to write
(`resolver._refuse_marker_string` / `_plain_yaml_for`). The refusal matches the exact marker
prefixes, NEVER a bare leading `!` — an ordinary value may start with one. It carries no
`file:line` because a scalar has none to carry (only markers are `_stamp_loc`-ed); that is
tracked in `TASKS.md`, not worked around.

**Rule — the whole path is DELETED in 0.4.0 with the tags.** It exists only to work around a
tag limitation (YAML forbids two tags on one node, so a nested `!ref:` had to be quoted), and
the reserved keys have no such limitation. Do NOT extend it — no new marker support, no
codemod rule. Census 2026-08-12: zero configs use it workspace-wide.

**Pins.** `tests/test_loader.py` — the quoted-marker refusal group (incl.
`::test_an_ordinary_value_starting_with_a_bang_is_untouched`, the false-positive guard).

**Rule — the TAG spelling is DEPRECATED and goes in 0.4.0.** Parsing a tag emits a
`FutureWarning` naming the file, the line and `confluid-migrate`, ONCE PER DOCUMENT (per-tag
buries the message; per-process names one config and hides the rest). `FutureWarning`, not
`DeprecationWarning`: Python shows it by default, and the audience is whoever wrote the YAML.
Never downgrade or silence it — the alias round in this same release found consumers still on
names "deprecated" for months because nothing ever said so at runtime.

**Rule — 0.3.0 ships BOTH the tags and `confluid-migrate`, and that pairing is deliberate.**
`migrate.verify_equivalence` parses the ORIGINAL tagged text to prove a conversion equivalent, so
a release that deleted the constructors while shipping the tool would make it refuse every file it
exists to convert. Deleting the constructors is 0.4.0 work, together with the `${PLAIN}`
env→config-key flip (both are the breaking half; see `TASKS.md`).

**Why.** `docs/architecture.md` record 11. The tag format is not YAML anything else can read, which
locks out `yq`, editor schemas and linters.

**Pins.** `tests/test_plain_format.py` — the `test_both_spellings_agree` matrix, the malformed-marker
group, `::test_a_full_document_is_readable_by_plain_yaml`, the deprecation trio
(`::test_a_tagged_document_announces_the_deprecation` / `::test_the_notice_is_ONCE_PER_DOCUMENT_not_once_per_tag` /
`::test_the_reserved_key_format_is_silent`),
`::test_yaml_anchors_and_aliases_still_work`, `::test_the_two_spellings_can_be_mixed_in_one_document`.
**Docs.** `docs/plain-format.md`. **Example.** `examples/plain_format.py`.

### The migration codemod edits LINES

**Rule.** `confluid/migrate.py` MUST NOT parse-and-rewrite a document. It converts the lines
carrying a tag and leaves every other byte alone. Never reach for a round-trip YAML library: it
would be a new dependency the DEPENDENCIES rule forbids, and it reformats regardless — measured on
a 458-line config, a comment-preserving round-tripper changed 34 lines on a NO-OP load+dump. These
configs are ~86% comments and the comments are the documentation.

**Rule.** Verification MUST run with the migrated file's directory as CWD (`_working_dir`). It
loads the document from TEXT, which carries no location, so a relative `include:` otherwise
resolves against the caller's working directory — every such config then reports
`ConfigFileNotFoundError` under every activation and is refused as unverifiable, which reads
exactly like the safety check working. **Pins.**
`tests/test_migrate.py::test_verification_resolves_a_relative_include_against_the_FILE`.

**Rule.** Safety is the VERIFICATION, never the parser. `verify_equivalence` compares resolved
marker trees **once per scope activation the document declares** (`discover_dimension_values` over
the RAW parse — `yaml.safe_load` cannot see a scope block, so asking IT for the dimensions silently
degrades the check to the default alone). A file whose conversion is not provably equivalent is
NOT written.

**Rule.** Anything the grammar cannot convert is REPORTED with its line, never guessed at, and a
post-pass sweep re-scans for surviving tags so nothing is dropped silently. A `Finding` makes the
run exit non-zero.

**Detail — the `@axis=` selector rides along.** It is part of the target STRING, not tag syntax,
so `_target_: Loss@framework=keras` is ordinary YAML and `registry.parse_target_spec` reads it
unchanged (the `$key` document form included). The codemod must NOT report it. Do not re-open
deleting the selector: the "zero users" census that proposed it read YAML configs only, and it is
the documented idiom in three consuming projects' user docs.

**Detail — the line shapes.** Four, all found by running the tool over the workspace: `key: !tag`,
`- !tag` (first key on the dash line), a tag ALONE on its line (keys join the block at the tag's
OWN indent), and a trailing flow mapping (`!class:X {a: 1}`, merged inline). The flow scan is
depth- AND quote-aware — a value may carry its own braces (`{ low_level: "-{template}" }`) and a
`\{[^}]*\}` pattern stops at the wrong one.

**Detail — interpolation.** `${NAME}` / `$NAME` → `${env:NAME}`, `${NAME:d}` → `${env:NAME,d}`; a
dotted name is a config key and is left alone. Meaning-preserving today, and it is what removes
every ambiguous spelling before a bare `${name}` is re-read as a config key. The pass MUST skip a
placeholder already naming a resolver, or it is not idempotent (`${env:X}` → `${env:env,X}`).

**Pins.** `tests/test_migrate.py` — the form matrix, the comments-are-never-edited group, the
real-config shapes, and the equivalence tests that feed WRONG conversions in (a check that only
ever passes is worse than none). **Docs.** `docs/plain-format.md` → "Migrating an existing config".

### There are exactly TWO construction modes

**Rule.** `partial` decides whether a marker is built, and NOTHING else does — not the parent's
configurability, not the nesting depth, not document position. `Target` is built by
materialization; `Partial` never is. `!class:Foo` and `!class:Foo()` are the SAME thing now (the
trailing `()` is inert); `!lazy:` / `_partial_: true` is the only spelling that defers.

**Rule.** A slot the RECEIVING class declared deferred (`Partial[T]`, or a body slot holding
`PartialClass(...)`) keeps its value unbuilt whatever the value says — the receiver's declared
contract, read via `partial_param_names(target)` in `_flow_target`. That is a slot declaration, not
parent-context guessing: static, local to the class, and readable. The ONE promotion site (marker →
`Partial`, with the warning) stays `_apply_post_init_attrs`.

**Rule.** Never reintroduce a third mode or a context-dependent build rule. The deleted middle state
(`Class`) was justified as "so broadcasting can still reach it", which is not a reason: broadcasting
is pass 7 and construction is pass 8, so a BUILT node already receives every cascading key before
its constructor runs.

**Rule — the memos key on `id()`, which is only unique while the object is ALIVE.** Every marker a
memo keys on MUST be pinned in `_EngineState.memo_keepalive` for the pass. The engine builds
short-lived broadcast COPIES, and CPython reuses a freed address, so an unpinned copy reads as a
memo HIT for an unrelated node — measured: every stage of a three-stage pipeline came back as stage
one. **Pins.** `tests/test_ref_identity.py::test_sibling_list_items_do_not_share_an_instance_via_recycled_ids`.

**Detail.** Inline `(k=v)` scalars are coerced via `parse_value` in both the unquoted and quoted
forms, and MERGE with a mapping body (the block wins on conflict). The unquoted form cannot contain
spaces or a nested tag (YAML allows one tag per node). `1e-3` is not a YAML float — write `1.0e-3`.
A kwarg literally named `target` is legal (the loader assigns kwargs post-construction).

**Pins.** `tests/test_loader.py`, `tests/test_partial.py`. **Docs.** `docs/targets.md`.

### Clone — ONE override semantic, decided by the referent's kind

**Rule.** A Clone's overrides are applied by `engine._clone_of` — the ONE site, called by BOTH
paths (`_flow_recursive`'s document branch and `_flow_clone`). The semantic follows the
REFERENT's kind, never the resolving path: a MARKER referent merges overrides into the copy's
kwargs and the clone is BUILT from them; a MAPPING merges keys (override wins); a LIVE object
takes setattrs (the `configure()` semantic); a scalar/list with overrides raises a located
`ConfigurationError` — never a silent drop. Deepcopy happens FIRST, so the template is never
mutated by its clones. Never re-inline an override application in one path.

**Rule.** `flow(clone, **runtime_kwargs)` configures the CLONE and runtime wins — never the
referent's build (the pre-2026-08-13 behaviour fed runtime kwargs to the referent and let the
clone's stored kwarg beat them). The fresh cloned marker is pinned in `memo_keepalive` (the
memos key on `id()`).

**Why.** `docs/architecture.md` record 14 — the split was invisible for convention-compliant
classes and measured three ways (an eager class's 0.4-vs-0.1, the runtime-kwarg inversion, the
silent drops).

**Pins.** `tests/test_clone.py` — the 2026-08-13 group:
`::test_clone_overrides_reach_the_constructor_on_BOTH_paths`,
`::test_runtime_kwargs_on_a_flowed_clone_win`,
`::test_a_live_referents_override_is_applied_not_dropped`,
`::test_clone_of_a_mapping_merges_overrides`,
`::test_clone_of_a_scalar_with_overrides_raises_located`,
`::test_clone_overrides_never_mutate_the_template_marker`.
**Docs.** `docs/targets.md` → "What the overrides mean".

### A target may be ANY callable

**Rule.** `!class:`/`!lazy:` targets, `Class`/`Partial`/`Instance`/`flow()` targets, AND the
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

**Rule.** A `Partial` is never AUTO-flowed — not by `materialize()`, not by external deep-flow
walkers. **Deferral withholds CONSTRUCTION only: a `Partial` IS broadcast into, exactly like a
`Class`.** Never re-add a blanket `isinstance(v, Partial)` early return in `_resolve_kwarg_value`.

**Rule.** An EXPLICIT `flow(node)` builds it, even with no runtime kwargs. A mapping addressed at a
slot already holding a deferred marker TUNES it, never replaces it. A marker kwarg set in CODE is a
DEFAULT and does not block a bare key.

**Rule.** Subscript the alias with the INTERFACE the slot flows into (`Partial[Optimizer]`, never
`Partial[Any]` when the type is known). `partial_param_names(cls)` is the single authority and reports a
body slot deferred by EITHER signal — a `PartialClass(...)` value or a `Partial[T]` annotation.

**Rule.** `resolve_ast_annotation` MUST `inspect.unwrap` before reading `__globals__` — the
validation wrapper's globals are confluid's own, which silently typed every body slot `Any`.

**Why.** `docs/architecture.md` record 5. The engine flag that settles ordering is
`Fluid._order_resolved`, never `_yaml_loc`.

**Pins.** `tests/test_partial.py`, `tests/test_deferred_broadcasting.py`.

### Post-flow auto-solidification

**Rule.** `flow()` calls any `solidify()` method on the object it returns — including on the
IDEMPOTENCY return, so an object that arrived already built is finalized too. `solidify()` MUST be
idempotent (build-once-and-cache) and take no arguments. `flow(obj, solidify=False)` /
`materialize(..., solidify=False)` suppress it for the whole subtree.

**Rule.** `configure()` finalizes AFTER applying: `_walk` flows with `solidify=False` and re-fires
the hook POST-ORDER, matching the load path's ordering.

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

### Introspection without cost — `resolve()` and `solidify=False`

**Rule.** `resolve(data)` = `materialize` MINUS `_deep_flow`: broadcasting and `!ref:` applied,
markers returned, NOTHING constructed. `flow(obj, solidify=False)` constructs but suppresses the
finalize. Neither changes default behaviour.

**Detail.** `resolve()` constructs NOTHING — a *dotted* `!ref:a.b` stays a `Reference` just as a
plain `!ref:name` does. The dotted branch in `_flow_recursive` is the ONE place a Reference
triggers construction (reading `split.train` means building `split`), and it is gated on
`_EngineState.structural`, which only `resolve()` sets. `materialize()` / `load()` are unchanged,
so a dotted ref still resolves off ONE shared instance there.

**Why.** Until 2026-08-11 the dotted branch ran on this path too, so the "introspection without
cost" API walked a dataset: 3.9s on a real config whose split scans 37 archives, for a call
documented as constructing nothing. Two consumers already claimed behaviour they were not getting
(a visual editor's YAML importer — "no instantiation, every node is a Fluid marker"; a flow-graph
builder — "step markers stay UNbuilt"), and two projects' shipped-config suites hand-rolled a
tag-stubbing parser because `resolve()` "is not an escape either". Steady-state cost is now 10ms.

**Pins.** `tests/test_resolve.py::test_resolve_leaves_a_DOTTED_ref_deferred` and
`::test_a_dotted_ref_still_resolves_through_load` (the two halves — the deferral is `resolve()`-only);
`tests/test_ref_identity.py::test_dotted_attribute_ref_reuses_single_instance` (one construction
through `load()`, unchanged).

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
  appended at the end.

**Rule.** The candidate set both paths' verdicts draw from is the ONE
`broadcast._cascade_scalar_positions` (bare keys at their own index, a `'**'` rider's scalar
contents at the RIDER's index). The directional reads stay per-caller (load: keys AFTER the slot;
configure: keys BEFORE the block); the candidate set may not differ again.

**Rule.** The configure()-path verdict is CALL-SCOPED (`_LiveSink.beaten_per_slot` →
`_tune_deferred(beaten=…)`), never marker state — a marker-stamped verdict outlives the document
that produced it. Deferred slots are tuned by their OWNER's scan; `_walk` returns early on a `Partial`.

**Rule.** The three engine fields on `Fluid` are read through `fluid.addressed_keys_of` /
`is_order_resolved` / `late_bare_keys_of`, NEVER a bare `getattr`.

**Why.** `docs/architecture.md` records 6 (position settled once, incl. the include-merge
corollary) and 8 (D7).

**Pins.** `tests/test_document_order.py` (the spelling × ordering matrix, the D7 rider matrix, the
rule-level pin that two orderings of one spelling must DISAGREE, and
`::test_a_second_configure_is_not_bound_by_the_first_ones_verdict`), `tests/test_includes.py`
(the ordering group), `tests/test_ordered_merge.py`.

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
on keys or scopes — a new rule goes behind a `_Receiver` predicate, added to BOTH factories
(`_receiver_for_target` / `_receiver_for_instance`) with a pin per path.

**Rule.** Glob keys are routing metadata: they never reach ctor kwargs, post-init setattrs,
`__confluid_kwargs__`, or `resolve()` marker kwargs.

**Detail — configure() parity.** Blocks unroll inline at their position (the `Cls.inst.attr` form
included), bare keys broadcast via the SHARED accept-list ∪ live `vars(obj)`, dict-valued entries
addressing a configurable child RECURSE with the sub-block spliced ADDRESSED into the child's view.
A present `null` SETS `None`; a typo'd non-dict key inside an object's own block emits ONE warning
(glob-delivered and bare keys never warn).

**Why.** `docs/architecture.md` record 8. **Docs.** `docs/broadcasting.md`, `docs/configure.md`.

**Pins.** `tests/test_broadcast_scoping.py` (incl.
`::test_var_keyword_class_receives_every_bare_key`), `tests/test_broadcast_wrapper_override.py`,
`tests/test_scanner.py`, `tests/test_cross_path_pins.py`, `tests/test_configurator.py` + the four
parity files (`test_basic_parity` / `test_names_parity` / `test_parity` / `test_transforms_parity`);
`examples/broadcasting.py` demonstrates the `**kwargs` split.

### Configuration reports

**Rule — naming.** The marker, the annotation and the YAML key are ONE word: `Partial` /
`PartialClass` / `partial_param_names` / `_partial_`. The deprecation aliases `Class` / `Instance` /
`Lazy` / `LazyClass` / `lazy_param_names` and the `confluid.lazy` shim module are DELETED
(2026-08-11) — importing one now fails loudly, which is the point. `Class` and `Instance` had
become the SAME class, so code discriminating with `isinstance(x, Instance)` reads `x.partial`.

**Rule.** `introspect._PARTIAL_CALL_NAMES` is the pinned list of call names that mark a body slot
deferred, and it matches on the NAME IN THE SOURCE — a name missing from it does not raise, the
slot is simply BUILT and a runtime-injection target reaches its constructor without its argument.
Adding a spelling means adding it there in the same change.

**Pins.** `tests/test_partial.py` (`::test_the_deprecated_aliases_are_removed`,
`::test_the_body_slot_scan_matches_the_canonical_call_names_only`).

**Rule.** `confluid/report.py` is a dependency LEAF (stdlib + loggair). Only `ConfigurationReport` and
`collect_report` are top-level exports. Every instrumentation site is `if report is not None`-guarded
so the default path stays zero-cost.

**Rule — `strict_attrs=True` closes the surface, and is the ONLY way to.** The default is
permissive and must stay so. A class opting in refuses any addressed key naming nothing it
declares, via the ONE `broadcast.refuse_if_undeclared`, called from the three sites B1 already
identified (`engine._warn_undeclared`, `_MergeSink.unknown`, `_LiveSink.unknown`). It binds
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
engine state for the load path. `materialize()`/`active_context()` MUST carry an ambient report into
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
inherited one (a plain `getattr` registers a subclass under its parent's custom name).
`key_for(cls)` is live-computed, never a second stamp — the dumper asks it so `dump()` emits a key
that reloads THIS class.

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
`category` — confluid derives `category = f"{task}_{role}"` and indexes all three. `framework=` is
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

**Pins.** `tests/test_task_role.py`, `tests/test_group.py`. **Docs.** `docs/discovery.md`.

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

**Rule — a LIST whose FIRST item is a `_scope_` mapping IS a scope block**, the remaining items its
body. That is the ONLY way to write a conditional list ITEM, because a YAML node is a mapping or a
sequence and never both. Registered as a DEFAULT SEQUENCE tag constructor that tests the first
node's keys, so an ordinary list keeps PyYAML's own path. Do NOT reintroduce a `_content_` key: it
needed a reserved key, a second body-shape rule AND a scalar special case, all of which this shape
removes (a scalar body is a one-item list, and `_resolve_list` already extends).

**Rule.** A dimension VALUE that YAML reads as a boolean is REJECTED with a quote-it message.
`{extra: yes}` becomes `True` and then never matches the `extra=yes` string an activation carries —
silently never firing. `{debug: }` is the boolean DIMENSION spelling.

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

**Rule.** An ACTIVE keyed scope MUST name a value the document declares, else `ScopeError`. Three
exemptions are load-bearing: an UNDECLARED dimension is an inert no-op; an UNSET dimension resolves
to defaults; a dimension carrying ANY `!notscope:` block accepts EVERY value. Consequently
`discover_dimension_values` reports POSITIVE values only and maps a negation-only dimension to an
EMPTY set — do NOT "fix" that.

**Rule.** `discover_dimension_values` takes the RAW document, never a `load()` result (whose blocks
are already spliced away).

**Why.** `docs/architecture.md` record 1. **Docs.** `docs/scopes.md`.
**Pins.** `tests/test_scopes.py` — the "active value must be declared" and "body shapes" groups,
plus `::test_keyed_scope_inside_a_markers_own_kwargs_swaps_the_slot` and the four
`*_inside_a_markers_kwargs_*` cases. **Example.** `examples/scopes.py`.

---

## Interpolation

**Rule.** `${...}` dispatches on the NAME SHAPE: a plain identifier is an environment variable; a
dotted/bracketed name is a config-key path resolved against the config tree (local context first,
then global). That test is what keeps every pre-existing `${VAR}` an env var.

**Rule.** Bare `$IDENTIFIER` expands as an ENV-ONLY read after the `${...}` pass — no dotted form,
no `:default`. Strings starting with `!` are EXEMPT (a marker string keeps its `$` for flow-time
parsing, where the `@axis=$key` selector grammar also spells `$`).

**Rule.** Interpolation is a SINGLE PASS and the substituted value BURNS IN — `dump()` emits it and
a deferred slot flowed later sees it. Marker kwargs are IN the pass (walked in place, identity
preserved, text-only). A slot that must stay late-bound uses `!ref:`, not `${...}`.

**Rule.** Bare `{key.path}` was deliberately NOT adopted — it collides with per-record templates and
checkpoint filenames that pass through configs as literals.

**Why.** `docs/architecture.md` record 7. **Docs.** `docs/interpolation.md`.
**Pins.** `tests/test_resolver.py` — the `${key.path}` block plus the bare-env group
(`::test_bare_env_var_expands_embedded_in_a_path` / `::test_bare_env_var_unset_stays_literal` /
`::test_bare_env_pass_leaves_marker_strings_untouched` /
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
fidelity is non-negotiable.

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
scope resolution (`confluid.scopes` — only `discover_dimensions` / `discover_dimension_values` are
public), annotation predicates (`confluid.partial` / `confluid.mandatory` / `confluid.pydantic_export`),
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

**Pins.** `tests/test_exceptions.py`. **Docs.** `docs/errors.md`.

### Dependencies

**Rule.** Confluid's runtime dependencies are `pyyaml`, `loggair`, `typing-extensions` — nothing
else. Pydantic (`confluid[pydantic]`) and python-dotenv (`confluid[env]`) are OPTIONAL extras,
imported lazily with an `ImportError` naming the extra. A new hard dependency needs a reason that
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

**Rule — the CANONICAL spelling, at three strictnesses.** No file under `examples/` may contain
`!class:` / `!lazy:` / `!ref:` / `!clone:` / `!scope:` / `!notscope:` anywhere — not in a YAML
literal, not in a docstring, not in a comment, not in a directory example's `README.md`. In
`docs/`, prose MAY name the deprecated spelling (that is how a reader with old configs learns
what to convert), but a fenced ```` ```yaml ```` block MUST NOT: a guide's config samples have
to agree with the companion example that proves them. Convert a config with `confluid-migrate`;
rewrite prose to name the reserved key.

The THIRD surface is the public API itself (2026-08-13): no docstring of a
`confluid.__all__` member may carry a tag LITERAL — `help(confluid.load)` is documentation with
the same obligation, and sixteen public objects still taught the tag spelling after the examples
and guides were converted. Prose naming the deprecated spelling ("the deprecated tag spelling
parses to the same marker") stays fine; implementation modules (`loader` / `resolver` /
`migrate`) are out of scope — they implement the tags.

Exemptions live with their reason in `tests/test_canonical_spelling.py` — examples
`plain_format.py` (proves the spellings agree) and `tags_deferred.py` (documents the tag form);
guides `targets.md` (the marker reference it converts FROM) and `architecture.md` (records
quoting the configs that motivated them). All four go with the constructors in 0.4.0.

**Why.** These are the first confluid a reader writes by hand. Twelve of the twenty-four
examples still taught the tag spelling on 2026-08-12 — including `lifecycle.py`, which the
README designates "start here" — so following the documentation path emitted the deprecation
warning; converting them then left 69 tag lines across nine guides showing configs their own
companion no longer matched. Running the examples proves nothing here: `ml_pipeline.py` warned
about nothing because it used the QUOTED-STRING spelling, and `examples/ml_experiments/` had
migrated YAML with comments, docstrings and a README still describing tags. The check is
therefore static, over source text.

**Pins.** `tests/test_canonical_spelling.py` (including the two inverse pins: an allow-list
entry naming a missing file, and an entry that is no longer needed).

---

## Releasing to PyPI

Tag-driven: bump `version` in `pyproject.toml`, then `git tag v<version> && git push origin
v<version>` — `.github/workflows/release.yml` builds, verifies (twine strict + a clean-venv wheel
smoke test from a neutral cwd, since the repo root would shadow the installed package) and publishes
via PyPI Trusted Publishing. Publish ORDER across the trio: loggair → confluid → liquifai (each
depends on the previous being on PyPI). Once published, feature/fix PRs go on THIS repo
(`main ← dev/main`), never bundled into workspace PRs.
