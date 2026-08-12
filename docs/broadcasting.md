# Broadcasting & Ordered Matching

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

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

## What this costs you: order-dependence

The rule above has a consequence worth stating plainly, because it is the one
thing about confluid that surprises people:

> **Moving a line in a config can change the result.** Not only adding or
> removing one — *moving* one. Position is the whole arbitration.

That is the price of the bet this page describes. Confluid lets an unaddressed
key reach every node that accepts it, so a sweep sets `lr` once for a whole tree
with no parameter threading; the cost is that the same `lr` beats a value
written *at* a node whenever it sits lower in the file. Libraries that make
reach-many explicit — writing `Trainer.lr` at every site, or calling a
`select(...)` API — pay the opposite price in verbosity and get order-independence
back. Neither answer is free.

Two habits keep it manageable:

- **Put document-wide defaults at the TOP and overrides below them.** Reading
  top-to-bottom then matches what the engine does, and a tidy-up that reorders
  blocks cannot silently invert a value.
- **When a value surprises you, ask the report** rather than reading the file
  again. [`ConfigurationReport.explain`](report.md) prints the contest in
  document order with the winner marked:

```python
from confluid import collect_report, load

with collect_report() as report:
    cfg = load("experiment.yaml")

print(report.explain("lr"))
```

```
lr on Trainer = 0.9
    #0   block 'Trainer'    0.5          beaten — earlier in the document
  ✓ #2   bare               0.9          applied
```

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
# ... TRACE | confluid.broadcast:apply | broadcast: 'strength' -> Transform (bare)
# ... TRACE | confluid.broadcast:apply | broadcast: 'lr' -> Transform (glob '**')
# ... TRACE | confluid.broadcast:apply | broadcast: 'lr' -> Transform (block 'trainer')
```

For a structured, assertable version of the same information — every applied
key with its receiver and origin, plus failed and unused keys — see
[Configuration Reports](report.md): `configure()` returns a
`ConfigurationReport`, and `collect_report()` collects one across a
`load()` / `materialize()` pass.

## Classes with `**kwargs` constructors

Broadcasting filters cascade keys through each receiver's *accept-list* — the
set of constructor parameters (plus configurable attributes) the class
declares. A constructor with a `**kwargs` catch-all makes that list
**unknowable**, and confluid deliberately errs permissive: the accept-list is
treated as "accepts everything", so **every** bare top-level key (and every
`*`/`**` glob-delivered key) broadcasts into instances of such a class:

```python
@configurable
class Passthrough:
    def __init__(self, **kwargs):   # accept-list unknowable
        self.options = kwargs

# name: "x" / lr: 0.1 / ANY other bare top-level key now lands on Passthrough
```

**Where such a key lands follows the addressing**, the same split the two
predicates below describe — and for a `**kwargs` class the difference is visible,
because the constructor would take either:

| The key | Reaches | Why |
|---|---|---|
| written on the marker (`{_target_: Passthrough, tag: x}`), or in a block naming it | the **constructor** (`kwargs`) | it says what to build this node *with* |
| injected at flow time (`flow(node, model=…)`) | the **constructor** (`kwargs`) | a call argument by construction |
| bare, cascading (`name: "x"` at the top level) | a post-init **attribute** | it was aimed at the whole document, not at this node |

```python
graph = load("""
sink:
  _target_: Passthrough
  tag: addressed
name: run-42
""")
graph["sink"].options   # {"tag": "addressed"}  — addressed at the node
graph["sink"].name      # "run-42"              — cascaded past it
```

The asymmetry is deliberate. A `**kwargs` class has no accept-list to filter
with, so *every* bare key in the document reaches it; passing those to the
constructor would turn permissive broadcasting into "called with whatever the
document happens to contain". A key the author addressed to this node carries no
such ambiguity.

If the cascade soaks up keys you didn't intend, the opt-outs above are the fix:
`@configurable(broadcast=False)` shields the whole class from cascade keys
(addressed blocks still work), or declare the real parameters explicitly so
the accept-list exists. The permissive path announces itself once per class at
TRACE level (`accept-list unknown for <Class> (**kwargs constructor)` — see
the trace-logging snippet above).

**For a class you don't own**, neither of those is available to you — you cannot
edit its signature and you cannot decorate it. `register()` carries the same two
controls for exactly that reason: see
[Discovery](discovery.md#registering-a-class-you-dont-own).

```python
register(SomeLibraryClass, name="Sink", broadcast=False)
```

## Asking whether a key may land

The rules above — the accept-list, plus the two opt-outs — are also available
as predicates, so code that delivers configuration from *outside* a YAML
document (a CLI layer turning `--lr 0.1` into a config change, a form editor,
an RPC surface) can ask confluid instead of re-deriving them:

```python
from confluid import accepts_any_key, accepts_broadcast, accepts_key, configurable, NoBroadcast

