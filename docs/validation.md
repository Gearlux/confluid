# Validation

Every `@configurable` class has its `__init__` wrapped at decoration time to validate kwargs against the auto-generated pydantic schema (`confluid.to_pydantic(cls)`). The validation fires at three points, each with its own strict / warn / off knob:

| Point | When it runs | Policy field | Env var |
|---|---|---|---|
| Constructor | Every direct Python instantiation | `policy.init` | `CONFLUID_VALIDATE_INIT` |
| YAML materialization | `confluid.flow()` / `load()` instantiating a `_target_` marker | `policy.yaml` | `CONFLUID_VALIDATE_YAML` |
| Tool entry | An MCP/agent tool server validating a config payload before dispatching a run | `policy.tool` | `CONFLUID_VALIDATE_TOOL` |

All three default to `"strict"` — pydantic `ValidationError` is raised. `"warn"` logs the error to `confluid.validation` and lets the call proceed; for a document-driven construction the warning names the marker's `file:line:col`, the same location the strict path's `ConstructionError` carries. `"off"` skips validation entirely. `set_policy()` and the env vars refuse any other spelling with `ValidationModeError` — a typo never silently downgrades a knob.

**A string accepted for a number or a bool becomes that value.** The schema accepts the text of a number for an `int`
or `float` parameter, and `"true"` / `"false"` (also `"1"` / `"0"`, `"yes"` / `"no"`, `"on"` / `"off"`) for a `bool`
one; the object then holds the value, never the text — at construction (direct, positional or from a document), on a
`@configurable` function's call, and through `configure()`. It matters most for YAML, which reads `1e-4` and `40.0e6`
as text (a YAML 1.1 float needs a dot AND a signed exponent: `1.0e-4`), and for a quoted `"false"`, which as text is
truthy:

```python
Optimizer(lr="1e-4").lr                       # 0.0001, a float
load("!class:Optimizer {lr: 1e-4}").lr        # 0.0001 — and dump() writes lr: 0.0001
Loader(shuffle="false").shuffle               # False, not the truthy text "false"
```

Only a string that validated into an `int`, `float` or `bool` is replaced. Everything else reaches the constructor
exactly as given: a `str` parameter keeps `"5e6"`, a `Union[float, str]` or `Union[bool, str]` keeps its text, an
`int` passed to a `float` parameter stays an `int`, a value refused under `"warn"` is passed through unchanged, and
under `"off"` nothing is touched.

**A class whose schema mirror cannot be built is never blocked — and never silently unvalidated.** If `to_pydantic(cls)` fails (a C-extension constructor with no Python signature, an annotation that cannot be resolved at runtime), the constructor still runs and confluid logs **once per class**, at WARNING: `X: validation is OFF for this class — no schema mirror can be built (IntrospectionError: …)`. Fix the annotation, add the import, or opt the class out explicitly with `@configurable(validate=False)`.

**Pydantic is an optional dependency** (`pip install 'confluid[pydantic]'`). The core — loading, references, scopes, `flow()`, `configure()` — never needs it. Without the extra, every validation point degrades to `"off"` (a one-time log line records the downgrade) and the schema-export API (`to_pydantic`, `confluid_class_of`) raises `ImportError` naming the extra. Packages that rely on schema export or want validation enforced must depend on `confluid[pydantic]`.

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

The YAML setting applies only to the objects a `load()` builds. A direct
`Optimizer(lr="not a float")` elsewhere — on another thread while that load runs,
or after two loads overlapped — is validated under `policy.init`, and a load never
changes the policy `get_policy()` returns.

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
