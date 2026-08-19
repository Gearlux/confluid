# Confluid

**Confluid** is a modern, hierarchical configuration and dependency injection framework for Python, built for researchers and engineers who need modularity and 100% reproducibility in their experiment pipelines.

## Installation

Released on [PyPI](https://pypi.org/project/confluid/):

```bash
pip install confluid                # the configuration engine (pyyaml, loggair, typing-extensions)
pip install "confluid[pydantic]"    # + pydantic-powered schema export & validation
pip install "confluid[cli]"         # + the `hydraide` command (Click; `eval "$(hydraide completion bash)"`)
```

## Quick Start

The whole walkthrough is [`examples/quickstart.py`](https://github.com/Gearlux/confluid/blob/main/examples/quickstart.py) — runnable, and every number below is asserted there.

### 1. Define configurable classes

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
    # Every parameter defaulted, so `Trainer()` works and the model can be wired afterwards
    # (see "Class Design"). Optional: required params and real work in __init__ are fine too.
    def __init__(self, model: Optional[Model] = None, lr: float = 0.001):
        self.model = model
        self.lr = lr
```

### 2. Describe the objects in YAML

```yaml
# experiment.yaml — the tag form; `hydraide emit` turns it into the plain form `yq` reads
defaults:
  n_layers: 10

trainer: !class:Trainer
  lr: 0.0001
  model: !class:Model
    layers: ${defaults.n_layers}      # config-key interpolation — one source of truth
```

### 3. Build everything from the document

```python
from confluid import load

trainer = load("experiment.yaml")["trainer"]   # the whole graph, constructed and wired

print(type(trainer).__name__, trainer.lr)            # Trainer 0.0001
print(type(trainer.model).__name__, trainer.model.layers)   # Model 10
```

`load` runs the passes a document needs — includes, scopes, interpolation, broadcasting,
construction — and stops where you say: `load(path, until="document")` gives you the
merged document before anything is built, `until="settled"` the markers with their final
kwargs (see [The Lifecycle](https://github.com/Gearlux/confluid/blob/main/docs/lifecycle.md)).

### 4. …or configure objects you already have

A mapping at a slot tunes the live object **in place** — the existing `Model` stays the
same object:

```yaml
# overrides.yaml
Trainer:
  lr: 0.0001
  model:
    layers: 10
```

```python
from confluid import configure, configure_from_file, load

model = Model()
trainer = Trainer(model=model)

report = configure(trainer, config=load("overrides.yaml", until="raw"))
print(trainer.lr, trainer.model.layers)   # 0.0001 10
print(trainer.model is model)             # True — tuned in place
print(report.summary())                   # 2 applied, 0 failed, 0 unused

configure_from_file(trainer, path="overrides.yaml")   # load + configure in one call
```

`configure` returns a [`ConfigurationReport`](https://github.com/Gearlux/confluid/blob/main/docs/report.md)
(applied / failed / unused keys). Matching follows the one rule of the
[Broadcasting guide](https://github.com/Gearlux/confluid/blob/main/docs/broadcasting.md):
document order, last write wins.

### 5. Dump and reconstruct

```python
from confluid import dump, load

state_yaml = dump(trainer)        # the live object graph as a reloadable document
new_trainer = load(state_yaml)    # the identical hierarchy — e.g. in another process
```

## Key Features
- **Modern layout, Hydra-like output:** write configs with custom YAML tags — `model: !class:MLP(hidden=32)`, `optimizer: !partial:Adam`, `!ref:model` (the [tag form](https://github.com/Gearlux/confluid/blob/main/docs/targets.md)) — and convert them with [`hydraide emit`](https://github.com/Gearlux/confluid/blob/main/docs/hydraide.md) to a Hydra-like plain-YAML format — `model: {_target_: MLP, hidden: 32}` (the [reserved-key form](https://github.com/Gearlux/confluid/blob/main/docs/plain-format.md)) — that `yaml.safe_load`, `yq`, editor schemas and linters read: includes spliced, scopes applied, broadcasting settled, one file. Both forms load, and may be mixed.
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
| [**hydraide — the preprocessor**](https://github.com/Gearlux/confluid/blob/main/docs/hydraide.md) | Resolve a config (either spelling) to ONE plain-YAML document: includes spliced, scopes applied, dotted keys expanded, broadcasting settled, shared markers anchored — `emit(cfg, scopes=[...])` / `check(path)`, and the `hydraide emit` / `check` command (`confluid[cli]`, shell completion incl. `--scope` values from the document) | `hydraide.py` |
| [Targets & Deferred Initialization](https://github.com/Gearlux/confluid/blob/main/docs/targets.md) | The marker family and the Fluid→Solid lifecycle: `Target` vs `PartialClass`, `flow()` runtime injection, reference identity, and the tag and reserved-key spelling of each | `tags_deferred.py` |
| [Broadcasting & Ordered Matching](https://github.com/Gearlux/confluid/blob/main/docs/broadcasting.md) | Bare/addressed/glob scoping (`*` / `**`), document-order/last-write-wins matching, `NoBroadcast` / `broadcast=False` opt-outs, the frozen-deployment bake step | `broadcasting.py` |
| [Post-Construction Configuration](https://github.com/Gearlux/confluid/blob/main/docs/configure.md) | `configure()` / `configure_from_file` — applying a document to LIVE objects: the same one matching rule, deferred-slot tuning, layered calls, values-before-finalize ordering | `configure.py` |
| [Closing the Config Surface](https://github.com/Gearlux/confluid/blob/main/docs/strict-attrs.md) | `strict_attrs=True` — refuse an addressed key the class declares nowhere (the permissive default warns and applies it); what stays untouched: bare keys, `**kwargs` targets, declared slots; `register(..., strict_attrs=True)` for classes you don't own | `strict_attrs.py` |
| [Configuration Reports](https://github.com/Gearlux/confluid/blob/main/docs/report.md) | `ConfigurationReport` — applied/failed/unused override keys; `configure()`'s return value, the `collect_report()` context manager for `load()`/`load()`/`flow()` | `report.py` |
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
| [Scopes](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md) | `_scope_` / `_notscope_` conditional overlays, their activation, `default_scopes:` (what a bare load picks), and `discover_dimension_values` — what a document offers, and the error when you ask for something else | `scopes.py` |
| [Introspection](https://github.com/Gearlux/confluid/blob/main/docs/introspection.md) | `cast()` for type checkers, `load(until="settled")` markers, `solidify=False`, dump/reconstruct | `introspection.py` |
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
- **Modern layout, Hydra-like output:** configs are written with custom YAML tags (`!class:` / `!partial:` / `!ref:` / `!scope:`) and can be converted — `hydraide emit` — to a Hydra-like plain-YAML format (`_target_` / `_partial_` / `_ref_` / `_scope_`) that any YAML parser reads. Both parse to the same markers.
- **Object-Based Internal Representation:** Use the typed Fluid marker family (`Target`, `PartialClass`, `Reference`) for internal resolution.

### Dependency Injection
- **Automatic Hydration:** Support `@configurable` decorator for automatic class registration and instantiation.
- **Fluid-Solid Protocol:** Implement a two-stage lifecycle where objects are defined ("Fluid") and then materialized ("Solid").
- **Materialize API:** Provide an explicit `load()` function to instantiate objects from already-resolved configuration.

### Robustness
- **IR-Aware Merging:** `deep_merge` and `expand_dotted_keys` must traverse into Fluid marker kwargs.
- **Circular Reference Detection:** Gracefully handle and report circular dependencies in the object graph.
- **Type Coercion:** Integrate `parse_value` to ensure CLI strings (e.g. "100") are cast to correct types (int 100).

## License
MIT
