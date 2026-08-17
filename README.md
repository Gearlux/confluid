# Confluid

**Confluid** is a modern, hierarchical configuration and dependency injection framework for Python, built for researchers and engineers who need modularity and 100% reproducibility in their experiment pipelines.

## Key Features
- **Two spellings, one document:** write configs in the concise [tag form](https://github.com/Gearlux/confluid/blob/main/docs/targets.md) — `model: !class:MLP(hidden=32)`, `optimizer: !partial:Adam`, `!ref:model` — or in the [reserved-key form](https://github.com/Gearlux/confluid/blob/main/docs/plain-format.md) — `model: {_target_: MLP, hidden: 32}` — which is ordinary YAML that `yaml.safe_load`, `yq`, editor schemas and linters read. Both are first-class input and may be mixed. **`hydraide` resolves either to one plain document** — includes spliced, scopes applied, broadcasting settled — see the [hydraide guide](https://github.com/Gearlux/confluid/blob/main/docs/hydraide.md).
- **Works with plain Python classes:** Required constructor params and real work in `__init__` are fully supported for load/flow/dump — the lazy/zero-arg class-design convention is optional.
- **Post-Construction Configuration:** Configure existing objects without requiring re-instantiation.
- **Strict Gated Hierarchy:** Prevents deep-traversal into non-configurable third-party objects.
- **Third-Party Registration:** Easily make third-party classes (like PyTorch Optimizers) — or plain builder **functions** — part of your configurable graph via `@configurable` / `register`.
- **Smart Reference Resolution:** `!ref:` (`_ref_` / `${ref:…}` in the plain form) for cross-config references (shared instance), `${env:VAR}` for environment variables and `${train.dataset}` for config keys.
- **Deferred Initialization — two modes, nothing implicit:** a node is built at load, or `!partial:` (`_partial_: true`) keeps it deferred until you `flow()` it with runtime-injected arguments (e.g. an optimizer needing `params=model.parameters()`, or a model needing `num_classes` from the dataset). Nothing about the surrounding document changes which one you get.
- **Full Hierarchy Dumping:** Export your runtime state to YAML/JSON and reconstruct it later.
- **Schema Export & Validation:** Auto-generated pydantic schemas validate every `@configurable` constructor; docstring `Args:` blocks become machine-readable parameter help (`parse_param_docs`); `sanitize_schema` downgrades schemas to the subset strict LLM function-calling APIs accept.
- **I/O Contract:** `@output` properties and `Mandatory[T]` inputs declare a Runnable's contract for GUIs and agents from one source.
- **Flat-View Ordered Matching:** Bare keys broadcast tree-wide (an implicit `**.key`), addressed keys (`trainer.lr` / `trainer: {lr: …}`) are **exact** — no cascade to descendants — and glob wildcards opt back in (`trainer.*.lr` = direct children, `trainer.**.lr` = the node and all descendants). Matching scalars apply in YAML document order with **last-write-wins** semantics — no hidden priority tiers.
- **Scopes:** conditional overlays (`!scope:debug`, `!scope:task=classification`, `!notscope:…`) activated per run — the reserved-key spelling `_scope_: {…}` takes a mapping of dimension to required value, so one block can require several at once.

## Documentation

For the *why* — how confluid compares to Hydra, gin-config, and plain pydantic, and the design
bets behind it — see [RATIONALE.md](https://github.com/Gearlux/confluid/blob/main/RATIONALE.md).
Looking for a specific function? The [API index](https://github.com/Gearlux/confluid/blob/main/docs/api-index.md)
maps every public name to its guide.

Each topic has its own guide, and every guide except the architecture notes has a runnable companion script in [`examples/`](https://github.com/Gearlux/confluid/tree/main/examples):

| Guide | What it covers | Example |
|---|---|---|
| [**The Lifecycle**](https://github.com/Gearlux/confluid/blob/main/docs/lifecycle.md) — *start here* | How a document becomes objects: the nine passes in order (parse → import → include → scope → interpolate → expand → broadcast → flow → solidify), what each one decides permanently, and where you can stop | `lifecycle.py` |
| [**The Plain-YAML Format**](https://github.com/Gearlux/confluid/blob/main/docs/plain-format.md) | Writing configs as ordinary YAML that `yaml.safe_load` and `yq` read: `_target_` / `_partial_` construction, `_ref_` and its `${ref:…}` shorthand, `${env:…}`, anchors and `<<:` merge keys, `_scope_` blocks | `plain_format.py` |
| [**hydraide — the preprocessor**](https://github.com/Gearlux/confluid/blob/main/docs/hydraide.md) | Resolve a config (either spelling) to ONE plain-YAML document: includes spliced, scopes applied, dotted keys expanded, broadcasting settled, shared markers anchored — `emit(cfg, scopes=[...])` / `check(path)`; the `hydraide` command line ships with the CLI framework built on confluid | `hydraide.py` |
| [Targets & Deferred Initialization](https://github.com/Gearlux/confluid/blob/main/docs/targets.md) | The marker family and the Fluid→Solid lifecycle: `Target` vs `Partial`, `flow()` runtime injection, reference identity, and the legacy tag spelling of each | `tags_deferred.py` |
| [Broadcasting & Ordered Matching](https://github.com/Gearlux/confluid/blob/main/docs/broadcasting.md) | Bare/addressed/glob scoping (`*` / `**`), document-order/last-write-wins matching, `NoBroadcast` / `broadcast=False` opt-outs, the frozen-deployment bake step | `broadcasting.py` |
| [Post-Construction Configuration](https://github.com/Gearlux/confluid/blob/main/docs/configure.md) | `configure()` / `configure_from_file` — applying a document to LIVE objects: the same one matching rule, deferred-slot tuning, layered calls, values-before-finalize ordering | `configure.py` |
| [Closing the Config Surface](https://github.com/Gearlux/confluid/blob/main/docs/strict-attrs.md) | `strict_attrs=True` — refuse an addressed key the class declares nowhere (the permissive default warns and applies it); what stays untouched: bare keys, `**kwargs` targets, declared slots; `register(..., strict_attrs=True)` for classes you don't own | `strict_attrs.py` |
| [Configuration Reports](https://github.com/Gearlux/confluid/blob/main/docs/report.md) | `ConfigurationReport` — applied/failed/unused override keys; `configure()`'s return value, the `collect_report()` context manager for `load()`/`materialize()`/`flow()` | `report.py` |
| [Interpolation & Config Files](https://github.com/Gearlux/confluid/blob/main/docs/interpolation.md) | `${ENV}` + `${config.key}` interpolation, capturing the `include:` tree | `interpolation_includes.py` |
| [Config-File Search Paths](https://github.com/Gearlux/confluid/blob/main/docs/search-paths.md) | XDG-last resolution of relative paths and `include:` entries (CWD → `./config/` → XDG base dirs), `set_app_name` namespacing, `resolve_config_path` | `search_paths.py` |
| [Class Design](https://github.com/Gearlux/confluid/blob/main/docs/class-design.md) | Lazy init & zero-arg construction — the four-rule convention for reconfigurable classes | `ml_pipeline.py`, `basic_registration.py` |
| [Eager Classes](https://github.com/Gearlux/confluid/blob/main/docs/eager-classes.md) | Plain constructors — required params, work in `__init__`, full dump round-trip via captured kwargs, the `capture=False` opt-out, the `eager=True` staleness warning | `eager_classes.py` |
| [I/O Contract](https://github.com/Gearlux/confluid/blob/main/docs/io-contract.md) | `@output` properties, `Mandatory[T]` inputs, `output_specs` / `input_specs` | `io_contract.py` |
| [Validation](https://github.com/Gearlux/confluid/blob/main/docs/validation.md) | The three strict/warn/off validation points, `Annotated[..., Field(...)]` constraints, `validate=False` | `validation.py` |
| [Schema Export](https://github.com/Gearlux/confluid/blob/main/docs/schema-export.md) | `to_pydantic` (signature → validating model, docstrings → field help, constraints → JSON schema), `parse_param_docs`, `validate_model`, the `sanitize_schema` LLM-safe downgrade | `schema_export.py` |
| [Discovery](https://github.com/Gearlux/confluid/blob/main/docs/discovery.md) | `category` / `group` tags, behavioral marks (`random` / `constant`), docstring-derived help | `discovery.py` |
| [Extending the Discovery Surface](https://github.com/Gearlux/confluid/blob/main/docs/extending-discovery.md) | The end-to-end contract a tagged class must satisfy to surface automatically in an MCP tool server and a visual node editor: the `task` × `role` taxonomy, entry-point registration, signature-to-widget/schema mapping, and the common failure modes | `discovery.py` |
| [Error Handling](https://github.com/Gearlux/confluid/blob/main/docs/errors.md) | The typed exception hierarchy (each also inherits the builtin it replaces) | `error_handling.py` |
| [Scopes](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md) | `_scope_` / `_notscope_` conditional overlays, their activation, and `discover_dimension_values` — what a document offers, and the error when you ask for something else | `scopes.py` |
| [Introspection](https://github.com/Gearlux/confluid/blob/main/docs/introspection.md) | `cast()` for type checkers, `resolve()` markers, `solidify=False`, dump/reconstruct | `introspection.py` |
| [Serialization](https://github.com/Gearlux/confluid/blob/main/docs/serialization.md) | `dump()` and the round trip: per-param reconstruction (live attr, captured kwargs), what a dump omits and why, registry-handle class names, burned-in interpolation | `reproducible_experiment.py` |
| [Threads & Async](https://github.com/Gearlux/confluid/blob/main/docs/concurrency.md) | ContextVar propagation, `active_context`, worker-thread recipes | `concurrency.py` |
| [Architecture Decisions](https://github.com/Gearlux/confluid/blob/main/docs/architecture.md) | The *why* behind non-obvious behaviour — decision records, backfilled as the questions come up | — |
| [Performance](https://github.com/Gearlux/confluid/blob/main/docs/performance.md) | The engine-timing baseline: per-phase benchmark over a ~2,500-marker tree, `CONFLUID_BENCH_PROFILE=1` profiling mode | `performance.py` |

### Real-world scenarios

Two examples show the features working *together* at application scale (pure
Python, no ML dependencies — run them as-is):

- [`examples/ml_experiments/`](https://github.com/Gearlux/confluid/tree/main/examples/ml_experiments)
  — an ML experiment suite in the Hydra style: one base config, model/optimizer
  **config groups** selected per run via scopes (`scopes=["model=cnn"]`),
  `include:` experiment overlays, `!class:`/`!ref:`/`!partial:` object wiring,
  bare-key broadcast of global knobs (`seed`, `device`), and a `dump()`
  snapshot that reloads into the identical experiment. Its README maps each
  Hydra concept to the confluid feature that plays its role.
- [`examples/deep_injection.py`](https://github.com/Gearlux/confluid/blob/main/examples/deep_injection.py)
  — the gin-config pitch: a four-level service tree (`Pipeline → Stage →
  Worker → RetryPolicy`) where one bare YAML key configures the deepest leaf
  with **zero parameter-threading code**, while addressed keys stay surgical,
  globs scope a subtree, and `NoBroadcast` protects generic names.
- [`examples/modular_includes/`](https://github.com/Gearlux/confluid/tree/main/examples/modular_includes)
  — the on-disk `include:` tree companion to the
  [Interpolation guide](https://github.com/Gearlux/confluid/blob/main/docs/interpolation.md):
  a config split across files, composed at load.

## Design Goals & Requirements

### Configuration Engine
- **Dotted-Key Resolution:** Allow flat overrides to target nested attributes (e.g. `model.layers: 10`).
- **Two Spellings, One IR:** a marker is a YAML tag (`!class:` / `!partial:` / `!ref:` / `!scope:` — the authoring form) or an ordinary mapping carrying a reserved key (`_target_`, `_partial_`, `_ref_`, `_scope_` — the machine form `hydraide` emits, readable by any YAML parser). Both parse to the same markers.
- **Object-Based Internal Representation:** Use the typed Fluid marker family (`Target`, `Partial`, `Reference`) for internal resolution.

### Dependency Injection
- **Automatic Hydration:** Support `@configurable` decorator for automatic class registration and instantiation.
- **Fluid-Solid Protocol:** Implement a two-stage lifecycle where objects are defined ("Fluid") and then materialized ("Solid").
- **Materialize API:** Provide an explicit `materialize()` function to instantiate objects from already-resolved configuration.

### Robustness
- **IR-Aware Merging:** `deep_merge` and `expand_dotted_keys` must traverse into Fluid marker kwargs.
- **Circular Reference Detection:** Gracefully handle and report circular dependencies in the object graph.
- **Type Coercion:** Integrate `parse_value` to ensure CLI strings (e.g. "100") are cast to correct types (int 100).

## Quick Start

### 1. Define Configurable Classes
```python
from typing import Optional

from confluid import configurable

@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

@configurable
class Trainer:
    # Lazy + zero-arg: every parameter is defaulted, so `Trainer()` works and the model is
    # wired afterwards. See the "Class Design" guide.
    def __init__(self, model: Optional[Model] = None, lr: float = 0.001):
        self.model = model
        self.lr = lr
```

### 2. Configure via YAML
```yaml
# experiment.yaml — the tag form; `hydraide.emit(...)` emits the plain form `yq` reads
defaults:
  n_layers: 10

Trainer:
  lr: 0.0001
  model: !class:Model
    layers: ${defaults.n_layers}    # config-key interpolation — one source of truth
```

### 3. Load and Apply
```python
from confluid import load_config, configure

# Instantiate with defaults
model = Model()
trainer = Trainer(model=model)

# Apply configuration — returns a ConfigurationReport (applied/failed/unused keys)
config = load_config("experiment.yaml")
report = configure(trainer, config=config)

print(trainer.lr) # 0.0001
print(trainer.model.layers) # 10
print(report.summary()) # "2 applied, 0 failed, 1 unused"
# (the "1 unused": `defaults` feeds the interpolation but sets no attribute
#  itself, so it counts as an unused OVERRIDE — see docs/report.md)
```

`configure_from_file` collapses the load + apply into one call — handy when the config lives on disk:

```python
from confluid import configure_from_file

# Equivalent to configure(trainer, config=load_config("experiment.yaml"))
configure_from_file(trainer, path="experiment.yaml")
```

It reads the file via `load_config` (so `include:` / `import:` directives and `!class:` / `!ref:` markers are honoured) and then applies it exactly as `configure` does. A missing path raises `ConfigFileNotFoundError`. Matching follows the one rule described in the [Broadcasting guide](https://github.com/Gearlux/confluid/blob/main/docs/broadcasting.md): document order, last write wins.

### 4. Dump and Reconstruct
```python
from confluid import dump, load

# Export current state
state_yaml = dump(trainer)

# Recreate exact same hierarchy in a new process
new_trainer = load(state_yaml)
```

## Installation
```bash
pip install confluid                     # from PyPI
pip install "confluid[pydantic]"    # + pydantic-powered schema export & validation
```

Or straight from GitHub:

```bash
pip install git+https://github.com/Gearlux/confluid.git@main
```

## License
MIT
