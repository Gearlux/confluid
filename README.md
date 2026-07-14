# Confluid

**Confluid** is a modern, hierarchical configuration and dependency injection framework for Python, built for researchers and engineers who need modularity and 100% reproducibility in their experiment pipelines.

## Key Features
- **Works with plain Python classes:** Required constructor params and real work in `__init__` are fully supported for load/flow/dump — the lazy/zero-arg class-design convention is optional.
- **Post-Construction Configuration:** Configure existing objects without requiring re-instantiation.
- **Strict Gated Hierarchy:** Prevents deep-traversal into non-configurable third-party objects.
- **Third-Party Registration:** Easily make third-party classes (like PyTorch Optimizers) — or plain builder **functions** — part of your configurable graph via `@configurable` / `register`.
- **Smart Reference Resolution:** Uses `!ref:` syntax for cross-config references (shared instance), `!clone:` for a deep copy, and `${...}` for string interpolation of environment variables (`${HOME}`) AND config keys (`${train.dataset}`).
- **Deferred Initialization:** A node is built eagerly (`!class:Model()`) or left as a deferred recipe (`!class:Model`); `!lazy:` keeps a node deferred until you `flow()` it with runtime-injected arguments (e.g. an optimizer needing `params=model.parameters()`).
- **Full Hierarchy Dumping:** Export your runtime state to YAML/JSON and reconstruct it later.
- **Schema Export & Validation:** Auto-generated pydantic schemas validate every `@configurable` constructor; docstring `Args:` blocks become machine-readable parameter help (`parse_param_docs`); `sanitize_schema` downgrades schemas to the subset strict LLM function-calling APIs accept.
- **I/O Contract:** `@output` properties and `Mandatory[T]` inputs declare a Runnable's contract for GUIs and agents from one source.
- **Flat-View Ordered Matching:** Bare keys broadcast tree-wide (an implicit `**.key`), addressed keys (`trainer.lr` / `trainer: {lr: …}`) are **exact** — no cascade to descendants — and glob wildcards opt back in (`trainer.*.lr` = direct children, `trainer.**.lr` = the node and all descendants). Matching scalars apply in YAML document order with **last-write-wins** semantics — no hidden priority tiers.
- **Tag-Driven Scopes:** Conditional overlays (`!scope:debug`, `!scope:task=classification`, `!notscope:…`) activated per run.

## Documentation

Each topic has its own guide, and every guide has a runnable companion script in [`examples/`](https://github.com/Gearlux/confluid/tree/main/examples):

| Guide | What it covers | Example |
|---|---|---|
| [Tags & Deferred Initialization](https://github.com/Gearlux/confluid/blob/main/docs/tags.md) | The six YAML tags, Fluid→Solid lifecycle, `!class:` eager-vs-deferred, `!lazy:` + `flow()`, `!ref:` vs `!clone:` | `tags_deferred.py` |
| [Broadcasting & Ordered Matching](https://github.com/Gearlux/confluid/blob/main/docs/broadcasting.md) | Bare/addressed/glob scoping (`*` / `**`), document-order/last-write-wins matching, `NoBroadcast` / `broadcast=False` opt-outs, the frozen-deployment bake step | `broadcasting.py` |
| [Configuration Reports](https://github.com/Gearlux/confluid/blob/main/docs/report.md) | `ConfigurationReport` — applied/failed/unused override keys; `configure()`'s return value, the `collect_report()` context manager for `load()`/`materialize()`/`flow()` | `report.py` |
| [Interpolation & Config Files](https://github.com/Gearlux/confluid/blob/main/docs/interpolation.md) | `${ENV}` + `${config.key}` interpolation, capturing the `include:` tree | `interpolation_includes.py` |
| [Class Design](https://github.com/Gearlux/confluid/blob/main/docs/class-design.md) | Lazy init & zero-arg construction — the four-rule convention for reconfigurable classes | `ml_pipeline.py` et al. |
| [Eager Classes](https://github.com/Gearlux/confluid/blob/main/docs/eager-classes.md) | Plain constructors — required params, work in `__init__`, full dump round-trip via captured kwargs, the `capture=False` opt-out, the `eager=True` staleness warning | `eager_classes.py` |
| [I/O Contract](https://github.com/Gearlux/confluid/blob/main/docs/io-contract.md) | `@output` properties, `Mandatory[T]` inputs, `output_specs` / `input_specs` | `io_contract.py` |
| [Validation](https://github.com/Gearlux/confluid/blob/main/docs/validation.md) | The three strict/warn/off validation points, `Annotated[..., Field(...)]` constraints, `validate=False` | `validation.py` |
| [Discovery](https://github.com/Gearlux/confluid/blob/main/docs/discovery.md) | `category` / `group` tags, behavioral marks (`random` / `constant`), docstring-derived help | `discovery.py` |
| [Error Handling](https://github.com/Gearlux/confluid/blob/main/docs/errors.md) | The typed exception hierarchy (each also inherits the builtin it replaces) | `error_handling.py` |
| [Scopes](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md) | `!scope:` / `!notscope:` conditional overlays and their activation | `scopes.py` |
| [Introspection](https://github.com/Gearlux/confluid/blob/main/docs/introspection.md) | `cast()` for type checkers, `resolve()` markers, `solidify=False`, dump/reconstruct | `introspection.py` |
| [Threads & Async](https://github.com/Gearlux/confluid/blob/main/docs/concurrency.md) | ContextVar propagation, `active_context`, worker-thread recipes | `concurrency.py` |
| [Performance](https://github.com/Gearlux/confluid/blob/main/docs/performance.md) | The engine-timing baseline: per-phase benchmark over a ~2,500-marker tree, `CONFLUID_BENCH_PROFILE=1` profiling mode | `performance.py` |

## Design Goals & Requirements

### Configuration Engine
- **Dotted-Key Resolution:** Allow flat overrides to target nested attributes (e.g. `model.layers: 10`).
- **Tag-Based IR:** Use standard YAML tags (`!class:Name` deferred / `!class:Name()` eager, `!lazy:Name`, `!ref:path`, `!clone:path`) instead of proprietary symbols like `@`.
- **Object-Based Internal Representation:** Use typed `Reference` and `ClassReference` objects for internal resolution.

### Dependency Injection
- **Automatic Hydration:** Support `@configurable` decorator for automatic class registration and instantiation.
- **Fluid-Solid Protocol:** Implement a two-stage lifecycle where objects are defined ("Fluid") and then materialized ("Solid").
- **Materialize API:** Provide an explicit `materialize()` function to instantiate objects from already-resolved configuration.

### Robustness
- **IR-Aware Merging:** `deep_merge` and `expand_dotted_keys` must traverse into `ClassReference` arguments.
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
# experiment.yaml
n_layers: 10

Trainer:
  lr: 0.0001
  model: "!class:Model(layers=!ref:n_layers)"
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
print(report.summary()) # e.g. "2 applied, 0 failed, 0 unused"
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
