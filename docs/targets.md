# Targets & Deferred Initialization

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

A Confluid config is built from six marker forms — reserved keys in ordinary
YAML ([the plain format](plain-format.md)). Each parses into a typed
**Fluid** — a deferred *recipe* — that `load()` resolves into
a live **Solid** object. This two-stage lifecycle is what lets Confluid
broadcast values into a node *before* it is built and inject runtime arguments
*as* it is built.

Each marker has two spellings (both columns below). The TAG form is the
preferred way to *write* a config — one line per node. Tags are parsed only by
confluid's own loader (a private `yaml.SafeLoader` subclass); a plain
`yaml.safe_load` raises on them, which is why the reserved-key form exists: it is
what [`hydraide`](hydraide.md) *emits*, ordinary YAML any tool reads. Both are
first-class input, may be mixed in one file, and produce the SAME markers.

| Tag | Reserved key | Purpose | Produces |
|---|---|---|---|
| `!class:Name` / `!class:Name(k=v)` | `_target_: Name` | The callable to build — built at load | `Target` |
| `!partial:Name` (`!lazy:` is an alias) | `_target_: Name` + `_partial_: true` | Built by nobody until an explicit `flow()` (runtime injection) | `PartialClass` |
| `!ref:path` | `_ref_: path` (or `${ref:path}`) | Late-bound reference to another node (shared instance) | `Reference` |
| `!scope:KEY[=VAL]` / `!notscope:…` | `_scope_: {KEY: VAL}` / `_notscope_: …` | Conditional overlay (see [Scopes](scopes.md)) | `ScopeBlock` |

The tag column is the one you will mostly write; the reserved-key column is
what you will mostly *read* — in a `hydraide` artefact, a `dump()`, or a diff.

## The lifecycle: Fluid → Solid

| State | What it is | Tag types |
|---|---|---|
| **Fluid** (deferred) | A recipe, not yet built. Still receives broadcast kwargs. | `Target`, `PartialClass`, `Reference` |
| **Solid** (live) | The actual Python instance your code uses. | — |

`load(text)` (≡ `load(text, until="objects")`) walks the tree and turns
Fluids Solid — but **not all of them**, by design (see the flow table further
down). `load(text, until="document")` stops at the Fluid layer so you can inspect
or re-merge the IR before anything is constructed; `until="settled"` goes one pass
further (broadcast applied, still markers). See [The Lifecycle](lifecycle.md) →
"Where you can stop".

## Partial construction

There are exactly **two** construction modes, and `!partial:` / `_partial_: true` is
the whole difference. Nothing about the parent, the nesting depth or the document
position changes whether a marker is built.

| Tag | Reserved key | Parses to | After `load()` | Built? |
|---|---|---|---|---|
| `m: !class:Model` | `m: {_target_: Model}` | `Target` | a live `Model` | **Yes** |
| `m: !class:Model(layers=10)` | `m: {_target_: Model, layers: 10}` | `Target` (+ kwargs) | a live `Model(layers=10)` | **Yes** |
| `m: !partial:Model` | `m: {_target_: Model, _partial_: true}` | `PartialClass` | a deferred stub | **No** |

A `PartialClass` is never auto-flowed — only an explicit `flow(marker, **runtime)`
builds it, which is the point: the receiving code supplies an argument that does
not exist at config time (`params=model.parameters()`).

> **Deferral withholds CONSTRUCTION only.** A `PartialClass` is broadcast into and
> configured exactly like a built target — merging keys into its kwargs
> constructs nothing — so a bare `lr: 0.001` still tunes a deferred optimizer.

**The target may be any callable, not just a class.** A `!class:` / `!partial:`
target resolves to any callable — a class OR a plain builder/factory **function**
(e.g. `!partial:torchvision.models.detection.fasterrcnn_resnet50_fpn`,
`!class:timm.create_model`). `flow()` introspects the callable's own signature,
so both its stored kwargs and any runtime-injected kwargs are passed through
(runtime wins) — e.g. `flow(deferred_builder, num_classes=37)` calls
`builder(..., num_classes=37)`. This is what lets a trainer flow a `!partial:`
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

### Writing a marker's arguments

```yaml
model: !class:Model                          # no arguments
model: !class:Model(layers=10,dropout=0.5)   # inline scalars — coerced to their declared types
model: !class:Model                          # a block body — for nested markers and mappings
  layers: !ref:n_layers
  head: !class:LinearHead(units=128)
```