@configurable(broadcast=False)
class Pinned:
    def __init__(self, lr: float = 0.1, tag: NoBroadcast[str] = "") -> None:
        self.lr, self.tag = lr, tag

accepts_key(Pinned, "lr")         # True  — `Pinned: {lr: …}` sets it
accepts_broadcast(Pinned, "lr")   # False — a bare `lr:` must not cascade in
accepts_key(Pinned, "typo")       # False — nothing to set
accepts_any_key(Pinned)           # False — it has an accept-list to filter with
```

Which predicate to use follows the addressing, not the source:

| The key… | Predicate | Gated by |
|---|---|---|
| names its receiver (`ClassName:` block, exact dotted path, a marker's own kwargs) | `accepts_key` | the accept-list |
| is bare and cascades | `accepts_broadcast` | the accept-list **and** both opt-outs |

All accept a class, a live instance, or the dotted string a `_target_` marker
carries; an unresolvable target accepts nothing. Re-deriving this yourself is
the mistake they exist to prevent — a hand-rolled accept-list typically misses
`**kwargs` targets, `__init__`-body slots, and both opt-outs, so a class that
declared "no bare key may land on me" quietly accepts one anyway.

### Declaring a key vs being unable to refuse it

The two predicates above answer *"may this key land here?"*. `accepts_any_key`
answers the prior question — *"does this target discriminate between keys at
all?"* — and it exists because for a [`**kwargs` class](#classes-with-kwargs-constructors)
the other two say **yes to every key**, including keys the class has never heard of:

```python
@configurable
class Forwards:
    def __init__(self, **kwargs): ...

accepts_key(Forwards, "run_name")   # True  — it cannot refuse it ...
accepts_any_key(Forwards)           # True  — ... precisely because it has no accept-list
```

That difference decides how an external front-end should **deliver** the key.
Writing it into a marker's own kwargs is the *addressed* channel, so it becomes a
**constructor argument**; a key the target merely cannot refuse was never aimed
there, and must be left to cascade so it lands as a post-init **attribute**
instead. Deliver it as an argument and you call somebody else's constructor with
whatever the document happened to contain — a strict library then rejects a key
it never asked for, from a call site nowhere near the config:

```python
if accepts_any_key(cls):
    ...          # leave it in the document; let broadcasting deliver it BARE
elif accepts_broadcast(cls, key):
    marker.kwargs[key] = value   # the class declares it — an argument is correct
```

A fourth predicate completes the family: `declares_key(target, key)` answers
what the target **names** — constructor parameters, settable properties,
`__init__`-body slots — with the `**kwargs` catchall never counting. It is the
question between `accepts_key` (yes to everything a catchall cannot refuse) and
`accepts_any_key` (whether it discriminates at all): a library that forwards
its catchall somewhere strict rejects undeclared names far from the config, so
a caller sizing such targets checks `declares_key` before passing a dimension.
For a target with no catchall it agrees with `accepts_key` by construction.


## Deferred (`_partial_`) slots are configured, not skipped

A slot that needs a runtime argument is declared deferred, typically in code:

```python
@configurable
class Trainer:
    def __init__(self, model=None) -> None:
        self.model = model
        # cannot be built yet — `params=` only exists once the model does
        self.optimizer: Partial[Optimizer] = PartialClass(AdamW, lr=1e-4, weight_decay=0.05)

    def configure_optimizers(self):
        return flow(self.optimizer, params=self.model.parameters())
