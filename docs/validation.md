# Validation

Every `@configurable` class has its `__init__` wrapped at decoration time to validate kwargs against the auto-generated pydantic schema (`confluid.to_pydantic(cls)`). The validation fires at three points, each with its own strict / warn / off knob:

| Point | When it runs | Policy field | Env var |
|---|---|---|---|
| Constructor | Every direct Python instantiation | `policy.init` | `CONFLUID_VALIDATE_INIT` |
| YAML materialization | `confluid.flow()` / `materialize()` / `load()` instantiating a `!class:` Fluid | `policy.yaml` | `CONFLUID_VALIDATE_YAML` |
| Tool entry | An MCP/agent tool server validating a config payload before dispatching a run | `policy.tool` | `CONFLUID_VALIDATE_TOOL` |

All three default to `"strict"` — pydantic `ValidationError` is raised. `"warn"` logs the error to `confluid.validation` and lets the call proceed. `"off"` skips validation entirely.

**Pydantic is an optional dependency** (`pip install 'confluid[pydantic]'`). The core — loading, references, scopes, `flow()`, `configure()` — never needs it. Without the extra, every validation point degrades to `"off"` (a one-time log line records the downgrade) and the schema-export API (`to_pydantic`, `confluid_class_of`, `lazy_param_names_of`) raises `ImportError` naming the extra. Packages that rely on schema export or want validation enforced must depend on `confluid[pydantic]`.

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

## Runnable example

[`examples/validation.py`](../examples/validation.py) triggers a strict
constructor rejection, relaxes a policy to `"warn"`, enforces an
`Annotated[..., Field(...)]` range, and opts a dynamic class out with
`validate=False`.
