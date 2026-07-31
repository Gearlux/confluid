# Tags & Deferred Initialization

A Confluid config is built from six YAML tags. Each parses into a typed
**Fluid** — a deferred *recipe* — that `load()` / `materialize()` resolves into
a live **Solid** object. This two-stage lifecycle is what lets Confluid
broadcast values into a node *before* it is built and inject runtime arguments
*as* it is built.

The tags are parsed only by confluid's own loader (a private `yaml.SafeLoader`
subclass) — a plain `yaml.safe_load` elsewhere in your process does **not**
recognize them and will raise on the unknown tag, exactly as it would without
confluid installed. Always go through `confluid.load` / `load_config` to parse
tagged documents.

| Tag | Purpose | Produces |
|---|---|---|
| `!ref:path` | Late-bound reference to another node (shared instance) | `Reference` |
| `!clone:path` | Like `!ref:` but returns a deep copy | `Clone` |
| `!class:Name` / `!class:Name(...)` | Class node — **deferred or eager depending on `()`** (below) | `Class` / `Instance` |
| `!lazy:Name(...)` | Class node that **always** stays deferred (runtime injection) | `Lazy` |
| `!scope:KEY[=VAL]` / `!notscope:…` | Conditional overlay (see [Scopes](scopes.md)) | `ScopeBlock` |

## The lifecycle: Fluid → Solid

| State | What it is | Tag types |
|---|---|---|
| **Fluid** (deferred) | A recipe, not yet built. Still receives broadcast kwargs. | `Class`, `Lazy`, `Reference`, `Clone` |
| **Solid** (live) | The actual Python instance your code uses. | — |

`load(text)` (≡ `load(text, flow=True)`) and `materialize(data)` walk the tree
and turn Fluids Solid — but **not all of them**, by design (see the flow table
further down). `load(text, flow=False)` stops at the Fluid layer so you can
inspect or re-merge the IR before anything is constructed.

## `!class:` — one tag, two behaviours

`!class:` is a single tag, but the **parentheses decide whether it is built
eagerly or left deferred**. `!class:Model` and `!class:Model()` look almost
identical and are *not* the same thing:

| YAML | Parses to | After `load()` | Built? |
|---|---|---|---|
| `m: !class:Model` | `Class` Fluid | a deferred `Class` stub | **No** |
| `m: !class:Model` + indented block | `Class` Fluid (+ block kwargs) | a deferred `Class` carrying those kwargs | **No** |
| `m: !class:Model()` | `Instance` Fluid | a live `Model` | **Yes** |
| `m: !class:Model(layers=10)` | `Instance` Fluid (+ kwargs) | a live `Model(layers=10)` | **Yes** |
| `m: !class:Model()` + indented block | `Instance` Fluid (+ block kwargs) | a live `Model(**block)` | **Yes** |

**Rule of thumb: a trailing `()` means "build it now."** `!class:Model` is a
deferred stub; `!class:Model()` is a live instance. Everything else follows from
that one bit.

