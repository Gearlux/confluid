# Closing the Config Surface — `strict_attrs=True`

> Runnable companion: [`examples/strict_attrs.py`](../examples/strict_attrs.py).
> The reporting half of this story is [Configuration Reports](report.md); the
> accept-list it closes over is [Broadcasting & Ordered Matching](broadcasting.md).

Confluid is **permissive by default**: a key addressed at a class that declares
it nowhere — not a constructor parameter, not a settable class attribute, not an
`__init__`-body slot — is still applied as a post-init attribute. That is the
post-construction toggle mechanism working as designed, and it stays the
default. Since the key names nothing, the engine *warns* while applying it and
records `"unknown-attribute"` in the [report](report.md), on the load path and
under `configure()` alike.

`strict_attrs=True` is the opt-in that turns that warning into a refusal, for a
class whose author wants its config surface **closed**:

```python
from confluid import ConfigurationError, configurable, load

@configurable(strict_attrs=True)
class Cache:
    def __init__(self, path: str = "/tmp/cache", size_mb: int = 64) -> None:
        self.path = path
        self.size_mb = size_mb

load("cache: {_target_: Cache, pathh: /x}")
# ConfigurationError: Cache has no attribute 'pathh' at <config>:1:8 and is
# declared strict_attrs=True, so it will not be set as a post-init attribute.
# Cache declares: path, size_mb.
```

"Declared" is the accept-list: constructor parameters (or the callable's own
signature for a registered builder function), public settable class attributes,
and `__init__`-body slots. The refusal is located — the message names the YAML
`file:line:col` that wrote the key.

## What the mark binds — and what it never touches

| Spelling | Unmarked class (default) | `strict_attrs=True` |
|---|---|---|
| Undeclared key on the marker (`{_target_: Cache, pathh: /x}`) | warning + applied as attribute | **raises** `ConfigurationError` |
| Undeclared key in a class block (`Cache: {pathh: /x}`) | warning + ignored | **raises** `ConfigurationError` |
| Undeclared key under `configure()` | warning + ignored | **raises** `ConfigurationError` |
| Declared param / body slot | applied | applied — unchanged |
| **Bare** top-level key matching nothing | ignored (`unused` in the report) | ignored — unchanged |
| A `**kwargs` class carrying the mark | accepts everything | accepts everything — never refused |

The last two rows are load-bearing:

- **Bare keys never reach the gate.** A bare key is an implicit `**.key` that
  cascades tree-wide and legitimately matches nothing, so a sweep document that
  also configures something else keeps loading around a strict class. The
  accept-list drops the key before the gate is consulted.
- **A `**kwargs` target is never refused.** It has no accept-list (it accepts
  everything by design), so nothing is "undeclared" for it — marking one is
  meaningless rather than an error.

The mark binds **both paths** — the load path and `configure()` — because a
mark that meant different things per path would reintroduce the exact asymmetry
the both-paths warning removed. It is inherited by subclasses.

## Classes you do not own

`register()` carries the mark too, for the same reason it carries
`broadcast=False`: a third-party class is exactly the one you cannot close by
editing its declaration.

```python
from confluid import register

register(ThirdPartyCache, strict_attrs=True)
```

Read it back through the one public mark surface: `marks(Cache).strict_attrs`.
