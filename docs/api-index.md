# API Index

Every public name (`confluid.<name>`), grouped by the guide that explains it.
This page is completeness-pinned: a name added to the public surface without a
row here fails a test.

New to confluid? The surface is wide because the engine has several audiences —
start from [The Lifecycle](lifecycle.md), which shows where each of these names
plugs into the passes, and from the eight names most configs need:
`configurable`, `register`, `load`, `configure`, `flow`, `cast`, `Partial`, `dump`.

## Loading & materialization — [Tags & Deferred Initialization](targets.md)

| Name | One line |
|---|---|
| `load` | Parse + resolve + materialize YAML (text, path, or parsed data) into live objects |
| `load_config` | Read and parse a YAML file (search-tier resolution, `include:` composition) — no materialization |
| `load_config_with_paths` | `load_config` plus the list of every file the `include:` tree pulled in |
| `materialize` | Resolve and instantiate already-parsed config data |
| `resolve` | Materialization *minus* construction — returns broadcast-merged markers ([Introspection](introspection.md)) |
| `flow` | Build one node now — with runtime args/kwargs for deferred slots |
| `cast` | `flow` that also narrows the static type for checkers ([Introspection](introspection.md)) |

## The marker family — [Tags & Deferred Initialization](targets.md)

| Name | One line |
|---|---|
| `Fluid` | Base class of every marker |
| `Target` | A callable + its kwargs (`_target_:`) — built at load, shared by identity |
| `Partial` / `PartialClass` | A runtime-injection slot (`_partial_: true`) — never auto-built; `PartialClass` is the Python spelling |
| `partial_param_names` | Every slot of a class declared `Partial[...]` — ctor params and body slots |
| `Class` / `Instance` / `Lazy` / `LazyClass` / `lazy_param_names` | *Deprecated aliases.* `Class`/`Instance` are both `Target` now; the `Lazy*` names are the old spelling of `Partial*` |
| `Reference` | A `!ref:` — the same object reached twice |
| `Clone` | A `!clone:` — an independent deep copy |

## Registration & discovery — [Discovery](discovery.md)

| Name | One line |
|---|---|
| `configurable` | The decorator: registers a class/callable and wraps its constructor with validation |
| `register` | Registration without the validation wrap — for classes/functions you don't own |
| `get_registry` | The registry object (`list_classes`, `get_class`, tag filters) |
| `load_configurables` | Import every module in the `confluid.configurables` entry-point group so its registrations run |
| `marks` / `Marks` | Read-only record of a class's confluid marks (task/role/framework, lazy/random/…) |
| `ignore_config` | Mark a class attribute as never-configurable |

## Post-construction configuration — [Post-Construction Configuration](configure.md)

| Name | One line |
|---|---|
| `configure` | Apply a document to live objects, in place |
| `configure_from_file` | Load a path, then `configure` |

## Reports — [Configuration Reports](report.md)

| Name | One line |
|---|---|
| `ConfigurationReport` | Applied / failed / unused keys for one pass |
| `collect_report` | Context manager collecting a report across `load`/`materialize`/`flow` |

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
| `partial_param_names` | The deferred (lazy) slot names of a target — params and body slots |

## Serialization — [Serialization](serialization.md)

| Name | One line |
|---|---|
| `dump` | A live object graph → reloadable YAML |

## Scopes — [Scopes](scopes.md)

| Name | One line |
|---|---|
| `discover_dimensions` | The scope dimensions a raw document declares |
| `discover_dimension_values` | The values each dimension offers |
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
| `ReferenceResolutionError` | A `!ref:` that cannot resolve |
| `UnknownClassError` | A name no registration or import satisfies |
| `AmbiguousClassError` | A name several registrations satisfy — narrow with tag filters |
| `ValidationModeError` | An invalid validation-mode value |
| `ScopeError` | See Scopes above |
| `IntrospectionError` | A signature/docstring scan failure (also a `TypeError`) |
| `WorkspaceEnvError` | A workspace-env loading failure (also a `RuntimeError`) |
| `format_yaml_loc` | The `file:line` suffix helper error messages use |
