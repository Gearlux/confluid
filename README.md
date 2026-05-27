# Confluid

**Confluid** is a modern, hierarchical configuration and dependency injection framework for Python, built for researchers and engineers who need modularity and 100% reproducibility in their experiment pipelines.

## Key Features
- **Post-Construction Configuration:** Configure existing objects without requiring re-instantiation.
- **Strict Gated Hierarchy:** Prevents deep-traversal into non-configurable third-party objects.
- **Third-Party Registration:** Easily make third-party classes (like PyTorch Optimizers) part of your configurable graph.
- **Smart Reference Resolution:** Uses `!ref:` syntax for cross-config references and `${}` for environment variables.
- **Full Hierarchy Dumping:** Export your runtime state to YAML/JSON and reconstruct it later.
- **Flat-View Ordered Matching:** When a class materializes, the visible context is the document minus the descent path; matching scalars are applied in YAML document order with **last-write-wins** semantics. Explicit kwargs are not privileged — every source (own kwargs, sibling broadcasts, class-name blocks) takes its slot at its document position.

> **Scopes:** Conditional overlays use explicit YAML tags — `!scope:debug`, `!scope:task=classification` (or equivalently `!scope:task(classification)`), and the `!notscope:…` negative twins. Activate via `confluid.load(path, scopes=["debug", "task=classification"])` or, in liquifai-built CLIs, via `--scope debug` / `--scope task=classification` / `--task classification`. See the [Scopes](#scopes) section below.

## Design Goals & Requirements

### Configuration Engine
- **Dotted-Key Resolution:** Allow flat overrides to target nested attributes (e.g. `model.layers: 10`).
- **Tag-Based IR:** Use standard YAML tags (`!class:Name`, `!ref:path`) instead of proprietary symbols like `@`.
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
from confluid import configurable

@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

@configurable
class Trainer:
    def __init__(self, model: Model, lr: float = 0.001):
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

# Apply configuration
config = load_config("experiment.yaml")
configure(trainer, config)

print(trainer.lr) # 0.0001
print(trainer.model.layers) # 10
```

### 4. Dump and Reconstruct
```python
from confluid import dump, load

# Export current state
state_yaml = dump(trainer)

# Recreate exact same hierarchy in a new process
new_trainer = load(state_yaml)
```

## Validation

Every `@configurable` class has its `__init__` wrapped at decoration time to validate kwargs against the auto-generated pydantic schema (`confluid.to_pydantic(cls)`). The validation fires at three points, each with its own strict / warn / off knob:

| Point | When it runs | Policy field | Env var |
|---|---|---|---|
| Constructor | Every direct Python instantiation | `policy.init` | `CONFLUID_VALIDATE_INIT` |
| YAML materialization | `confluid.flow()` / `materialize()` / `load()` instantiating a `!class:` Fluid | `policy.yaml` | `CONFLUID_VALIDATE_YAML` |
| MCP tool entry | Navigaitor subprocess tools, just before spawning marainer | `policy.tool` | `CONFLUID_VALIDATE_TOOL` |

All three default to `"strict"` — pydantic `ValidationError` is raised. `"warn"` logs the error to `confluid.validation` and lets the call proceed. `"off"` skips validation entirely.

```python
from confluid import configurable, get_policy, set_policy

@configurable
class Optimizer:
    def __init__(self, lr: float = 1e-3, weight_decay: float = 0.0) -> None:
        self.lr = lr
        self.weight_decay = weight_decay

# strict (default) — pydantic catches the bad type and raises
Optimizer(lr="not a float")  # → pydantic.ValidationError

# Relax YAML loads but keep direct Python instantiation strict
set_policy(yaml="warn")

# Or via env: CONFLUID_VALIDATE_INIT=warn python train.py
```

**Tightening constraints** lives on the annotation, not the body:

```python
from typing import Annotated
from pydantic import Field

@configurable
class TimmClassifierModel:
    def __init__(
        self,
        num_classes: Annotated[int, Field(ge=1)] = 1000,
        drop_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0,
    ) -> None: ...
```

**Opt out per class** when the constructor is intentionally untyped:

```python
@configurable(validate=False)
class ExperimentalThing:
    def __init__(self, **kwargs): ...  # too dynamic for pydantic
```

**Discovery category** — group classes by taxonomy so navigaitor's `list_configurable_classes(category="loss")` filter can find them:

```python
@configurable(category="loss")
class FocalLoss:
    def __init__(self, gamma: float = 2.0) -> None: ...
```

## Scopes

Conditional config blocks live at an arbitrary key whose value carries a
`!scope:` / `!notscope:` tag. The key is inert — pick a descriptive label
(`if_debug`, `if_classification`, …); on activation the wrapper disappears
and the block's contents are spliced in at that slot. Three activation
forms are supported, all equivalent at the IR level:

```yaml
# Boolean — flips on with `--scope debug`
if_debug: !scope:debug
  log_level: DEBUG

# Keyed — flips on with `--scope task=classification` (or `--task classification`)
if_classification: !scope:task=classification
  model: !class:ClassifierModel

# Equivalent function-call form
also_classification: !scope:task(classification)
  model: !class:ClassifierModel

# Negation. `!notscope:KEY=VAL` is also active when the user passes no
# `--KEY ...` at all (the *unset ⇒ active* convention).
unless_debug: !notscope:debug
  log_level: WARNING
```

Resolve them by passing `scopes=` to `load()`:

```python
from confluid import load
trainer = load("experiment.yaml", scopes=["debug", "task=classification"])
```

Liquifai apps wire `--scope NAME` / `--scope KEY=VAL` and per-dimension
`--KEY VAL` flags automatically — see liquifai's docs.

## Installation
```bash
pip install git+https://github.com/Gearlux/confluid.git@main
```

## License
MIT