```

Being deferred means confluid will not **build** it. It does not mean confluid
will not **configure** it — merging keys into a marker's `kwargs` constructs
nothing. So every spelling reaches it, and the marker stays deferred:

```yaml
lr: 0.5                     # bare — cascades like any other key
'**.lr': 0.5                # a rider SCALAR — cascades to every accepting slot target
'**.optimizer.lr': 0.5      # a rider MAPPING — tunes every declared `optimizer` slot

trainer:
  _target_: Trainer
  optimizer:                # a block TUNES the marker ...
    lr: 0.5                 # ... `weight_decay: 0.05` survives untouched
  optimizer.lr: 0.5         # the dotted form, same effect
```

Two points worth knowing:

- **The cascade gates are identical on both paths.** Whether a key reaches
  a deferred slot during YAML materialization or through post-construction
  `configure()`, and whether it arrives bare or through a `'**'` rider, the
  same gates decide: container values at *undeclared* keys never cascade
  (they are routing), the target's accept-list and the `NoBroadcast` opt-outs
  gate what lands (a rider mapping is gated by the SLOT param's shield, a
  rider scalar by the TARGET param's — each shield guards its own key), and a
  marker value requires a *declared* key (never the `**kwargs` catchall) with
  a different target than the slot's own — so a slot can never be tuned with
  a copy of itself.
- **A block merges, it does not replace.** `optimizer: {lr: 0.5}` keeps every
  kwarg you did not mention. To replace the marker outright — a different
  optimizer class, say — write one:
  `optimizer: {_target_: torch.optim.SGD, _partial_: true, lr: 0.5}`.
  That form starts from scratch, so restate what you need.
- **Position decides, not the spelling.** There is no "addressed beats bare" tier:
  whichever of the two you write *later* in the document wins, exactly as two bare
  keys of the same name would. All four ways of aiming a value at the slot behave
  identically here.

  ```yaml
  lr: 0.9                          # a document-wide default ...
  runnable:
    _target_: Trainer
    optimizer:
      _target_: AdamW
      _partial_: true
      lr: 0.5                      # ... overridden per-slot below it  -> 0.5
  ```

  ```yaml
  runnable:
    _target_: Trainer
    optimizer:
      _target_: AdamW
      _partial_: true
      lr: 0.5                      # a per-slot value ...
  lr: 0.9                          # ... overridden by a later sweep   -> 0.9
  ```

  A value set in *code* — `self.optimizer = PartialClass(AdamW, lr=1e-4)` — has no
  position in the document at all, so it is a default: any `lr:` overrides it,
  exactly as it would override a constructor default. This is also why a CLI
  override always takes effect: it is applied after the whole file.

### When a knob "did not take"

Because the later spec wins, a value you wrote at a node can be replaced by a
key further down the file — which is the feature, but it looks the same as a
setting that never applied. Confluid reports every replaced value at DEBUG:

```
LOGGAIR_CONSOLE_LEVEL=DEBUG python train.py config.yaml
```

```
override: 'lr' 0.5 -> 0.9 (exact value replaced by a bare one;
                           document order decides — the later spec wins)
```

An uncontested value logs nothing, so anything you see here is a real contest.
The fix is usually to move the document-wide key *above* the node you want to
keep. For the complementary question — which keys matched nothing at all — use
[`collect_report()`](report.md).

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

For the same rules at scenario scale — a four-level service tree configured
with zero parameter-threading code — see
[`examples/deep_injection.py`](../examples/deep_injection.py).
