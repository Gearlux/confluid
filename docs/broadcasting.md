# Broadcasting & Ordered Matching

Confluid **broadcasts** bare top-level YAML keys into every configurable node
whose constructor (or `__init__`-body attribute set) accepts a parameter of that
name — the mechanism that lets one flat `batch_size: 64` land on every loader in
the graph without addressing each one. **Addressed keys are exact**: a dotted
`trainer.lr: 0.001` (or the equivalent nested block `trainer: {lr: 0.001}`)
configures the matched node only — it never cascades to the node's
descendants. Cascade is opt-in via glob wildcards.

## Bare, addressed, glob — the scoping model

Every config key is a **path**; the final segment is the parameter name:

| Spelling | Reaches |
|---|---|
| `lr: 0.001` (bare) | every accepting node in the tree — an implicit `**.lr` |
| `trainer.lr: 0.001` | the node named `trainer` only (class name or instance `name`) |
| `trainer: {lr: 0.001}` | identical to the dotted form — one rule, two spellings |
| `trainer.*.lr: 0.001` | trainer's **direct children** only (`*` = exactly one level) |
| `trainer.**.lr: 0.001` | trainer **and** all its descendants (`**` = zero or more levels) |

Details that make the grammar predictable:

* **The first named segment floats** — `trainer.lr` matches a node named
  `trainer` anywhere in the tree (the classic top-level-block reach), i.e.
  `trainer.lr` ≡ `**.trainer.lr`. Segments **after** the first are strict
  one-level hops: `trainer.opt.lr` addresses a *direct* child of trainer
  named `opt`; use `trainer.**.opt.lr` to float `opt` at any depth.
* Segment matching is by **class name or instance `name`**; containers
  (lists, plain grouping dicts) are transparent — one level = one object
  nesting hop.
* All three spellings converge inside blocks too: `trainer: {'**.lr': 1}` ≡
  `trainer: {'**': {lr: 1}}` ≡ `trainer.**.lr: 1`. **YAML quoting caveat:**
  a key starting with `*` must be quoted (`'**.lr':`, `'**':`) because a bare
  `*` opens a YAML alias — the top-level dotted form `trainer.**.lr:` needs
  no quotes.
* A marker's **own kwargs follow the same rule** — they configure that marker
  only. A kwarg set on a wrapper block that the wrapper itself does not
  accept *shields* the wrapper's subtree from an outer `'**'` cascade
  (the value overrides the rider's entry for that subtree).
* Glob-delivered keys (`*`/`**`) are cascade keys: they honour the
  NoBroadcast opt-outs below exactly like bare keys. Exact addressed keys
  bypass the opt-outs, like blocks always did.

## The one rule: document order, last write wins

When a class materializes, the visible context is the document minus the descent
path; matching scalars are applied in **YAML document order** with
**last-write-wins** semantics. Explicit kwargs are not privileged — every source
(own kwargs, bare broadcasts, glob riders, class-name blocks) takes its slot at
its document position. There are **no specificity tiers**: an exact
`trainer.lr` earlier in the document loses to a later `**.lr`, and vice versa.

`configure()` (post-construction configuration of live objects) follows the
same matching rule: `ClassName:` / instance-name blocks unroll at their
position, bare keys broadcast, globs opt back in, and whichever assignment
comes last in the document wins (no priority tiers). A `null` value is applied
(`dropout: null` sets `None`), an unknown key inside a class block logs a
warning instead of failing silently, and property getters are never executed
during configuration.

## Opting out of broadcasting

Broadcasting matches by name alone, which can bite very generic parameter
names. Two opt-outs exist — both block only cascade keys (bare top-level keys
and `*`/`**` glob-delivered keys); addressed `ClassName:` blocks, dotted exact
paths, and `configure()` always keep working:

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
# ... TRACE | confluid.engine:_prepare_kwargs | broadcast: 'lr' -> Transform (glob '**')
# ... TRACE | confluid.engine:_prepare_kwargs | broadcast: 'lr' -> Transform (block 'trainer')
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
landing on two siblings, an exact addressed key stopping at its node, the
`*` / `**` glob forms opting back into the cascade, last-write-wins ordering,
and both opt-outs (`NoBroadcast[str]` and `@configurable(broadcast=False)`).
The bake step needs a real packaging pipeline, so it stays illustrated inline
above.
