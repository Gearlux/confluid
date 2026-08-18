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

| Reserved key | Tag | Purpose | Produces |
|---|---|---|---|
| `_ref_: path` (or `${ref:path}`) | `!ref:path` | Late-bound reference to another node (shared instance) | `Reference` |
| `_target_: Name` | `!class:Name` / `!class:Name(...)` | The callable to build — built at load | `Target` |
| `_target_: Name` + `_partial_: true` | `!partial:Name(...)` (`!lazy:` is an alias) | Built by nobody until an explicit `flow()` (runtime injection) | `Partial` |
| `_scope_: {KEY: VAL}` / `_notscope_: …` | `!scope:KEY[=VAL]` / `!notscope:…` | Conditional overlay (see [Scopes](scopes.md)) | `ScopeBlock` |

The tag column is the one you will mostly write; the reserved-key column is
what you will mostly *read* — in a `hydraide` artefact, a `dump()`, or a diff.

## The lifecycle: Fluid → Solid

| State | What it is | Tag types |
|---|---|---|
| **Fluid** (deferred) | A recipe, not yet built. Still receives broadcast kwargs. | `Target`, `Partial`, `Reference` |
| **Solid** (live) | The actual Python instance your code uses. | — |

`load(text)` (≡ `load(text, until="objects")`) walks the tree and turns
Fluids Solid — but **not all of them**, by design (see the flow table further
down). `load(text, until="document")` stops at the Fluid layer so you can inspect
or re-merge the IR before anything is constructed; `until="settled"` goes one pass
further (broadcast applied, still markers). See [The Lifecycle](lifecycle.md) →
"Where you can stop".

## `_target_` vs `_partial_` — the only thing that defers

There are exactly **two** construction modes, and `_partial_` is the whole
difference. Nothing about the parent, the nesting depth or the document position
changes whether a marker is built.

| YAML | Parses to | After `load()` | Built? |
|---|---|---|---|
| `m: {_target_: Model}` | `Target` | a live `Model` | **Yes** |
| `m: {_target_: Model, layers: 10}` | `Target` (+ kwargs) | a live `Model(layers=10)` | **Yes** |
| `m: {_target_: Model, _partial_: true}` | `Partial` | a deferred stub | **No** |

A `Partial` is never auto-flowed — only an explicit `flow(marker, **runtime)`
builds it, which is the point: the receiving code supplies an argument that does
not exist at config time (`params=model.parameters()`).

> **Deferral withholds CONSTRUCTION only.** A `Partial` is broadcast into and
> configured exactly like a built target — merging keys into its kwargs
> constructs nothing — so a bare `lr: 0.001` still tunes a deferred optimizer.

> **A trailing `()` in the legacy tag form is INERT.** `!class:Model` and
> `!class:Model()` are the same thing and both build. Until 2026-08-11 the parens
> chose between an eager `Instance` and a deferred `Class`; that middle mode was
> deleted (broadcasting is pass 7 and construction is pass 8, so a built node
> already receives every cascading key before its constructor runs) and the two
> marker classes collapsed into one `Target` carrying `partial`.

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

Four grammar notes (all pinned by the test suite):

- **Inline scalars are coerced.** In both the unquoted tag (`!class:Model(layers=7)`)
  and the quoted-string (`"!class:Model(layers=7)"`) forms, each inline
  `key=value` is run through `parse_value`, so `7` → `int`, `0.01` → `float`,
  `true` → `bool`, `null` → `None`. Two YAML-level caveats on the **unquoted**
  form: it can't contain **spaces** (YAML ends a tag at whitespace — write
  `(a=1,b=2)`, not `(a=1, b=2)`), and it can't carry a **nested tag**
  (`!ref:` / another `!class:`), because YAML allows only one tag per node.
- **For a nested reference, spaces, or `${ENV}` — use the reserved keys.** The
  tag form's escape hatch was to QUOTE it (`"!class:Adam(lr=!ref:base_lr)"`), and
  that quoted-string form is a third grammar with sharp edges: it parses only
  `!class:` and `!ref:`, refuses the rest, and is honoured by nothing inside a
  marker's own kwargs. It is refused where it cannot be honoured and is slated for
  deletion (`TASKS.md`, Phase 5e). `_target_` has none
  of these problems, because nesting a mapping in a mapping needs no escape at
  all: `{_target_: Adam, lr: ${ref:base_lr}}`. (A tag block body works too:
  nested tags are valid as block values.) Note `1e-3` is *not* a YAML float —
  write `1.0e-3` or `0.001`.
