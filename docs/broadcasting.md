# Broadcasting & Ordered Matching

Confluid **broadcasts** bare top-level YAML keys into every configurable node
whose constructor (or `__init__`-body attribute set) accepts a parameter of that
name — the mechanism that lets one flat `batch_size: 64` land on every loader in
the graph without addressing each one.

## The one rule: document order, last write wins

When a class materializes, the visible context is the document minus the descent
path; matching scalars are applied in **YAML document order** with
**last-write-wins** semantics. Explicit kwargs are not privileged — every source
(own kwargs, sibling broadcasts, class-name blocks) takes its slot at its
document position.

`configure()` (post-construction configuration of live objects) follows the
same matching rule: `ClassName:` / instance-name blocks unroll at their
position, bare keys broadcast, and whichever assignment comes last in the
document wins (no priority tiers). A `null` value is applied (`dropout: null`
sets `None`), an unknown key inside a class block logs a warning instead of
failing silently, and property getters are never executed during configuration.

## Opting out of broadcasting

Broadcasting matches by name alone, which can bite very generic parameter
names. Two opt-outs exist — both block only BARE top-level keys; addressed
`ClassName:` blocks and `configure()` always keep working:

```python
from confluid import NoBroadcast, configurable

@configurable
class Transform:
    def __init__(self, name: NoBroadcast[str] = "t", strength: float = 1.0):
        self.name = name          # a top-level ``name:`` key no longer lands here
        self.strength = strength  # still broadcastable

@configurable(broadcast=False)     # class-level: NO bare key ever lands
class Reporter:
    def __init__(self, path: str = "out"): ...
```

To see exactly what broadcast where, enable trace logging:

```bash
LOGGAIR_CONSOLE_LEVEL=TRACE python train.py config/train.yaml
# ... TRACE | confluid.engine:_prepare_kwargs | broadcast: 'strength' -> Transform (bare)
```

## Post-init attrs in compiled/frozen deployments (`confluid-bake` / `broadcast_attrs`)

Broadcasting discovers post-init body attributes (`self.loss_fn = …` inside
`__init__`) by AST-scanning the constructor **source**. In compiled / frozen /
zip deployments `inspect.getsource` fails, the scan is silently empty, and
those slots vanish from the broadcast surface (confluid logs one warning per
class when it can't scan an uncovered `@configurable` class).

**The primary fix is the build-time bake step** — run the same scan while
source still exists and ship the result:

```bash
# In the packaging pipeline, BEFORE freezing/zipping:
confluid-bake mypackage otherpackage        # == python -m confluid.bake ...
# writes mypackage/_confluid_baked.py (provenance-headed, deterministic)

confluid-bake mypackage --check             # CI drift guard: exit 1 if stale
```

At runtime the engine unions three sources — `live scan ∪ declared ∪ baked` —
consulting the baked table per MRO class only when the live scan finds nothing,
so a dev checkout is always governed by fresh source and the baked table
carries the load exactly where source is missing. Every class the package
defines with its own `__init__` is baked (in-package base classes contribute
through the MRO), and an empty entry means "scanned, no body slots" — it
silences the warning.

> **Frozen-bundler note (PyInstaller etc.):** the engine imports
> `<pkg>._confluid_baked` lazily by dotted name, which static import tracers
> don't see — add `--hidden-import mypkg._confluid_baked` or import it
> explicitly from the package's `__init__`. Wheel/zip/pyc-only deployments
> need nothing extra.

The manual override for classes the bake can't reach (or third-party code you
register) is an explicit declaration, likewise unioned with the scan:

```python
@configurable(broadcast_attrs=["loss_fn", "val_metrics"])
class Trainer:
    def __init__(self, model: str = "m"):
        self.model = model
        self.loss_fn = "ce"        # scanned in dev; declared for packaged mode
        self.val_metrics = None
```

An explicit `broadcast_attrs=[]` declares "no post-init broadcast attrs" and
silences the warning.

## Runnable example

[`examples/broadcasting.py`](../examples/broadcasting.py) shows a bare key
landing on two siblings, last-write-wins ordering, and both opt-outs
(`NoBroadcast[str]` and `@configurable(broadcast=False)`). The bake step needs
a real packaging pipeline, so it stays illustrated inline above.