**The target may be any callable, not just a class.** A `!class:` / `!lazy:`
target resolves to any callable — a class OR a plain builder/factory **function**
(e.g. `!lazy:torchvision.models.detection.fasterrcnn_resnet50_fpn`,
`!class:timm.create_model`). `flow()` introspects the callable's own signature,
so both its stored kwargs and any runtime-injected kwargs are passed through
(runtime wins) — e.g. `flow(deferred_builder, num_classes=37)` calls
`builder(..., num_classes=37)`. This is what lets a trainer flow a `!lazy:`
model builder with a dataset-derived dimension and no wrapper class. **`to_pydantic`
is callable-aware too** — it generates a config schema from a builder function's
own signature, so a `register`-ed function (e.g. `register(fasterrcnn_resnet50_fpn,
task="detection", role="model")`) surfaces in form-spec / MCP schemas and
visual-editor palettes exactly like a class (un-JSON-schemable param types —
e.g. torchvision's `Weights` enums or a `Callable[...]` arg — degrade to `Any`).

**You can also `@configurable` / `register` a FUNCTION directly**, not just a class:

```python
from confluid import configurable

@configurable
def build_model(num_classes: int = 10, backbone: str = "resnet18"):
    return ...   # a plain builder function is now a first-class configurable target
```

A `@configurable` function has its **call** validated against its signature (the
callable analogue of a class's `__init__` validation) — an unknown kwarg or a
type-invalid value raises the same structured pydantic error, under the same
`ValidationPolicy` (`@configurable(validate=False)` opts out). `register(fn, ...)`
registers an off-the-shelf builder for discovery without wrapping validation, just
as it does for a third-party class.

```yaml
# Deferred — the receiving object gets a Class stub and builds it itself,
# after broadcasting has filled in matching scalars.
model: !class:Model

# Eager — Confluid builds the Model during load(). Inline scalars are coerced
# to their declared types (layers → int 10, not "10").
model: !class:Model(layers=10,dropout=0.5)

# Eager with a block body — the empty () flips it to eager; the indented
# block carries the kwargs. Use this form when a kwarg value is itself a
# tag (!ref:, !class:, …) or a nested mapping.
model: !class:Model()
  layers: !ref:n_layers              # nested tags are fine as block values
  head: !class:LinearHead(units=128)
```

Three grammar notes (all pinned by the test suite):

- **Inline scalars are coerced.** In both the unquoted tag (`!class:Model(layers=7)`)
  and the quoted-string (`"!class:Model(layers=7)"`) forms, each inline
  `key=value` is run through `parse_value`, so `7` → `int`, `0.01` → `float`,
  `true` → `bool`, `null` → `None`. Two YAML-level caveats on the **unquoted**
  form: it can't contain **spaces** (YAML ends a tag at whitespace — write
  `(a=1,b=2)`, not `(a=1, b=2)`), and it can't carry a **nested tag**
  (`!ref:` / another `!class:`), because YAML allows only one tag per node.
- **For a nested `!ref:` / `${ENV}`, or for spaces, quote the tag.** The
  quoted-string form `"!class:Adam(lr=!ref:base_lr)"` is resolved through the
  resolver, which both coerces scalars *and* resolves a nested reference. (A
  block body works too: nested tags are valid as block values.) Note `1e-3`
  is *not* a YAML float — write `1.0e-3` or `0.001`.
- **Inline kwargs and a block body merge.** When both are present the inline
  `(k=v)` kwargs combine with the block; on a key in both, the **block body
  wins** (it's later in document order — last-write-wins). Inline-only keys are
  preserved, not discarded.

> A legacy, colon-free spelling — `!class Model` / `!class Model(lr=1)` — is
> kept for backward compatibility. It mirrors the same eager/deferred `()` rule
> but supports scalars only, not a block body. Prefer the `!class:` form.

## Deferred initialization: `Class` stubs, `!lazy:`, and `flow()`

A deferred node is built later by calling **`flow(node, **runtime_kwargs)`** —
idempotent (live objects pass through unchanged), with runtime kwargs winning
over stored ones. There are two distinct reasons to defer, and a tag for each.

**1. `Class` stub — "broadcast into it, but I'll build it myself."**
A bare `!class:Foo` (or a `Class(Foo)` default in Python) stays a stub so the
*receiving* `@configurable` object can build it on its own terms — typically
after Confluid's broadcasting has merged matching scalars into its kwargs.

```python
from typing import Any
from confluid import Class, configurable, flow

@configurable
class Car:
    def __init__(self, engine: Any = Class(Engine), color: str = "red"):
        self.engine = engine             # stays a Class stub after load()
    def start(self) -> None:
        self.engine = flow(self.engine)  # built here, with broadcasts applied
```

What `materialize()` / `load()` actually build vs. leave deferred:

| Node | Eagerly built? |
|---|---|
| `Instance` (`!class:Foo()`) | **Yes**, always |
| Root-level Fluid (the whole document is `!class:…`) | **Yes** |
| `Class` nested in a **`@configurable`** parent | **No** — the parent receives the stub |
| `Class` nested in a **non-`@configurable`** parent (e.g. `pl.Trainer`) | **Yes** — the third-party ctor won't flow it, so Confluid does |
| `Lazy` (`!lazy:…`) | **No**, anywhere — see below |

**2. `Lazy` — "this genuinely cannot be built until runtime."**
Some objects need an argument that does not exist at config time — the textbook
case is an optimizer that needs `params=model.parameters()`. Declare it with the
**`!lazy:`** tag, which mirrors the `!class:` grammar (`!lazy:Foo`,
`!lazy:Foo(lr=1e-3)`, or `!lazy:Foo` + block) but **always** produces a deferred
`Lazy` — parentheses or not:

```yaml
# Inline kwargs are coerced just like !class: — lr is a float here.
optimizer: !lazy:torch.optim.Adam(lr=0.01)
```

```python
def configure_optimizers(self):
    # runtime injection: params isn't known until the model exists
    return flow(self.optimizer, params=self.parameters())
```

> ⚠️ `!lazy:` must be written as a real (unquoted) YAML tag — the "quote the
> tag" trick does **not** apply (a quoted `"!lazy:…"` stays a plain string,
> never a `Lazy`). Its inline kwargs are coerced and merge with a block body
> exactly as for `!class:`; only the deferral differs.

**`Class` vs `Lazy` — when to reach for which.** Both are deferred, but they
differ in how *external* deep-flow walkers treat them. An auto-flowing caller
(any framework that recursively flows a graph before handing it to your code)
will **eagerly build a bare `Class`** — which crashes if the target needs a runtime
argument. A `Lazy` is *never* auto-flowed by anything; only an explicit
`flow(node, …)` builds it. So:

- Use a **`Class` stub** when the target *can* be built from config alone but
  you want to build it yourself (to apply broadcasts, sequence side effects, …).
- Use **`!lazy:` / `Lazy`** when building the target without a runtime-injected
  argument would fail.

**Python-side `Lazy[T]` annotation.** The same deferral can be pinned on a
*constructor parameter*, so an auto-flow walker leaves even a plain
`Class` / `Instance` default in that slot alone:

```python
from torch.optim import Adam, Optimizer

from confluid import Class, configurable, flow
from confluid.lazy import Lazy   # Lazy[T] == Annotated[Union[T, Fluid], <marker>]

@configurable
class Trainer:
    def __init__(self, optimizer: Lazy[Optimizer] = Class(Adam, lr=1e-3)):
        self.optimizer = optimizer           # auto-flow walkers skip this slot
    def configure_optimizers(self):
        return flow(self.optimizer, params=self.parameters())
```

Subscript with the **interface the slot eventually flows into** — the abstract
base (`Lazy[Optimizer]`), not the concrete default (`Lazy[Adam]`). Because
`Lazy[T]` expands to `Union[T, Fluid]`, the annotation is honest to strict type
checkers about *both* states of the slot: pre-flow it holds a deferred `Fluid`
stub (so the `Class(Adam, …)` default type-checks — a `Class` *is* a `Fluid`),
and any live `Optimizer` also satisfies it. `Lazy[Any]` remains valid when the
target type is genuinely open. To narrow the flowed result for a type-checker,
use `cast(node, Optimizer)` (confluid's typed `flow`). The marker itself is
runtime-only and is discovered via `lazy_param_names(cls)`. `Lazy[T]` is the
Python-annotation twin of the YAML `!lazy:` tag: **the tag defers a *value*,
the annotation defers a *slot*.**

## `!ref:` vs `!clone:` — shared instance vs. deep copy

Both point at another node in the same document; the difference is identity.

```yaml
proto: !class:Box(size=3)
a: !ref:proto     # a is the SAME object as proto (and as b)
b: !ref:proto
c: !clone:proto   # c is a deep copy — independent of proto
```

Within one `materialize()` pass, a marker reached directly or through any number
of `!ref:` resolves to **one** live instance (so `a is b`). `!clone:` opts out
with an explicit `deepcopy`, and may carry extra kwargs to override on the copy
(`!clone:proto` + a block). `!ref:` also resolves dotted attribute / method
paths — `!ref:my_split.train`, `!ref:some_obj.build()` — against that single
materialized instance.

## Runnable example

[`examples/tags_deferred.py`](../examples/tags_deferred.py) exercises every
behaviour above: deferred vs eager `!class:`, `!lazy:` + `flow()` runtime
injection, and `!ref:` / `!clone:` identity.
