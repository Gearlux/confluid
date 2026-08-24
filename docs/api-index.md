# API Index

Every public name (`confluid.<name>`), grouped by the guide that explains it.
This page is completeness-pinned: a name added to the public surface without a
row here fails a test.

New to confluid? The surface is wide because the engine has several audiences —
start from [The Lifecycle](lifecycle.md), which shows where each of these names
plugs into the passes, and from the eight names most configs need:
`configurable`, `register`, `load`, `configure`, `flow`, `cast`, `Partial[T]`, `dump`.

## Loading — [The Lifecycle](lifecycle.md) → "Where you can stop"

| Name | One line |
|---|---|
| `load` | The ONE door: text, path or parsed data → `until="raw"` (1–3) / `"document"` (1–6) / `"settled"` (1–7) / `"objects"` (1–9, default); `return_paths=True` adds the list of every file read |
| `flow` | Build one node now — with runtime args/kwargs for deferred slots |
| `cast` | `flow` that also narrows the static type for checkers ([Introspection](introspection.md)) |

## The marker family — [Targets & Deferred Initialization](targets.md)

| Name | One line |
|---|---|
| `Fluid` | Base class of every marker |
| `Target` | A callable + its kwargs (`_target_:`) — built at load, shared by identity |
| `PartialClass` | The deferred marker — a `Target` that materialization never builds (`_partial_: true` in YAML, `PartialClass(Adam, lr=1e-3)` in code). It defers a **value**: this recipe waits for an explicit `flow(marker, params=…)` |
| `Partial` | The slot **annotation**, written `Partial[T]` — `optimizer: Partial[Optimizer]` on a constructor param or body attribute. It defers a **slot**: whatever value lands there (a plain `!class:` too) stays an unbuilt marker until the class flows it. `T` is the type the slot flows INTO |
| `Reference` | A `${ref:}` / `_ref_` — the same object reached twice |

## Registration & discovery — [Discovery](discovery.md)

| Name | One line |
|---|---|
| `configurable` | The decorator: registers a class/callable and wraps its constructor with validation |
| `register` | Registration without the validation wrap — for classes/functions you don't own |
| `get_registry` | The registry object (`list_classes`, `get_class`, tag filters) |
| `load_configurables` | Import every module in the `confluid.configurables` entry-point group so its registrations run |
| `marks` / `Marks` | Read-only record of a class's confluid marks (task/role/framework, lazy/random/…) |

## Post-construction configuration — [Post-Construction Configuration](configure.md)

| Name | One line |
|---|---|
| `configure` | Apply a document to live objects, in place — through the document: the objects' own document + the config, resolved once, written back; keyword-named objects are addressable by dotted path |
| `configure_from_file` | Load a path, then `configure` |

## Reports — [Configuration Reports](report.md)

| Name | One line |
|---|---|
| `ConfigurationReport` | Applied / failed / unused keys for one pass; `.explain(key)` shows why a key has its value |
| `collect_report` | Context manager collecting a report across `load`/`flow` |

## Settability & broadcasting — [Broadcasting & Ordered Matching](broadcasting.md)

| Name | One line |
|---|---|
| `accepts_key` | May this key set this target when ADDRESSED? |
| `accepts_broadcast` | May a BARE key cascade onto this target (opt-outs honoured)? |
| `accepts_any_key` | Does this target discriminate between keys at all (`**kwargs`)? |
| `declares_key` | Does the target NAME this key (catchall never counts)? |
| `NoBroadcast` | Annotation: exclude one parameter from bare-key cascade |
| `no_broadcast_param_names` | The `NoBroadcast`-marked parameter names of a target |

## Schema export — [Schema Export](schema-export.md)

| Name | One line |
|---|---|
| `to_pydantic` | Constructor signature → validating pydantic model |
| `parse_param_docs` | Google-style `Args:` docstring → `{param: description}` |
| `confluid_class_of` | The origin class stored on a generated model |
| `sanitize_schema` | JSON Schema → the subset strict LLM function-calling APIs accept |
| `validate_model` | Re-validate a built model under a policy mode |

## Validation policy — [Validation](validation.md)

| Name | One line |
|---|---|
| `ValidationPolicy` / `ValidationMode` | The strict/warn/off knobs and their vocabulary |
| `set_policy` / `get_policy` / `reset_policy` | Read and change the process-wide policy |

## I/O contract — [I/O Contract](io-contract.md)