- **Inline kwargs and a block body merge.** When both are present the inline
  `(k=v)` kwargs combine with the block; on a key in both, the **block body
  wins** (it's later in document order — last-write-wins). Inline-only keys are
  preserved, not discarded.
- **A target may carry a tag selector.** `!class:FourierOp@group=fft/torch` picks
  between classes that share a registered name (see
  [Discovery](discovery.md#when-two-classes-share-a-name)). It composes with
  everything above — inline kwargs (`!class:X@role=metric(k=3)`), a block body,
  and `!lazy:`. A value written `$key` is read from the loaded configuration
  (`!class:Loss@framework=$engine`), including from inside another marker's
  block; `${...}` cannot appear in an unquoted tag at all, because `{` is not a
  legal YAML tag character.

> A legacy, colon-free spelling — `!class Model` / `!class Model(lr=1)` — is
> kept for backward compatibility. It mirrors the same eager/deferred `()` rule
> but supports scalars only, not a block body. Prefer the `!class:` form.

## Deferred initialization: `_partial_`, declared slots, and `flow()`

A deferred node is built later by calling **`flow(node, **runtime_kwargs)`** —
idempotent (live objects pass through unchanged), with runtime kwargs winning
over stored ones. Deferral has exactly **two** sources, and neither of them is
the document's shape:

| Node | Eagerly built? |
|---|---|
| `{_target_: Foo}` anywhere — nested, root-level, under any parent | **Yes**, always |
| `{_target_: Foo, _partial_: true}` | **No**, anywhere |
| any value in a slot the RECEIVER declared deferred | **No** — see below |

> **There is no context-dependent build rule.** Until 2026-08-11 a middle mode
> existed (`Class`) whose construction depended on whether its parent was
> `@configurable`. It was deleted: its stated purpose was "so broadcasting can
> still reach it", which is not a reason — broadcasting is pass 7 and
> construction is pass 8, so a built node already receives every cascading key
> before its constructor runs.

**1. The RECEIVER declares the slot deferred.** This is the replacement for the
old `Class` stub, and it is a better one: the contract is static, local to the
class, and readable — instead of the document having to guess. Annotate the slot
`Partial[T]`, or give a body slot a `PartialClass(...)` value; either signal
keeps whatever the config puts there unbuilt.

```python
from confluid import Partial, PartialClass, configurable, flow

@configurable
class Car:
    def __init__(self, engine: Partial[Engine] = None):
        # A declared-deferred slot: a plain `{_target_: Engine}` written in the
        # config stays a marker here, broadcasts and all, instead of being built.
        self.engine = engine if engine is not None else PartialClass(Engine)

    def start(self) -> None:
        self.engine = flow(self.engine)  # built here, with broadcasts applied
```

Without the declaration the slot is built at load, because `_target_` means
build. That is the whole rule.

**2. `_partial_: true` — "this genuinely cannot be built until runtime."**
Some objects need an argument that does not exist at config time — the textbook
case is an optimizer that needs `params=model.parameters()`. Add `_partial_: true`
beside the `_target_` and the marker is **never** auto-flowed — not by
`load()`, not by an external deep-flow walker:

```yaml
optimizer: !partial:torch.optim.Adam
  lr: 0.01
```

```python
def configure_optimizers(self):
    # runtime injection: params isn't known until the model exists
    return flow(self.optimizer, params=self.parameters())
```

> ⚠️ In the legacy tag spelling, `!lazy:` must be a real (unquoted) YAML tag —
> the "quote the tag" trick does **not** apply to it. A quoted `"!lazy:…"` used
> to become a plain **string**: no marker, no error, no warning, and a deferred
> optimizer reached its constructor as the literal text `!lazy:Adam(lr=0.01)`.
> Since 0.3.0 it raises instead, naming the plain-YAML line to write. Writing
> `_partial_: true` avoids the question — it is ordinary YAML, so there is no
> escape hatch to get wrong.

**Which of the two do I want?** Ask whether the target could be built from config
alone:

- If it **can**, but you want to build it yourself (to sequence side effects, or
  to build it at a particular moment) — declare the SLOT deferred on the
  receiving class (`Partial[T]` / a `PartialClass(...)` body value). The config
  then writes an ordinary `_target_` and stays unaware.
- If it **cannot** without a runtime-injected argument — write
  `_partial_: true` in the config, so the deferral is visible to whoever reads
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
Python-annotation twin of `_partial_: true`: **the key defers a *value*, the
annotation defers a *slot*.**

The slot wins, and that is the point: a plain `_target_:` written into a
`Partial[T]` slot **stays a marker**, because the receiving class declared that it
supplies the missing runtime argument itself.

```yaml
optimizer: !class:SGD            # the slot is `Partial[Optimizer]`, so this is NOT built at load —       # `flow(self.optimizer, params=model.parameters())` builds it later
  lr: 0.01
```

Writing `_partial_: true` as well is redundant but harmless. There is still no third
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

The positional half is **runtime-only**: it is never stored on a marker and never
round-trips through `dump()` — exactly like the `params=` / `dataset=` kwargs
above. Live objects follow the same convention as runtime kwargs: passing args to
an already-built object drops them rather than raising, so a slot flowed
`flow(slot, train, valid)` stays safe when a config wired a live object into it.

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

It used to be absorbed as a post-init attribute instead: the constructor never saw
it, and `obj.loaders` held a value the object ignored. (Hydra refuses the same
spelling and offers `_args_` as its separate channel; confluid's channel is
`flow()`, so the config-side answer matches.)

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

Independence has exactly one spelling: write the marker again (`c` above). The
`!clone:` / `_clone_` / `${clone:}` deep-copy marker was removed in 0.3.0 — it
had no users, and a second marker says the same thing in plain YAML.

## Runnable example

[`examples/tags_deferred.py`](../examples/tags_deferred.py) exercises every
behaviour above: deferred vs eager `!class:`, `!lazy:` + `flow()` runtime
injection, and `!ref:` identity.