and the same three in the reserved-key form:

```yaml
model: {_target_: Model}
model: {_target_: Model, layers: 10, dropout: 0.5}
model:
  _target_: Model
  layers: ${ref:n_layers}
  head: {_target_: LinearHead, units: 128}
```

Grammar notes (all pinned by the test suite):

- **Inline scalars are coerced.** Each inline `key=value` is run through
  `parse_value`, so `7` → `int`, `0.01` → `float`, `true` → `bool`, `null` → `None`.
  Two YAML-level limits on the inline form: it can't contain **spaces** (YAML ends
  a tag at whitespace — write `(a=1,b=2)`, not `(a=1, b=2)`), and it can't carry a
  **nested marker** or a `${...}` (YAML allows one tag per node, and `{` is not a
  legal tag character). For those, use the block body — or the reserved keys.
- **A marker is a tag or a reserved-key mapping — never a string.** A quoted tag
  (`optimizer: "!class:Adam(lr=0.1)"`) is a *string*, and a string that starts with
  a marker prefix is refused with the two lines that work (the tag unquoted with a
  block body, or `{_target_: Adam, lr: 0.1}`) — it never reaches a constructor as
  literal text. Note `1e-3` is *not* a YAML float — write `1.0e-3` or `0.001`.
- **Inline kwargs and a block body merge.** When both are present the inline
  `(k=v)` kwargs combine with the block; on a key in both, the **block body
  wins** (it's later in document order — last-write-wins). Inline-only keys are
  preserved, not discarded.
- **A target may carry a tag selector.** `!class:FourierOp@group=fft/torch` picks
  between classes that share a registered name (see
  [Discovery](discovery.md#when-two-classes-share-a-name)). It composes with
  everything above — inline kwargs (`!class:X@role=metric(k=3)`), a block body,
  and `!partial:`. A value written `$key` is read from the loaded configuration
  (`!class:Loss@framework=$engine`), including from inside another marker's
  block.

## Deferred initialization: `!partial:` / `_partial_`, declared slots, and `flow()`

Three names, three different things — keep them apart:

| Name | What it is | Where it appears | What it defers |
|---|---|---|---|
| `PartialClass` | the deferred **marker** — a `Target` with `partial=True` | in YAML `!partial:` (`_partial_: true`); in code `PartialClass(Adam, lr=1e-3)` | a **value**: *this recipe* is built only by an explicit `flow(marker, params=…)` |
| `Partial[T]` | the slot **annotation** — `Annotated[Union[T, Fluid], marker]` | on a constructor param or an `__init__`-body attribute: `optimizer: Partial[Optimizer]` | a **slot**: *whatever lands here* — a plain `!class:` too — stays an unbuilt marker until the class flows it. `T` is the type the slot flows into |
| `partial_param_names(cls)` | the **reader** of the declaration | in code that walks a live object and builds its markers | nothing — it answers "which slots of this class are deferred?" so a walker knows which attributes NOT to build |

Why the reader exists: after `load()`, a plain `!class:SGD` written into an
`optimizer: Partial[Optimizer]` slot is kept as a plain `Target` — the engine does
not rewrite the value — so `isinstance(value, PartialClass)` cannot tell that
attribute from one the class meant to be built. Any code that flows a live
object's remaining markers must ask the class, and `partial_param_names` is the
one place that question is answered (it reads the annotation AND the
`PartialClass(...)` body values, through the same slot scan everything else uses).

A deferred node is built later by calling **`flow(node, **runtime_kwargs)`** —
idempotent (live objects pass through unchanged), with runtime kwargs winning
over stored ones. Deferral has exactly **two** sources, and neither of them is
the document's shape:

| Node | Eagerly built? |
|---|---|
| `!class:Foo` / `{_target_: Foo}` anywhere — nested, root-level, under any parent | **Yes**, always |
| `!partial:Foo` / `{_target_: Foo, _partial_: true}` | **No**, anywhere |
| any value in a slot the RECEIVER declared deferred | **No** — see below |

There is no context-dependent build rule: broadcasting is pass 7 and construction
is pass 8, so a built node already receives every cascading key before its
constructor runs — nothing needs to stay unbuilt "so a key can still reach it".

### Repeat flows are cached — one recipe + one argument set = one object

A deferred marker remembers its **last** build. Flowing it again with an
unchanged recipe and the same arguments returns the same object; anything
else — a tuned recipe, a different argument, a different call shape — rebuilds,
and the new build replaces the remembered one (one entry per marker, never a
table):

```python
t = PartialClass(Trainer, max_epochs=3)
d = flow(t, logger=x)
e = flow(t, logger=x)        # same marker, same arguments -> e is d, built ONCE
f = flow(t, logger=y)        # different argument -> fresh build (replaces the entry)
g = flow(t)                  # different call shape -> fresh build
t.kwargs["max_epochs"] = 5   # a tune (what configure() does)
h = flow(t)                  # recipe changed -> fresh build
```

Arguments compare the way "the same" can be *proven*: scalars (`str`, `int`,
`float`, `bool`, `None`) by value, everything else by identity — so passing the
same object again is a hit, while a freshly built list or generator
(`params=model.parameters()`) rebuilds. A marker-valued recipe entry is compared
recursively, so tuning a nested recipe always invalidates. Three exemptions:
a `random=True` class re-executes every time, a marker **copy** never shares a
build (the cache is keyed on the marker's identity), and `solidify=False` builds
are distinct from finalized ones. Sharing across *different* markers keeps its
one spelling, `!ref:`.

**1. The RECEIVER declares the slot deferred.** The contract is static, local to
the class, and readable — instead of the document having to guess. Annotate the
slot `Partial[T]`, or give a body slot a `PartialClass(...)` value; either signal
keeps whatever the config puts there unbuilt.

```python
from confluid import Partial, PartialClass, configurable, flow

@configurable
class Car:
    def __init__(self, engine: Partial[Engine] = None):
        # A declared-deferred slot: a plain `!class:Engine` written in the
        # config stays a marker here, broadcasts and all, instead of being built.
        self.engine = engine if engine is not None else PartialClass(Engine)

    def start(self) -> None:
        self.engine = flow(self.engine)  # built here, with broadcasts applied
```

Without the declaration the slot is built at load, because `!class:` means
build. That is the whole rule.

**2. `!partial:` / `_partial_: true` — "this genuinely cannot be built until runtime."**
Some objects need an argument that does not exist at config time — the textbook
case is an optimizer that needs `params=model.parameters()`. Write `!partial:`
(or `_partial_: true` beside the `_target_`) and the marker is **never**
auto-flowed — not by `load()`, not by an external deep-flow walker:

```yaml
optimizer: !partial:torch.optim.Adam       # or: {_target_: torch.optim.Adam, _partial_: true, lr: 0.01}
  lr: 0.01
```

```python
def configure_optimizers(self):
    # runtime injection: params isn't known until the model exists
    return flow(self.optimizer, params=self.parameters())
```

**Which of the two do I want?** Ask whether the target could be built from config
alone:

- If it **can**, but you want to build it yourself (to sequence side effects, or
  to build it at a particular moment) — declare the SLOT deferred on the
  receiving class (`Partial[T]` / a `PartialClass(...)` body value). The config
  then writes an ordinary `_target_` and stays unaware.
- If it **cannot** without a runtime-injected argument — write `!partial:`
  (`_partial_: true`) in the config, so the deferral is visible to whoever reads
  it and no walker anywhere will build it.

**Python-side `Partial[T]` annotation.** The same deferral pinned on a
*constructor parameter*, so an auto-flow walker leaves even a plain
`Target` default in that slot alone:

```python
from torch.optim import Adam, Optimizer

from confluid import PartialClass, configurable, flow
from confluid.partial import Partial   # Partial[T] == Annotated[Union[T, Fluid], <marker>]

@configurable
class Trainer:
    def __init__(self, optimizer: Partial[Optimizer] = PartialClass(Adam, lr=1e-3)):
        self.optimizer = optimizer           # auto-flow walkers skip this slot
    def configure_optimizers(self):
        return flow(self.optimizer, params=self.parameters())
```

Subscript with the **interface the slot eventually flows into** — the abstract
base (`Partial[Optimizer]`), not the concrete default (`Partial[Adam]`). Because
`Partial[T]` expands to `Union[T, Fluid]`, the annotation is honest to strict type
checkers about *both* states of the slot: pre-flow it holds a deferred `Fluid`
stub (so the `PartialClass(Adam, …)` default type-checks — it *is* a `Fluid`),
and any live `Optimizer` also satisfies it. `Partial[Any]` remains valid when the
target type is genuinely open. To narrow the flowed result for a type-checker,
use `cast(node, Optimizer)` (confluid's typed `flow`). The marker itself is
runtime-only and is discovered via `partial_param_names(cls)`. `Partial[T]` is the
Python-annotation twin of `!partial:` / `_partial_: true`: **the marker defers a
*value*, the annotation defers a *slot*.**

The slot wins, and that is the point: a plain `!class:` written into a
`Partial[T]` slot **stays a marker**, because the receiving class declared that it
supplies the missing runtime argument itself.

```yaml
optimizer: !class:SGD    # the slot is `Partial[Optimizer]`, so this is NOT built at load —
  lr: 0.01               # `flow(self.optimizer, params=model.parameters())` builds it later
```

Writing `!partial:` as well is redundant but harmless. There is still no third
construction mode here: `partial` decides, and a slot declaration is one of the two
places it can be spelled.

**Body slots count too.** A class with many deferred dependencies often takes a
minimal constructor and assigns the rest in the `__init__` body. Annotate those
the same way — `partial_param_names` reports constructor parameters *and* body
slots:

```python
@configurable
class Trainer:
    def __init__(self, model: Partial[Module], batch_size: int = 32):
        self.model = model
        self.optimizer: Partial[Optimizer] = PartialClass(Adam, lr=1e-3)
        self.lightning: Partial[pl.Trainer] = PartialClass(pl.Trainer)

partial_param_names(Trainer)      # {'model', 'optimizer', 'lightning'}
```

A body slot is deferred if *either* signal says so — a `PartialClass(...)` value or
a `Partial[T]` annotation — so an existing class that only used the value form keeps
working. Prefer writing both: the value is what makes the slot survive an
auto-flow walker, and the annotation is what tells a reader and a type-checker.
Annotating such a slot `Any` (a common habit) declares nothing to either.

The body scan reads `__init__` source, so it is empty in a compiled / frozen /
zip-imported deployment — the same packaged-mode caveat as broadcasting, with the
same fix (`confluid-bake`).

### Runtime injection that has no keyword: `flow(node, *args)`

A tag carries keyword arguments only, so a marker does too. Some targets take
their inputs **positionally** — a variadic signature has no keyword for them at
all — and such a slot could not be deferred without a positional channel. So
`flow()` takes runtime *args* as well as runtime *kwargs*:

```python
class Loaders:
    def __init__(self, *loaders, device=None):   # the inputs have no keyword
        self.loaders, self.device = list(loaders), device

@configurable
class Trainer:
    def __init__(self, batch_size: int = 32):
        self.loaders: Partial[Loaders] = PartialClass(Loaders)   # a config can set device=

    def run(self, train, valid):
        loaders = flow(self.loaders, train, valid)         # target(train, valid, **stored)
```

```yaml
loaders: !partial:Loaders                    # YAML sets the knobs; the run supplies the inputs
  device: cuda
```

**What is configuration round-trips; what is a run-time input does not.** The
marker itself — target, `_partial_: true`, `device: cuda` — is configuration: it is
what `dump()` writes and what `load()` gives back, as a `PartialClass` you can
`flow()` again (measured: `dump(trainer)` emits `loaders: {_target_: Loaders,
_partial_: true, device: cuda}`, and after reload `flow(t.loaders, "a", "b")`
builds as before). The values handed to `flow()` at run time — the positional
`train, valid` here, `params=model.parameters()` above — are *inputs of that run*,
not part of the object's description: they are never written onto the marker and
therefore never appear in a `dump()`. The next run supplies them again. One
convenience follows: if the slot holds an already-built object (a config wired a
live instance into it), `flow(slot, train, valid)` returns it and drops the args
rather than raising, so the same call is safe on both shapes.

**A config key naming the variadic is refused.** Since the values can only arrive
positionally, a key of that name can never reach the parameter — so writing one is
an error rather than a no-op:

```yaml
loaders: !class:Loaders
  loaders: [train.bin, valid.bin]    # ConfigurationError, naming the file and line
```

```
Loaders cannot accept 'loaders' at exp.yaml:3:3: it is a *args parameter, which can
never be passed by keyword, so no config key can reach it. Pass the values
positionally — flow(node, a, b) — or give the target a keyword parameter.
```

(Hydra refuses the same spelling and offers `_args_` as its separate channel;
confluid's channel is `flow()`, so the config-side answer matches.)

Only **addressed** keys are refused — one written on the marker, or in a
`ClassName:` block. A **bare** key is an implicit `**.key` that cascades tree-wide
and legitimately matches nothing, so a document whose top-level `loaders:` happens
to collide with some class's variadic parameter keeps loading unchanged. A
`**kwargs` name is never refused either: such a class accepts everything by design.

### Post-flow `solidify()`

If the flowed object has a **`solidify()`** method, `flow()` calls it — after
construction for a marker, and on the pass-through for an object that was
*already* live. That second case is what makes the guarantee usable: an object
built eagerly (`!class:Model()`) or handed in from Python never went through the
marker path, so its lazy state was never finalized, and the failure surfaces far
from its cause (an optimizer flowed with `params=` receiving an empty parameter
list). Because a live object can be flowed more than once, `solidify()` is
expected to **build once and cache**:

```python
@configurable
class Model:
    def __init__(self, width: int = 8):
        self.width, self.backbone = width, None   # ctor does no functional work

    def solidify(self):                            # idempotent: build-once-and-cache
        if self.backbone is None:
            self.backbone = build_backbone(self.width)
        return self.backbone
```

`flow(obj, solidify=False)` suppresses the hook for the whole subtree, live
objects included — see [Introspection](introspection.md).

## `!ref:` — shared instance

`!ref:` points at another node in the same document and yields the **same** live
object wherever it appears:

```yaml
proto: !class:Box(size=3)
a: !ref:proto     # a is the SAME object as proto (and as b)
b: !ref:proto
c: !class:Box(size=3)   # a SECOND marker — a second, independent instance
```

Within one `load()` pass, a marker reached directly or through any number
of `!ref:` resolves to **one** live instance (so `a is b`). A dotted `!ref:` is
decided by its **first segment**: a document key walks *structure* only — dict
keys and list indices (`!ref:cfg.lr`, `!ref:packs[1].name`); anything else is an
import path (`!ref:posixpath.join` — the spelling `dump()` emits for a
function-valued parameter). Reading an **attribute** of a built object
(`!ref:my_split.train`) or calling a method (`!ref:obj.build()`) is refused, with
the line and the rewrite: give the referent's class a selector parameter and
reference the whole object.

```yaml
# refused: !ref:split.train reads the ATTRIBUTE `train` of the object built at `split`
train_set: !class:Stream
  source: &split_recipe !class:DatasetSplit    # instead: the selector on the marker,
    source: !ref:indexable                     # the recipe written once (anchored)…
    val_fraction: 0.2
    seed: 42
    split: train
val_set: !class:Stream
  source: !class:DatasetSplit
    <<: *split_recipe                          # …and merged into the other view.
    split: val                                 # `indexable` is still ONE instance.
```

Independence has exactly one spelling: write the marker again (`c` above) — a
second marker says "a second object" in plain YAML.

A marker used as a **constructor default** (`def __init__(self, engine=Target(Engine))`)
is a *recipe*, not a shared node: Python evaluates the default once, but each host
built in a pass gets its **own** child from it — exactly as the body-slot spelling
(`self.engine = Target(Engine)`) and the same code outside a pass behave. To share
one instance across hosts, use `!ref:`.

**Kwargs on a reference tune the one shared object.** A reference written with a
body — `!ref:proto` followed by `k: 5`, the mapping form `{_ref_: proto, k: 5}`, or
a dotted path that walks through a reference (`a.optimizer.lr: 9.0` where
`a.optimizer` is `!ref:shared`) — folds those kwargs into the *referent's* own
kwargs before broadcasting, exactly as `proto.k: 5` written beside it would. So
they compete at the referent's position like any own kwarg (a bare key written
after `proto` still wins), every alias and the referent itself see them, and of
two references tuning one referent the later one wins per key. A reference to a
plain value cannot carry kwargs — that is a located error.

```yaml
proto: !class:Box {size: 3}
a: !ref:proto
  color: red        # proto, a and b are ONE Box(size=3, color=red)
b: !ref:proto
```

## Runnable example

[`examples/tags_deferred.py`](../examples/tags_deferred.py) exercises every
behaviour above: `!class:` vs `!partial:`, `flow()` runtime injection, and
`!ref:` identity.