| Name | One line |
|---|---|
| `output` | Mark a read-only property as a declared output |
| `output_specs` / `input_specs` | A class's declared outputs / inputs, as spec records |
| `InputSpec` / `OutputSpec` | The record types those return |
| `Mandatory` | Annotation: required-in-spirit even when defaulted for zero-arg construction |
| `mandatory_param_names` | The `Mandatory`-marked parameter names of a target |
| `partial_param_names` | Which slots of a class are deferred (declared `Partial[T]`, or holding a `PartialClass(...)` body value) — what a walker asks before flowing a live object's markers, because a plain `!class:` in a `Partial[T]` slot stays a plain `Target` ([Targets](targets.md) → "Deferred initialization") |

## Serialization — [Serialization](serialization.md)

| Name | One line |
|---|---|
| `dump` | A live object graph → reloadable YAML |
| `register_dump_spelling` | The document spelling of a third-party value type — a marker or plain value, or decline to the placeholder |

## Scopes — [Scopes](scopes.md)

| Name | One line |
|---|---|
| `discover_dimensions` | The scope dimensions a raw document declares |
| `discover_dimension_values` | The values each dimension offers |
| `default_scopes` | The `{dimension: value}` a raw document's `default_scopes:` declares — what a bare load picks |
| `ScopeError` | Raised for an undeclared activation value or a circular alias |

## Hierarchy introspection — [Class Design](class-design.md)

| Name | One line |
|---|---|
| `get_hierarchy` | The configurable tree of a CLASS, as parameter rows |
| `get_hierarchy_from_instance` | The same view over a LIVE object graph |
| `get_configurable_attrs` | The configurable attribute names of one class |
| `shortest_unique_paths` | Shortest unambiguous dotted spellings for a hierarchy's rows |

## Config-file paths & environment — [Config-File Search Paths](search-paths.md)

| Name | One line |
|---|---|
| `resolve_config_path` | The one search-tier resolver (CWD → `./config/` → XDG) |
| `set_app_name` / `get_app_name` | The XDG namespace a CLI sets at startup |

## Concurrency — [Threads & Async](concurrency.md)

| Name | One line |
|---|---|
| `active_context` | Activate a resolution context for bare `flow()` calls |

## Merging & values — [Broadcasting](broadcasting.md), [Interpolation](interpolation.md)

| Name | One line |
|---|---|
| `deep_merge` | Merge one config tree onto another, Fluid-aware |
| `expand_dotted_keys` | `a.b: 1` → `a: {b: 1}` at the document top level |
| `parse_value` | Coerce a string to its YAML-native type (`"100"` → `100`) |

## Errors — [Error Handling](errors.md)

| Name | One line |
|---|---|
| `ConfluidError` | Root of the hierarchy — catch-all |
| `ConfigurationError` | Invalid config content (also a `ValueError`) |
| `ConfigFileNotFoundError` | No file at/under the searched tiers (also `FileNotFoundError`) |
| `CircularIncludeError` | An `include:` cycle |
| `ConfigurableDefinitionError` | A class definition confluid cannot accept |
| `ConstructionError` | A target could not be built (also a `RuntimeError`) |
| `ReferenceResolutionError` | A `${ref:}` that cannot resolve |
| `UnknownClassError` | A name no registration or import satisfies |
| `AmbiguousClassError` | A name several registrations satisfy — narrow with tag filters |
| `ValidationModeError` | An invalid validation-mode value |
| `ScopeError` | See Scopes above |
| `IntrospectionError` | A signature/docstring scan failure (also a `TypeError`) |
| `WorkspaceEnvError` | A workspace-env loading failure (also a `RuntimeError`) |
| `format_yaml_loc` | The `file:line` suffix helper error messages use |

## `confluid.hydraide` — the preprocessor

| Name | What it does | Guide |
|---|---|---|
| `emit(source, *, scopes=None)` | The resolved document for a path or YAML text, as plain YAML — passes 1–7 + the serializer | [hydraide](hydraide.md) |
| `check(path, *, scopes=None)` | `None` when the file is its own resolution, else a unified diff | [hydraide](hydraide.md) |
| `hydraide` (console script, `confluid[cli]`) | `hydraide emit CONFIG [--scope DIM=VALUE]… [-o FILE]` / `hydraide check CONFIG` / `hydraide completion bash\|zsh\|fish` — Click, `confluid/cli.py`; `--scope` completes from the document | [hydraide](hydraide.md) → "The command line" |

## `confluid.spelling` — the reserved-key spelling → the tag spelling

| Name | What it does | Guide |
|---|---|---|
| `to_tags(text, *, path="<config>")` | `(new_text, findings)` — the tag spelling, line by line, comments and layout kept; a `Finding(path, line, text, reason)` per site left untouched | [hydraide](hydraide.md) |
| `convert_file(path, *, dry_run=False)` | Rewrites a file ONLY when `hydraide.emit` is byte-identical before and after, under every declared scope activation; `Result(path, written, findings, activations_checked)` | [hydraide](hydraide.md) |
